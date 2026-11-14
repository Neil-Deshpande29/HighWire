"""deepagents team: a coordinator plus cause / reports / outcome subagents.

Checked against deepagents 0.7.38: create_deep_agent(model, tools, *, system_prompt, subagents, ...).
Subagent dicts use keys name, description, system_prompt, tools, model.
Falls back to the rule-based planner when there is no ANTHROPIC_API_KEY, on any error, or after a timeout.
"""
from __future__ import annotations

import json
import os
import re
import threading

from .mock_planner import run_mock
from .tools import JobLog, make_tools, verify_output

DEFAULT_MODEL = "claude-sonnet-5-5"
TIMEOUT_S = float(os.environ.get("AGENT_TIMEOUT_S", "60"))

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


LA_GRID_TEXT = """   Grid: the REAL Greater Los Angeles transmission network (OSM lines >= 66 kV, ids L1..L867, buses B1..B675).
   Electrical values are MODELED from voltage class. Fire risk per line comes from the calibrated physics; a line
   with risk >= the line alarm is a violation. You may also use {"type":"deenergize_lines","target":"label","targets":["L12","L40"]}
   to shut off a group of lines as ONE action (PSPS style). Avoid de-energizing >= 220 kV import corridors unless
   unavoidable: they feed the basin. Call get_event_context(event_id) and get_validation_summary() and cite what the
   historical validation says about trusting this signal (numbers only from those tools)."""


def _run_llm(state, log) -> dict:
    from deepagents import create_deep_agent
    from langchain_anthropic import ChatAnthropic

    model_name = os.environ.get("MODEL", DEFAULT_MODEL).removeprefix("anthropic:")
    model = ChatAnthropic(model=model_name, max_tokens=4096, default_request_timeout=45, max_retries=1)
    subagents = []
    for name, (focus, tool_names) in SUBAGENTS.items():
        subagents.append(
            {
                "name": name,
                "description": focus,
                "system_prompt": SUB_PROMPT.format(name=name, focus=focus),
                "tools": make_tools(state, log, name, tool_names),
            }
        )
    la = hasattr(state, "grid")
    prompt = COORDINATOR_PROMPT
    coord_tools = ["get_grid_summary", "get_critical_sites", "simulate_plan"]
    if la:
        start = prompt.index("   Grid: lines R1-R4")
        end = prompt.index("4. Call simulate_plan")
        prompt = prompt[:start] + LA_GRID_TEXT + "\n" + prompt[end:]
        prompt += f"\nThis run replays event {state.eid} at {state.scenario}."
        coord_tools += ["get_event_context", "get_validation_summary"]
    agent = create_deep_agent(
        model=model,
        tools=make_tools(state, log, "coordinator", coord_tools),
        system_prompt=prompt,
        subagents=subagents,
    )
    log("coordinator", f"model {model_name}: planning with 9 specialist agents")
    result = agent.invoke(
        {"messages": [{"role": "user", "content": f"Scenario: {state.scenario}. Assess the grid and propose plans."}]},
        config={"callbacks": [_make_callback(log)], "recursion_limit": 120},
    )
    return _parse_json(_text_of(result["messages"][-1]))


def run_team(state, log: JobLog) -> dict:
    """Entry point used by the API. Always returns physics-verified output."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return run_mock(state, log, "mock mode: no ANTHROPIC_API_KEY")

    gate = {"open": True}

    def gated(agent, message):  # once we fall back, late LLM lines are dropped
        if gate["open"]:
            log(agent, message)

    box: dict = {}

    def worker():
        try:
            box["out"] = _run_llm(state, gated)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join(TIMEOUT_S)
    if th.is_alive() or "err" in box or not isinstance(box.get("out"), dict):
        gate["open"] = False
        why = f"agent run timed out after {TIMEOUT_S:.0f}s" if th.is_alive() else f"agent run failed: {str(box.get('err'))[:120]}"
        log("system", why + "; falling back")
        return run_mock(state, log, why, pace=0.1)

    out = box["out"]
    out["mode"] = "llm"
    log("coordinator", "LLM plans received; backend re-running physics on each plan")
    return verify_output(state, out, log)
