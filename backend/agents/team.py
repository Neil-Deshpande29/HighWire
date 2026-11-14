"""deepagents team: a coordinator plus cause / reports / outcome subagents.

Checked against deepagents 0.7.38: create_deep_agent(model, tools, *, system_prompt, subagents, ...).
Subagent dicts use keys name, description, system_prompt, tools, model.
LLM: Google Gemini through langchain-google-genai (see backend/llm.py).
Falls back to the rule-based planner when there is no GEMINI_API_KEY, on any error, or after a timeout.
"""
from __future__ import annotations

import json
import os
import re
import threading

from .. import llm as LLM
from .mock_planner import run_mock
from .brief import brief_text, build_brief
from .tools import JobLog, make_tools, verify_output

TIMEOUT_S = float(os.environ.get("AGENT_TIMEOUT_S", "50"))

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

SUB_PROMPT = """You are {name} on a power-grid storm co-pilot team. Your focus: {focus}
Call each of your tools at most once, then reply with ONLY compact JSON, no prose:
{{"agent":"{name}","findings":[{{"target":"R3","issue":"short","evidence":"numbers copied from tool output","severity":1}}]}}
severity: 1 watch, 2 act soon, 3 act now. At most 4 findings. Only use numbers that appear in tool output."""

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
   "tradeoffs":"...","confidence":0.8,"assumptions":["..."],"attempts":2}],
 "disagreements":["fire_agent wanted X; brownout_agent objected because Y; resolved by Z"]}
If the grid is already safe with no action, return "plans": []. Do not invent numbers: the backend re-simulates every plan."""


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


def _make_callback(log):
    from langchain_core.callbacks import BaseCallbackHandler

    class TaskLogger(BaseCallbackHandler):
        def __init__(self):
            self.runs = {}

        def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kw):
            if (serialized or {}).get("name") != "task":
                return
            data = inputs or {}
            if not data:
                try:
                    data = json.loads(input_str)
                except Exception:  # noqa: BLE001
                    data = {}
            sub = data.get("subagent_type", "subagent")
            self.runs[run_id] = sub
            log("coordinator", f"-> {sub}: {str(data.get('description', ''))[:110]}")

        def on_tool_end(self, output, *, run_id=None, **kw):
            sub = self.runs.pop(run_id, None)
            if not sub:
                return
            text = _text_of(getattr(output, "content", output))
            try:
                f = _parse_json(text).get("findings", [])
                for item in f[:2]:
                    log(sub, f"{item.get('target')}: {item.get('issue')} ({item.get('evidence')})")
                if not f:
                    log(sub, "no significant findings")
            except Exception:  # noqa: BLE001
                log(sub, text[:160])

    return TaskLogger()


LA_SUBAGENTS = {
    "fire_agent": ("Ignition risk on the real LA grid: lines over the calibrated fire alarm (P(fault) x P(ignition | humidity, "
                   "LANDFIRE fuel)), which ones are sub-transmission (shut off) vs 220-500 kV corridors (patrol). For the "
                   "worst 2-3 lines, call get_site_context(line_id) to say what is on the ground and who is downwind.",
                   ["get_grid_summary", "get_event_context", "get_site_context"]),
    "wind_agent": ("Storm damage: calibrated wind+rain damage model (which lines are likely to be knocked down, storm index vs "
                   "alarm, historical accuracy). Recommends crew pre-staging and patrols. For the lines you name, call "
                   "get_site_context(line_id): road access and vegetation at each photo decide where patrols can actually reach.",
                   ["get_storm_damage", "get_grid_summary", "get_site_context"]),
    "heat_agent": ("Heat and load: demand, overloaded lines, conductor temperature vs limit.", ["get_grid_summary", "get_weather"]),
    "brownout_agent": ("Who loses power if lines are switched off: unserved MW, hospitals and water plants on backup. "
                       "Weigh that against what a spark would reach: get_site_context(line_id) shows homes and people "
                       "downwind of the line's riskiest stretches.",
                       ["get_grid_summary", "get_critical_sites", "get_site_context"]),
}

LA_COORDINATOR_PROMPT = """You coordinate an AI co-pilot for a power-grid operator on the REAL Greater Los Angeles transmission grid
(OSM lines >= 66 kV, ids L1..L867; electrical values are modeled). You never act on the grid: you propose plans and the
human operator approves or rejects them. The whole run has a 45 second budget, so be brief.

Workflow:
1. In ONE message call the `task` tool 4 times in parallel: fire_agent, wind_agent, heat_agent, brownout_agent.
   Description: "Assess current conditions and return your findings JSON."
2. Call get_candidate_actions() once. Build up to 3 plans of at most 3 actions each, using ONLY actions from that list
   (do not invent line or bus ids). deenergize_lines shuts a group of lines off as ONE action (PSPS style).
3. Call simulate_plan for every plan (parallel calls are fine). It is the ONLY way to judge a plan. If is_safe is false,
   read the violations and revise, at most 2 attempts per plan.
4. For the lines your plans switch off or leave energized, the specialists may have quoted get_site_context; you may
   call it too. Put what is on the ground (homes on the brush edge, poor road access) in "why" and "tradeoffs". It is
   Gemini's reading of aerial photos: context, never a reason to change a number.
5. Call get_event_context(event_id) and get_validation_summary() once and cite what the history says about trusting the
   signal (numbers only from those tools). Fire risk drives de-energization; the wind_agent's storm index drives crew
   pre-staging and patrols (mention those in "tradeoffs", they are not grid actions).
6. Reply with ONLY this JSON (no prose, no code fences):
{"situation":"2 plain-language sentences",
 "plans":[{"id":"P1","title":"short","steps":[{"action":{...},"why":"plain language","raised_by":["fire_agent"]}],
   "tradeoffs":"...","confidence":0.8,"assumptions":["..."],"attempts":1}],
 "disagreements":["fire_agent wanted X; brownout_agent objected because Y; resolved by Z"]}
If the grid is already safe with no action, return "plans": []. Do not invent numbers: the backend re-simulates every plan.
The specialists started from the same situation brief you have below; their findings add detail to it."""

BRIEF_HEADER = ("Situation brief (shared by all agents; start from it instead of re-deriving it; quote its numbers, "
                "never invent new ones):\n")


def _subagent_specs(state, log, table, extra: str = ""):
    return [{"name": n, "description": f, "system_prompt": SUB_PROMPT.format(name=n, focus=f) + extra,
             "tools": make_tools(state, log, n, t)}
            for n, (f, t) in table.items()]


def _la_prompts(state, log):
    """LA path: build the brief once; return (subagent specs, coordinator prompt), both carrying the brief."""
    brief = build_brief(state)
    log("coordinator", "situation brief: " + brief["headlines"][0])
    block = "\n\n" + BRIEF_HEADER + brief_text(brief)
    subagents = _subagent_specs(state, log, LA_SUBAGENTS, block)
    prompt = LA_COORDINATOR_PROMPT + f"\nThis run is event {state.eid} at {state.scenario}." + block
    return subagents, prompt


def _run_llm(state, log) -> dict:
    from deepagents import create_deep_agent

    model_name = LLM.model_name()
    model = LLM.chat_model(timeout=40, max_retries=1, max_tokens=4096)
    la = hasattr(state, "grid")
    if la:  # LA: only agents that have real data to read, one parallel wave (audit: 5 of 9 were no-ops on LA)
        subagents, prompt = _la_prompts(state, log)
        coord_tools = ["get_candidate_actions", "simulate_plan", "get_event_context", "get_validation_summary", "get_critical_sites",
                       "get_site_context"]
    else:
        subagents = _subagent_specs(state, log, SUBAGENTS)
        prompt = COORDINATOR_PROMPT
        coord_tools = ["get_grid_summary", "get_critical_sites", "simulate_plan"]
    agent = create_deep_agent(model=model, tools=make_tools(state, log, "coordinator", coord_tools), system_prompt=prompt,
                              subagents=subagents)
    log("coordinator", f"model {model_name}: planning with {len(subagents)} specialist agents")
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Scenario: {state.scenario}. Assess the grid and propose plans."}]},
        config={"callbacks": [_make_callback(log)], "recursion_limit": 120},
    )
    return _parse_json(_text_of(result["messages"][-1]))


def run_team(state, log: JobLog) -> dict:
    """Entry point used by the API. Always returns physics-verified output.
    The rule-based planner runs IN PARALLEL with the LLM team (silently), so a timeout or error falls back instantly."""
    if not LLM.enabled():
        return run_mock(state, log, "mock mode: no GEMINI_API_KEY")

    gate = {"open": True}

    def gated(agent, message):  # once we fall back, late LLM lines are dropped
        if gate["open"]:
            log(agent, message)

    box: dict = {}
    backup: dict = {"lines": []}

    def worker():
        try:
            box["out"] = _run_llm(state, gated)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    def backup_worker():
        try:
            backup["out"] = run_mock(state, lambda a, m: backup["lines"].append((a, m)), "parallel backup", pace=0)
        except Exception as e:  # noqa: BLE001
            backup["err"] = e

    th = threading.Thread(target=worker, daemon=True)
    bk = threading.Thread(target=backup_worker, daemon=True)
    th.start()
    bk.start()
    th.join(TIMEOUT_S)
    if th.is_alive() or "err" in box or not isinstance(box.get("out"), dict):
        gate["open"] = False
        why = f"agent run timed out after {TIMEOUT_S:.0f}s" if th.is_alive() else f"agent run failed: {str(box.get('err'))[:120]}"
        log("system", why + "; using the rule-based plan computed in parallel")
        bk.join(30)
        if "out" in backup:
            for a, m in backup["lines"]:
                log(a, m)
            out = backup["out"]
            out["fallback_reason"] = why
            return out
        return run_mock(state, log, why, pace=0.05)

    out = box["out"]
    out["mode"] = "llm"
    log("coordinator", "LLM plans received; backend re-running physics on each plan")
    out = verify_output(state, out, log)
    try:  # crew staging numbers always come from the calibrated storm model, never from the LLM
        from .mock_planner import _Planner

        out["crew_staging"] = _Planner(state, lambda *a, **k: None, 0).storm()[1]
    except Exception:  # noqa: BLE001
        pass
    return out
