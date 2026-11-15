"""deepagents team: a coordinator plus cause / reports / outcome subagents.

Checked against deepagents 0.7.38: create_deep_agent(model, tools, *, system_prompt, subagents, ...).
Subagent dicts use keys name, description, system_prompt, tools, model.
LLM: Google Gemini through langchain-google-genai (see backend/llm.py).
Falls back to the rule-based planner when there is no GEMINI_API_KEY, on any error, on an answer that cannot be
verified, or after a timeout. The result always says which planner produced it: "mode" and "fallback_reason".
"""
from __future__ import annotations

import json
from pathlib import Path
import os
import re
import threading
import time

from .. import llm as LLM
from ..textguard import guard
from .mock_planner import run_mock
from .brief import brief_text, build_brief
from .mock_planner import AGENT_NAMES, DISPATCH, send_alerts
from .tools import JobLog, _grid_ids, emit, findings_path, make_tools, verify_output


def _timeout_s() -> float:
    """Read per run, so AGENT_TIMEOUT_S can be changed without re-importing the module."""
    return float(os.environ.get("AGENT_TIMEOUT_S", "75"))


SUBAGENTS = {
    "heat_agent": ("Conductor heating: temperature vs the 75 C limit, thermal sag, heat-driven loss of tree clearance.", ["get_grid_summary", "get_weather"]),
    "wind_agent": ("Wind gust blowout: how far conductors swing toward trees, wind-caused clearance violations.", ["get_grid_summary", "get_weather"]),
    "atmosphere_agent": ("Elevation and air density: thin ridge air cools conductors less, so ridge lines run hotter.", ["get_grid_summary", "get_weather"]),
    "water_agent": ("Flood level vs substation pad heights; which substations are wet or close to it.", ["get_grid_summary", "get_weather"]),
    "reports_agent": ("Ground reports from residents/911: which are corroborated by physics and which assets they raise risk on.", ["get_reports", "get_grid_summary"]),
    "brownout_agent": ("Overloads, unserved load, and whether the hospital/water plant keep power.", ["get_grid_summary", "get_critical_sites"]),
    "fire_agent": ("Ignition risk: fire index, low clearance, red-flag weather, corroborated sparking/smoke reports.", ["get_grid_summary", "get_reports"]),
    "cascade_agent": ("N-1 security: which single line trip would overload others or island load.", ["get_grid_summary"]),
    "flood_agent": ("Energized equipment in floodwater; exposure of the hospital and water plant.", ["get_grid_summary", "get_critical_sites"]),
}

LA_SUB_PROMPT = """You are {name} on a power-grid storm co-pilot team. Your focus: {focus}
Call each of your tools at most once. Then call write_file twice:
1. file_path "/findings/{name}.json", content this compact JSON:
{{"agent":"{name}","findings":[{{"target":"L220","issue":"short","evidence":"words from tool output","severity":1}}]}}
severity: 1 watch, 2 act soon, 3 act now. At most 4 findings. "target" is one line or substation id from tool output.
In "issue" and "evidence" copy the identifiers and words from tool output; do not write numbers.
2. file_path "/findings/{name}.md", content two to four short plain lines saying the same in words.
Then reply with exactly: wrote /findings/{name}.json"""

SUB_PROMPT = """You are {name} on a power-grid storm co-pilot team. Your focus: {focus}
Call each of your tools at most once, then reply with ONLY compact JSON, no prose:
{{"agent":"{name}","findings":[{{"target":"R3","issue":"short","evidence":"words from tool output","severity":1}}]}}
severity: 1 watch, 2 act soon, 3 act now. At most 4 findings. "target" is one line or substation id from tool output.
In "issue" and "evidence" copy the identifiers and words from tool output; do not write numbers."""

COORDINATOR_PROMPT = """You coordinate an AI co-pilot for a power-grid operator during a storm. You never act on the grid: you propose
plans and the human operator approves or rejects them. Work fast; the whole run has a 45 second budget.

Workflow:
1. In ONE message call the `task` tool 5 times in parallel: heat_agent, wind_agent, atmosphere_agent, water_agent, reports_agent.
   Description: "Assess current conditions and return your findings JSON."
2. In ONE message call `task` 4 times in parallel: brownout_agent, fire_agent, cascade_agent, flood_agent.
   Put a 1-3 line digest of the cause + report findings in each description.
3. Propose up to 3 candidate plans of at most 3 ordered actions each. Action vocabulary:
   {"type":"open_line","target":"R3"} {"type":"close_line","target":"V8"} {"type":"shed_load","target":"B7","mw":20}
   {"type":"dispatch_gen","target":"G2","mw":60} {"type":"deenergize_bus","target":"B9"}
   Grid: lines R1-R4 ridge, V1-V8 valley (V8 is a normally-open tie), C1-C5 coast. Buses B1-B12; B10 = Mercy Hospital,
   B6 = water plant (never shed those). G2 = Ridge Hydro, max 60 MW. Lines the operator opened by hand may be re-closed.
4. Call simulate_plan for every plan (parallel calls are fine). It is the ONLY way to judge a plan. If is_safe is false,
   read the exact violations and revise, at most 3 attempts per plan.
5. Reply with ONLY this JSON (no prose, no code fences):
{"situation":"2 plain-language sentences",
 "plans":[{"id":"P1","title":"short","steps":[{"action":{...},"why":"plain language","raised_by":["fire_agent","wind_agent"]}],
   "tradeoffs":"...","assumptions":["..."]}],
 "disagreements":["fire_agent wanted X; brownout_agent objected because Y; resolved by Z"]}
If the grid is already safe with no action, return "plans": []. Outside "action", write no numbers: name lines and
substations by id and say the rest in words. Every plan is re-simulated and the backend adds the verified numbers."""


def _text_of(msg) -> str:
    c = getattr(msg, "content", msg)
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c)


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(json)?", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("coordinator returned no JSON")
    return json.loads(text[a : b + 1])


_ID_LIKE = re.compile(r"\b[A-Z]{1,3}\d+\b")  # line and substation ids such as L220 or B506


def _words(text, names=None) -> tuple[str, bool]:
    """Model text for the activity log -> (text, withheld). Grid ids are names, not numbers; without the grid's own
    list, any id-shaped word counts as one."""
    text = "" if text is None else str(text).strip()
    return guard(text, set(_ID_LIKE.findall(text)) if names is None else names)


def _task_text(output) -> str:
    """The specialist's reply: deepagents' `task` tool returns a Command wrapping its last message."""
    update = getattr(output, "update", None)
    if isinstance(update, dict) and update.get("messages"):
        return _text_of(update["messages"][-1])
    return _text_of(getattr(output, "content", output))


_ROUTE_LINE = re.compile(r"^\W*(\w+_agent)\W+(CALL|SKIP)\b\W*(.*)$", re.I)


def _file_text(data) -> str:
    """deepagents keeps a file as text or as {"content": text or lines}; either way, its text."""
    c = data.get("content") if isinstance(data, dict) else data
    return "\n".join(c) if isinstance(c, list) else "" if c is None else str(c)


def _make_callback(log, names=None):
    """Turns the team's tool calls into the log's structured events (the live agent graph is drawn from these alone)
    and mirrors every file the agents write into the run's shared files."""
    from langchain_core.callbacks import BaseCallbackHandler

    def ev(agent, message, kind, /, **meta):  # events are for a log that keeps them; a plain line log gets none of these
        if getattr(log, "structured", False):
            log(agent, message, kind, **meta)

    class TaskLogger(BaseCallbackHandler):
        def __init__(self):
            self.runs = {}
            self.spawned: list[str] = []
            self.routed: set[str] = set()

        @staticmethod
        def _args(input_str, inputs) -> dict:
            data = inputs or {}
            if not data:
                try:
                    data = json.loads(input_str)
                except Exception:  # noqa: BLE001
                    data = {}
            return data if isinstance(data, dict) else {}

        def _route(self, text: str):
            for line in text.splitlines():
                m = _ROUTE_LINE.match(line.strip())
                if m and m.group(1) in AGENT_NAMES and m.group(1) not in self.routed:
                    agent, called = m.group(1), m.group(2).upper() == "CALL"
                    why, held = _words(m.group(3), names)
                    why = "" if held else why[:120]
                    self.routed.add(agent)
                    ev("coordinator", f"{'calling' if called else 'not calling'} {AGENT_NAMES[agent]}" + (f": {why}" if why else ""),
                         "route", agent=agent, called=called, reason=why)

        def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kw):
            name, data = (serialized or {}).get("name"), self._args(input_str, inputs)
            if name == "write_file":
                path, text = str(data.get("file_path") or ""), _file_text(data.get("content"))
                m = re.match(r"^/findings/(\w+)\.\w+$", path)
                writer = m.group(1) if m and m.group(1) in AGENT_NAMES else "coordinator"
                if hasattr(log, "write"):
                    log.write(writer, path, text)
                if path == DISPATCH:
                    self._route(text)
                return
            if name == "read_file":
                if hasattr(log, "read"):
                    log.read("coordinator", str(data.get("file_path") or ""))
                return
            if name == "ls":
                ev("coordinator", f"ls {data.get('path') or '/'}", "tool", agent="coordinator", tool="ls")
                return
            if name != "task":
                return
            sub = data.get("subagent_type", "subagent")
            self.runs[run_id] = sub
            if sub in AGENT_NAMES and sub not in self.spawned:
                self.spawned.append(sub)
                ev(sub, "started", "spawn", agent=sub)
            desc, _ = _words(data.get("description"), names)
            log("coordinator", f"-> {sub}: {desc[:110]}" if desc else f"-> {sub}")

        def on_tool_end(self, output, *, run_id=None, **kw):
            sub = self.runs.pop(run_id, None)
            if not sub:
                return
            wrote = findings_path(sub) in (getattr(log, "vfs", None) or {})
            try:  # its findings: the file it wrote, or (an agent that answered inline) its reply
                data = json.loads(log.vfs[findings_path(sub)]) if wrote else _parse_json(_task_text(output))
            except Exception:  # noqa: BLE001  never echo unparsed model text: it can state numbers
                log(sub, "returned no readable findings")
                ev(sub, "finished without a findings file" if not wrote else f"wrote {findings_path(sub)}", "agent_done", agent=sub, wrote=wrote)
                return
            f = data.get("findings") if isinstance(data, dict) else None
            f = [item for item in f if isinstance(item, dict)] if isinstance(f, list) else []
            for item in f[:2]:
                target, tw = _words(item.get("target"), names)
                issue, iw = _words(item.get("issue"), names)
                evidence, _ = _words(item.get("evidence"), names)
                if tw or iw:
                    log(sub, "raised a finding (wording withheld: it stated numbers)")
                    continue
                line = f"{target}: {issue}" if target else issue
                log(sub, f"{line} ({evidence})" if evidence else line)
            if not f:
                log(sub, "no significant findings")
            ev(sub, f"wrote {findings_path(sub)}" if wrote else "finished without a findings file", "agent_done",
                 agent=sub, wrote=wrote, findings=len(f))

        def finish(self):
            """After the run: an agent the orchestrator never mentioned in its dispatch file still gets its line."""
            for agent in AGENT_NAMES:
                if agent not in self.routed:
                    called = agent in self.spawned
                    ev("coordinator", f"{'called' if called else 'did not call'} {AGENT_NAMES[agent]}", "route",
                         agent=agent, called=called, reason="" if called else "the orchestrator did not call it")

    return TaskLogger()


LA_SUBAGENTS = {
    "fire_agent": ("Fire risk on the real LA grid: lines over the calibrated fire alarm (the fire index is a 0-1 index that "
                   "blends gust, humidity, tree clearance and fuel; it is not an ignition probability), which ones are "
                   "sub-transmission (shut off) vs 220-500 kV corridors (patrol). For the worst 2-3 lines, call "
                   "get_site_context(line_id) to say what is on the ground and who is downwind.",
                   ["get_grid_summary", "get_site_context"]),
    "wind_agent": ("Storm damage: calibrated wind+rain damage model (which lines are likely to be knocked down, storm index vs "
                   "alarm, historical accuracy). Recommends crew pre-staging and patrols. For the lines you name, call "
                   "get_site_context(line_id): road access and vegetation at each photo decide where patrols can actually reach.",
                   ["get_storm_damage", "get_grid_summary", "get_site_context"]),
    "heat_agent": ("Heat and load: demand, overloaded lines, conductor temperature vs limit.", ["get_grid_summary", "get_weather"]),
    "brownout_agent": ("Who loses power if lines are switched off: unserved MW, hospitals and water plants on backup. "
                       "Weigh that against what a spark would reach: get_site_context(line_id) shows homes and people "
                       "downwind of the line's riskiest stretches.",
                       ["get_grid_summary", "get_critical_sites", "get_site_context"]),
    "buildings_agent": ("Which buildings lose grid power if a line is switched off, ranked on the priority ladder (tier 1 life "
                        "safety: hospitals, emergency services; tier 2 lifelines: water; tier 3 cannot ride through an outage; "
                        "tier 4 homes and industry; tier 5 offices and shops). Call get_building_impact with the flagged line "
                        "ids and say which lines can be cut at no cost to tiers 1-3 and which reach a hospital.",
                        ["get_building_impact", "get_critical_sites"]),
}

LA_COORDINATOR_PROMPT = """You coordinate an AI co-pilot for a power-grid operator on the REAL Greater Los Angeles transmission grid
(OSM lines of 66 kV and above, not distribution feeders; line and substation ids come from the tools; electrical values
are modeled). You never act on the grid: you propose plans and the human operator approves or rejects them. The whole run
has a 45 second budget, so be brief.

You and the specialists share a virtual file system (write_file, read_file, ls). Context moves through those files,
not through long replies.

Workflow:
0. ROUTE. From the situation brief below, decide which specialists this hour needs: fire_agent (a line near or over
   the fire alarm, or a Red Flag warning in force), wind_agent (strong gusts or a storm-damage index near its alarm),
   heat_agent (a line heavily loaded or overloaded, or a heat event), brownout_agent (a plan is likely to cut power to
   someone), buildings_agent (lines are likely to be switched off). Call write_file once with file_path
   "/plan/dispatch.md" and one line per specialist, all five, exactly like:
   fire_agent: CALL - reason in words
   heat_agent: SKIP - reason in words
1. In ONE message call the `task` tool once for each specialist you marked CALL, in parallel, and no others.
   Description: "Assess current conditions and write your findings files."
   Each one writes /findings/<agent>.json and /findings/<agent>.md and replies only "wrote /findings/<agent>.json".
2. Call ls with path "/findings", then read_file for each .json file there. Then call get_candidate_actions() once.
   Build up to 3 plans of at most 3 actions each, using ONLY actions from that list (do not invent line or bus ids).
   deenergize_lines shuts a group of lines off as ONE action (PSPS style). In every step, "raised_by" lists the
   findings file(s) the step came from, for example ["/findings/fire_agent.json"]; cite only files that ls showed.
3. Call simulate_plan for every plan (parallel calls are fine). It is the ONLY way to judge a plan. If is_safe is false,
   read the violations and revise, at most 2 attempts per plan.
4. For the lines your plans switch off or leave energized, the specialists may have quoted get_site_context; you may
   call it too. Put what is on the ground (homes on the brush edge, poor road access) in "why" and "tradeoffs". It is
   Gemini's reading of aerial photos: context, never a reason to change a number.
5. Say how far to trust the signal only from the "trust" rows of the situation brief below (name the check and its
   status); there is no other history to consult. Fire risk drives de-energization; the wind_agent's storm index drives
   crew pre-staging and patrols (mention those in "tradeoffs", they are not grid actions).
6. Reply with ONLY this JSON (no prose, no code fences):
{"situation":"2 plain-language sentences",
 "plans":[{"id":"P1","title":"short","steps":[{"action":{...},"why":"plain language","raised_by":["fire_agent"]}],
   "tradeoffs":"...","assumptions":["..."]}],
 "disagreements":["fire_agent wanted X; brownout_agent objected because Y; resolved by Z"]}
If the grid is already safe with no action, return "plans": []. Outside "action", write no numbers: name lines and
substations by id and say the rest in words. Every plan is re-simulated and the backend adds the verified numbers.
The specialists started from the same situation brief you have below; their findings add detail to it."""

BRIEF_HEADER = ("Situation brief (shared by all agents; start from it instead of re-deriving it; reason from its numbers "
                "but do not repeat them in your reply):\n")


def _subagent_specs(state, log, table, extra: str = "", prompt: str | None = None):
    return [{"name": n, "description": f, "system_prompt": (prompt or SUB_PROMPT).format(name=n, focus=f) + extra,
             "tools": make_tools(state, log, n, t)}
            for n, (f, t) in table.items()]


def _la_prompts(state, log):
    """LA path: build the brief once; return (subagent specs, coordinator prompt), both carrying the brief."""
    brief = build_brief(state)
    log("coordinator", "situation brief: " + brief["headlines"][0])
    block = "\n\n" + BRIEF_HEADER + brief_text(brief)
    subagents = _subagent_specs(state, log, LA_SUBAGENTS, block, LA_SUB_PROMPT)
    prompt = LA_COORDINATOR_PROMPT + f"\nThis run is event {state.eid} at {state.scenario}." + block
    try:  # how fast things are moving (the risk watch's rates of change, past hours only): weigh a rising check higher
        from ..la import watch

        trend = watch.trend_line(state.eid, state.t_utc.isoformat())
        if trend:
            log("coordinator", trend[0].lower() + trend[1:])
            prompt += "\n\n" + trend + " Treat a check that is rising fast as more urgent than its value alone."
    except Exception:  # noqa: BLE001
        pass
    from ..la import feedback as F

    said = F.as_text()  # what the operator said when rejecting earlier plans: the coordinator's memory across runs
    if said:
        log("coordinator", "carrying operator feedback from earlier rejections")
        prompt += ("\n\nOperator feedback from earlier rejections. Prefer plans that answer it, and say in \"tradeoffs\" how a "
                   "plan does. It never overrides the physics check:\n" + said)
    return subagents, prompt


def _run_llm(state, log) -> dict:
    from deepagents import create_deep_agent

    model_name = LLM.model_name()
    model = LLM.chat_model(timeout=40, max_retries=1, max_tokens=4096)
    la = hasattr(state, "grid")
    if la:  # LA: only agents that have real data to read, one parallel wave (audit: 5 of 9 were no-ops on LA)
        subagents, prompt = _la_prompts(state, log)
        # no get_event_context / get_validation_summary: they return the event's real fires and outcomes (hindsight)
        coord_tools = ["get_candidate_actions", "simulate_plan", "get_critical_sites", "get_site_context", "get_building_impact",
                       "get_operator_feedback"]
    else:
        subagents = _subagent_specs(state, log, SUBAGENTS)
        prompt = COORDINATOR_PROMPT
        coord_tools = ["get_grid_summary", "get_critical_sites", "simulate_plan"]
    tools = make_tools(state, log, "coordinator", coord_tools)
    if la and getattr(state, "alerts_on", False):  # Operate runs only: the orchestrator may text the on-call operator
        from .. import notify

        tools = tools + [notify.alert_tool(state, log)]
        prompt += ("\n\nYou also have send_alert(message). Use it ONLY when a specialist's findings file reports a line at very "
                   "high fire risk or very likely to be knocked down by the storm, at most once per line.")
    agent = create_deep_agent(model=model, tools=tools, system_prompt=prompt, subagents=subagents)
    log("coordinator", f"model {model_name}: the orchestrator decides which of {len(subagents)} specialist agents to call")
    watch = _make_callback(log, _grid_ids(state))
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Scenario: {state.scenario}. Assess the grid and propose plans."}]},
        config={"callbacks": [watch], "recursion_limit": 150},
    )
    if la:
        watch.finish()
        vfs = getattr(log, "vfs", None)
        if vfs is not None:  # anything in the team's file system the callback did not see being written
            for path, data in (result.get("files") or {}).items():
                vfs.setdefault(path, _file_text(data))
    return _parse_json(_text_of(result["messages"][-1]))


# Set when Google refuses a call because the key's quota is used up. Until it resets, asking again only costs the
# operator the wait before the same refusal, so the rule-based planner answers straight away.
_PAUSE = {"until": 0.0, "why": ""}
# The pause is kept in a small file too, so a restarted server does not spend its first run waiting on the same refusal.
_PAUSE_FILE = Path(__file__).resolve().parents[2] / "data" / "processed" / "ai_team_pause.json"


def _load_pause() -> None:
    try:
        saved = json.loads(_PAUSE_FILE.read_text())
        if float(saved["until"]) > time.time():
            _PAUSE.update(until=float(saved["until"]), why=str(saved["why"]))
    except Exception:  # noqa: BLE001  no file, or an unreadable one: no pause
        pass


def _save_pause() -> None:
    try:
        _PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PAUSE_FILE.write_text(json.dumps({"until": _PAUSE["until"], "why": _PAUSE["why"]}))
    except Exception:  # noqa: BLE001  a pause that cannot be saved still holds until the next restart
        pass


_load_pause()


def _about(seconds: float) -> str:
    n, unit = (round(seconds / 3600), "hour") if seconds >= 5400 else (max(1, round(seconds / 60)), "minute")
    return f"{n} {unit}{'' if n == 1 else 's'}"


def quota_refusal(err) -> tuple[str, float] | None:
    """(plain reason, seconds until it is worth asking again) when a model error is Google's quota limit, else None."""
    text = str(err)
    if "RESOURCE_EXHAUSTED" not in text and "429" not in text:
        return None
    m = re.search(r"retryDelay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", text)
    wait = float(m.group(1)) if m else 60.0
    wait = min(max(wait, 30.0), 6 * 3600.0)
    daily = "per day" in text.lower() or "PerDay" in text
    return ("Google's free daily limit for this key is used up" if daily else "Google is limiting how fast this key may ask"), wait


def run_team(state, log: JobLog) -> dict:
    """Entry point used by the API. Always returns physics-verified output.
    The rule-based planner runs IN PARALLEL with the LLM team (silently), so a timeout or error falls back instantly."""
    if not LLM.enabled():
        out = run_mock(state, log, "mock mode: no GEMINI_API_KEY")
        out["fallback_reason"] = None  # no key is a setting, not a failure
        return out
    if os.environ.get("AGENT_PLANNER", "auto").strip().lower() in ("rules", "rule-based", "mock"):
        out = run_mock(state, log, "the rule-based planner is selected (AGENT_PLANNER=rules)")
        out["fallback_reason"] = None  # a setting, not a failure
        return out
    left = _PAUSE["until"] - time.time()
    if left > 0:
        why = f"{_PAUSE['why']}; the AI team can be asked again in about {_about(left)}"
        out = run_mock(state, log, why)
        out["fallback_reason"] = why
        return out

    gate = {"open": True}
    first = len(getattr(log, "entries", ()))  # where this run's own lines start

    class Gated:  # once we fall back, late LLM lines, events and files are dropped
        vfs = getattr(log, "vfs", None)
        structured = getattr(log, "structured", False)

        def __call__(self, agent, message, kind=None, /, **meta):
            if gate["open"]:
                emit(log, agent, message, kind, **meta)

        def write(self, agent, path, content):
            if gate["open"] and hasattr(log, "write"):
                log.write(agent, path, content)

        def read(self, reader, path):
            if gate["open"] and hasattr(log, "read"):
                return log.read(reader, path)

    gated = Gated()
    box: dict = {}
    backup: dict = {"log": JobLog()}

    def worker():
        try:
            box["out"] = _run_llm(state, gated)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    def backup_worker():
        try:
            backup["out"] = run_mock(state, backup["log"], "parallel backup", pace=0, alerts=False)
        except Exception as e:  # noqa: BLE001
            backup["err"] = e

    th = threading.Thread(target=worker, daemon=True)
    bk = threading.Thread(target=backup_worker, daemon=True)
    th.start()
    bk.start()
    def fall_back(why: str) -> dict:
        gate["open"] = False
        log("system", why + "; using the rule-based plan computed in parallel")
        bk.join(30)
        if "out" in backup:
            if hasattr(log, "entries"):
                for e in log.entries[first:]:  # the AI team's unfinished attempt stays readable but is not drawn in the graph
                    e.pop("kind", None), e.pop("meta", None)
                log.vfs.clear()
                log.vfs.update(backup["log"].vfs)
                now = time.time()
                # the backup ran in an instant: space its lines out so the replay shows them one after another
                log.entries.extend({**e, "t": round(now + 0.12 * k, 3)} for k, e in enumerate(backup["log"].entries))
            else:
                for e in backup["log"].entries:
                    log(e["agent"], e["message"])
            out = backup["out"]
            send_alerts(state, log)
        else:
            out = run_mock(state, log, why, pace=0.05)
        out["fallback_reason"] = why
        return out

    timeout = _timeout_s()
    th.join(timeout)
    if th.is_alive() or "err" in box or not isinstance(box.get("out"), dict):
        refused = quota_refusal(box["err"]) if "err" in box and not th.is_alive() else None
        if refused:
            _PAUSE["why"], _PAUSE["until"] = refused[0], time.time() + refused[1]
            _save_pause()
            return fall_back(f"{refused[0]}; the AI team can be asked again in about {_about(refused[1])}")
        return fall_back(f"agent run timed out after {timeout:.0f}s" if th.is_alive()
                         else f"agent run failed: {str(box.get('err'))[:120]}" if "err" in box
                         else "the AI team's answer could not be used: it was not a JSON object")

    out = box["out"]
    out["mode"] = "llm"
    log("coordinator", "LLM plans received; backend re-running physics on each plan")
    try:
        out = verify_output(state, out, log)
    except Exception as e:  # noqa: BLE001
        return fall_back(f"the AI team's answer could not be used: {str(e)[:100]}")
    if not out["plans"] and out["do_nothing"]["violations"]:
        return fall_back("the AI team's answer could not be used: no plan it proposed could be run on this grid")
    out["fallback_reason"] = None
    out["vfs"] = dict(getattr(log, "vfs", None) or {})
    emit(log, "coordinator", f"done: {len(out['plans'])} plan(s) ready for operator review", "plans",
         plans=[{"id": q["id"], "safe": bool(q["result"]["is_safe"])} for q in out["plans"]])
    try:  # crew staging numbers always come from the calibrated storm model, never from the LLM
        from .mock_planner import _Planner

        out["crew_staging"] = _Planner(state, lambda *a, **k: None, 0).storm()[1]
    except Exception:  # noqa: BLE001
        pass
    return out
