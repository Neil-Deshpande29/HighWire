"""The deep-agents team, driven by rules instead of a language model.

The team is the same one the AI path builds (backend/agents/team.py): an orchestrator and five specialists in a
deepagents graph, with the same tools, the same `task` hand-offs and deepagents' own file system (write_file,
read_file, ls). What changes is who decides each step. Here it is `RulesModel`, a chat model with no model in it: it
looks at where the conversation has got to and makes the next tool call from a script, and the script is worked out
by the rule-based planner (mock_planner._Planner) for this hour before the run starts.

So every step is a real agent call through the graph, and none of it depends on an outside service:

  orchestrator   write_file /plan/dispatch.md        who it calls and why (the planner's routing rules)
                 task x N                             only the specialists it marked CALL
    specialist   its own read tools                   real reads of the grid, the weather, the buildings...
                 write_file /findings/<agent>.json    what the planner's rules found for that agent
                 write_file /findings/<agent>.md
  orchestrator   ls /findings, read_file x N
                 get_candidate_actions
                 simulate_plan x P                    the physics engine judges each plan, as for the AI team
                 send_alert                           only on an Operate run, only under the alert rule
                 final answer: the plans

The plans are then re-checked by the physics engine like any other team's (tools.verify_output). The words on screen
are written by code, so nothing here needs the number filter that model text goes through.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from .mock_planner import DISPATCH, LA_AGENTS, _Planner
from .tools import JobLog, emit, findings_path

WHO = re.compile(r"You are (\w+_agent) on a power-grid")
# what each specialist reads before it writes: its own tools (team.LA_SUBAGENTS), with the argument a tool needs
READS = {
    "fire_agent": ("get_grid_summary", "get_site_context"),
    "wind_agent": ("get_storm_damage", "get_grid_summary"),
    "heat_agent": ("get_grid_summary", "get_weather"),
    "brownout_agent": ("get_grid_summary", "get_critical_sites"),
    "buildings_agent": ("get_building_impact", "get_critical_sites"),
}


def _txt(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _call(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}", "type": "tool_call"}


def build_script(state) -> tuple[dict, dict, JobLog]:
    """Work the hour out with the rule-based planner, quietly. Returns (script for RulesModel, the planner's output
    before the physics re-check, the quiet log it wrote to)."""
    quiet = JobLog()
    planner = _Planner(state, quiet, 0)
    out = planner.run()
    called = [a for a in LA_AGENTS if a in (planner.called or ())]
    findings = planner.findings
    worst = lambda agent: next((f["target"] for f in findings.get(agent, []) if f["target"] in state.lines), None)  # noqa: E731
    flagged = [f["target"] for f in findings.get("fire_agent", []) if f["target"] in state.lines][:12]
    script = {
        "dispatch": planner.vfs.get(DISPATCH, ""),
        "called": called,
        "files": {p: c for p, c in planner.vfs.items() if p != DISPATCH},
        "site_line": {a: worst(a) for a in called},
        "flagged": flagged,
        "plans": [{"title": p["title"], "steps": [{"action": s["action"], "why": s["why"], "raised_by": [findings_path(a) for a in s.get("raised_by", [])]}
                                                   for s in p["steps"]]} for p in out["plans"]],
        "situation": out.get("situation", ""),
        "alerts": [],
    }
    if getattr(state, "alerts_on", False):  # the orchestrator's alert rule, for an Operate run
        try:
            from .. import notify

            script["alerts"] = ([f"Rewire ALERT: {r['id']} is at very high fire risk." for r in notify.very_high_fire(state)[:notify.MAX_PER_HOUR]]
                                + [f"Rewire ALERT: {t['id']} is very likely to be knocked down by this storm." for t in notify.likely_out(state)[:notify.MAX_PER_HOUR]])
        except Exception:  # noqa: BLE001  a text never breaks a run
            pass
    return script, out, quiet


class RulesModel(BaseChatModel):
    """Plays every agent from the script. It reads only how far the conversation has got (which tools have already
    answered) and returns the next step; it never looks at what a tool said, because the script already holds the
    hour's findings, worked out by the same rules."""

    script: dict
    can_alert: bool = False

    @property
    def _llm_type(self) -> str:
        return "rewire-rules"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = "\n".join(_txt(m.content) for m in messages if isinstance(m, SystemMessage))
        done = [m.name for m in messages if isinstance(m, ToolMessage)]
        who = WHO.search(system)
        msg = self._specialist(who.group(1), done) if who else self._orchestrator(done)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _specialist(self, name: str, done: list[str]) -> AIMessage:
        s = self.script
        reads = []
        for tool in READS.get(name, ()):
            if tool == "get_site_context":
                if s["site_line"].get(name):
                    reads.append(_call(tool, {"line_id": s["site_line"][name]}))
            elif tool == "get_building_impact":
                if s["flagged"]:
                    reads.append(_call(tool, {"line_ids_json": json.dumps(s["flagged"])}))
            else:
                reads.append(_call(tool, {}))
        if reads and not any(c["name"] in done for c in reads):
            return AIMessage(content="", tool_calls=reads)
        if "write_file" not in done:
            return AIMessage(content="", tool_calls=[
                _call("write_file", {"file_path": findings_path(name), "content": s["files"].get(findings_path(name), "{}")}),
                _call("write_file", {"file_path": findings_path(name, "md"), "content": s["files"].get(findings_path(name, "md"), "")})])
        return AIMessage(content=f"wrote {findings_path(name)}")

    def _orchestrator(self, done: list[str]) -> AIMessage:
        s = self.script
        if "write_file" not in done:
            return AIMessage(content="", tool_calls=[_call("write_file", {"file_path": DISPATCH, "content": s["dispatch"]})])
        if s["called"] and "task" not in done:
            return AIMessage(content="", tool_calls=[_call("task", {"description": "Assess current conditions and write your findings files.", "subagent_type": a})
                                                     for a in s["called"]])
        if "ls" not in done:
            return AIMessage(content="", tool_calls=[_call("ls", {"path": "/findings"})])
        if s["called"] and "read_file" not in done:
            return AIMessage(content="", tool_calls=[_call("read_file", {"file_path": findings_path(a)}) for a in s["called"]])
        if s["plans"] and "get_candidate_actions" not in done:
            return AIMessage(content="", tool_calls=[_call("get_candidate_actions", {})])
        if s["plans"] and "simulate_plan" not in done:
            return AIMessage(content="", tool_calls=[_call("simulate_plan", {"actions_json": json.dumps([st["action"] for st in p["steps"]])}) for p in s["plans"]])
        if self.can_alert and s["alerts"] and "send_alert" not in done:
            return AIMessage(content="", tool_calls=[_call("send_alert", {"message": m}) for m in s["alerts"]])
        return AIMessage(content=json.dumps({"situation": s["situation"], "plans": [{"id": f"P{k + 1}", **p} for k, p in enumerate(s["plans"])], "disagreements": []}))


def run(state, log) -> dict:
    """One rule-driven run of the deep-agents team on the LA grid. Returns the planner-shaped output (mode "mock",
    engine "deepagents"), not yet re-checked by physics; the caller does that. Raises if the graph cannot be run."""
    from deepagents import create_deep_agent

    from . import team as T

    log("coordinator", "reading this hour and working out who is needed")  # shown at once, while the rules do their work
    script, out, quiet = build_script(state)
    plain = [e for e in quiet.entries if not e.get("kind") and e["agent"] in ("coordinator", "system")]
    first_event = next((k for k, e in enumerate(quiet.entries) if e.get("kind")), len(quiet.entries))
    last_done = max((k for k, e in enumerate(quiet.entries) if e.get("kind") == "agent_done"), default=first_event)
    for e in plain:  # what the orchestrator starts from: the situation brief and how fast things are moving
        if quiet.entries.index(e) < first_event:
            log(e["agent"], e["message"])
    subagents = T._subagent_specs(state, log, T.LA_SUBAGENTS, prompt=T.LA_SUB_PROMPT)
    tools = T.make_tools(state, log, "coordinator", ["get_candidate_actions", "simulate_plan", "get_critical_sites", "get_site_context",
                                                      "get_building_impact", "get_operator_feedback"])
    can_alert = bool(script["alerts"])
    if can_alert:
        from .. import notify

        tools = tools + [notify.alert_tool(state, log)]
    model = RulesModel(script=script, can_alert=can_alert)
    agent = create_deep_agent(model=model, tools=tools, system_prompt=T.LA_COORDINATOR_PROMPT, subagents=subagents)
    log("coordinator", f"the orchestrator decides which of {len(subagents)} specialist agents to call (rules, no AI model)")
    watch = T._make_callback(log, T._grid_ids(state), trusted=True)
    result = agent.invoke({"messages": [{"role": "user", "content": f"Scenario: {state.scenario}. Assess the grid and propose plans."}]},
                          config={"callbacks": [watch], "recursion_limit": 150})
    watch.finish()
    vfs = getattr(log, "vfs", None)
    if vfs is not None:
        for path, data in (result.get("files") or {}).items():
            vfs.setdefault(path, T._file_text(data))
    answer = T._parse_json(T._text_of(result["messages"][-1]))
    said = [[s["action"] for s in p.get("steps", [])] for p in answer.get("plans", [])]
    if said != [[s["action"] for s in p["steps"]] for p in out["plans"]]:
        raise RuntimeError("the agent run did not end on the plans its rules worked out")
    for e in plain:  # how the orchestrator got from the findings to its plans
        if quiet.entries.index(e) > last_done:
            log(e["agent"], e["message"])
    stats = out.get("stats") or {}
    if stats.get("screened") is not None:
        emit(log, "physics", f"screened {stats['screened']} combinations ({stats.get('simulations', 0)} simulations): {stats.get('safe', 0)} safe",
             "physics", screened=stats["screened"], safe=stats.get("safe", 0), simulations=stats.get("simulations", 0))
    out["mode"], out["engine"] = "mock", "deepagents"
    return out


__all__ = ["RulesModel", "build_script", "run", "Any"]
