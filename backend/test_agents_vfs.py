"""The orchestrator decides who to call, findings travel through the run's shared files, and every step of that is a
structured event the screen can draw. Offline: the rule-based planner, and the AI team on a scripted fake model."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents import synthesis  # noqa: E402
from backend.agents import team as T  # noqa: E402
from backend.agents.mock_planner import DISPATCH, LA_AGENTS, run_mock  # noqa: E402
from backend.agents.tools import EVENT_KINDS, JobLog, findings_path  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402
from backend.la.state import LAState  # noqa: E402

WOOLSEY = ("E1", "2018-11-08T19:00:00+00:00")  # 29 lines over the fire alarm, gusts near 30 m/s
CALM = ("E1", "2018-11-08T08:00:00+00:00")  # midnight local, before the wind


@pytest.fixture(scope="module")
def grid():
    return LAGrid()


def _run(grid, when):
    log = JobLog()
    out = run_mock(LAState(*when, grid=grid), log, pace=0)
    return out, log, [e for e in log.entries if e.get("kind")]


def test_routing_skips_an_agent_when_its_trigger_is_absent(grid):
    out, log, events = _run(grid, WOOLSEY)
    route = {e["meta"]["agent"]: e["meta"] for e in events if e["kind"] == "route"}
    assert set(route) == set(LA_AGENTS)  # every specialist gets a decision, called or not, with a reason
    assert all(m["reason"] for m in route.values())
    assert route["fire_agent"]["called"] and route["wind_agent"]["called"] and route["brownout_agent"]["called"]
    assert not route["heat_agent"]["called"] and "70%" in route["heat_agent"]["reason"]  # no line is heavily loaded
    spawned = [e["meta"]["agent"] for e in events if e["kind"] == "spawn"]
    assert set(spawned) == {a for a, m in route.items() if m["called"]}
    assert "heat_agent" not in out["agent_findings"] and not any(e["agent"] == "heat_agent" for e in log.entries)
    assert "heat_agent: SKIP" in out["vfs"][DISPATCH] and "fire_agent: CALL" in out["vfs"][DISPATCH]

    _, _, quiet = _run(grid, CALM)
    quiet_route = {e["meta"]["agent"]: e["meta"]["called"] for e in quiet if e["kind"] == "route"}
    assert not quiet_route["brownout_agent"] and not quiet_route["buildings_agent"]  # nothing on the table cuts power


def test_every_called_agent_writes_its_findings_and_the_orchestrator_reads_them(grid):
    out, _, events = _run(grid, WOOLSEY)
    called = [e["meta"]["agent"] for e in events if e["kind"] == "spawn"]
    wrote = {e["meta"]["path"] for e in events if e["kind"] == "vfs_write"}
    read = [e["meta"]["path"] for e in events if e["kind"] == "vfs_read"]
    for a in called:
        assert {findings_path(a), findings_path(a, "md")} <= wrote <= set(out["vfs"])
        data = json.loads(out["vfs"][findings_path(a)])
        assert data["agent"] == a and data["findings"] == out["agent_findings"].get(a, [])
        assert out["vfs"][findings_path(a, "md")].startswith("# ")
    assert read == [findings_path(a) for a in called] and all(e["meta"]["reader"] == "coordinator" for e in events if e["kind"] == "vfs_read")
    order = [e["kind"] for e in events]
    assert order.index("route") < order.index("spawn") < order.index("vfs_read") < order.index("physics") < order.index("plans")
    assert all(e["meta"]["bytes"] == len(out["vfs"][e["meta"]["path"]].encode()) for e in events if e["kind"] == "vfs_write")


def test_plans_cite_only_findings_files_that_exist(grid):
    out, _, _ = _run(grid, WOOLSEY)
    assert out["plans"]
    for p in out["plans"]:
        for s in p["steps"]:
            assert s["raised_from"] and set(s["raised_from"]) <= set(out["vfs"])
            assert s["raised_from"] == [findings_path(a) for a in s["raised_by"]]
    assert not any("heat_agent" in s["raised_by"] for p in out["plans"] for s in p["steps"])  # it was not called


def test_the_summary_is_built_from_the_events_and_files(grid):
    out, log, _ = _run(grid, WOOLSEY)
    syn = synthesis.build(out, log.entries)
    assert [r["agent"] for r in syn["routing"]] == list(LA_AGENTS)
    by_id = {a["id"]: a for a in syn["agents"]}
    assert by_id["fire_agent"]["called"] and by_id["fire_agent"]["wrote"] == [findings_path("fire_agent"), findings_path("fire_agent", "md")]
    assert not by_id["heat_agent"]["called"] and by_id["heat_agent"]["wrote"] == [] and by_id["heat_agent"]["reason"]
    assert {f["path"] for f in syn["files"]} == set(out["vfs"])
    assert next(f for f in syn["files"] if f["path"] == findings_path("fire_agent"))["read"]


# ---------------------------------------------------------------- the AI team, on a scripted model that uses the files
def test_the_ai_team_and_the_fallback_emit_the_same_kinds_of_event(grid, monkeypatch):
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    import backend.llm as LLM
    from backend.test_team_llm import FakeGemini, _call, _txt

    class FilesGemini(FakeGemini):
        """Follows the workflow in the prompts: dispatch file, only the chosen agents, findings files, ls, read."""

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            system = "\n".join(_txt(m.content) for m in messages if isinstance(m, SystemMessage))
            import re

            who = re.search(r"You are (\w+_agent) on a power-grid", system)
            done = [m.name for m in messages if isinstance(m, ToolMessage)]
            if who:
                name = who.group(1)
                if "write_file" not in done:
                    found = {"agent": name, "findings": [{"target": "L5", "issue": "flags this line", "evidence": "over the alarm", "severity": 3}]}
                    msg = AIMessage(content="", tool_calls=[
                        _call("write_file", {"file_path": findings_path(name), "content": json.dumps(found)}),
                        _call("write_file", {"file_path": findings_path(name, "md"), "content": "L5 is over the alarm."})])
                else:
                    msg = AIMessage(content=f"wrote {findings_path(name)}")
            elif "write_file" not in done:
                msg = AIMessage(content="", tool_calls=[_call("write_file", {"file_path": DISPATCH, "content":
                    "fire_agent: CALL - lines over the fire alarm\nwind_agent: CALL - strong gusts\nheat_agent: SKIP - no heavy loading\n"
                    "brownout_agent: SKIP - nothing to weigh yet\nbuildings_agent: SKIP - nothing to weigh yet"})])
            elif "task" not in done:
                msg = AIMessage(content="", tool_calls=[_call("task", {"description": "Assess and write your findings files.", "subagent_type": a})
                                                         for a in ("fire_agent", "wind_agent")])
            elif "ls" not in done:
                msg = AIMessage(content="", tool_calls=[_call("ls", {"path": "/findings"})])
            elif "read_file" not in done:
                msg = AIMessage(content="", tool_calls=[_call("read_file", {"file_path": findings_path(a)}) for a in ("fire_agent", "wind_agent")])
            elif "get_candidate_actions" not in done:
                msg = AIMessage(content="", tool_calls=[_call("get_candidate_actions", {})])
            else:
                tool = next(m for m in messages if isinstance(m, ToolMessage) and m.name == "get_candidate_actions")
                action = json.loads(_txt(tool.content))[0]["action"]
                if "simulate_plan" not in done:
                    msg = AIMessage(content="", tool_calls=[_call("simulate_plan", {"actions_json": json.dumps([action])})])
                else:
                    msg = AIMessage(content=json.dumps({"situation": "Lines are over the fire alarm.", "plans": [{
                        "id": "P1", "title": "Shut off the riskiest lines", "tradeoffs": "some load is lost", "assumptions": [],
                        "steps": [{"action": action, "why": "over the fire alarm",
                                   "raised_by": [findings_path("fire_agent"), "/findings/heat_agent.json", "/findings/made_up.json"]}]}],
                        "disagreements": []}))
            return ChatResult(generations=[ChatGeneration(message=msg)])

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-not-a-real-key")
    monkeypatch.setenv("AGENT_PLANNER", "gemini")
    monkeypatch.setattr(LLM, "chat_model", lambda *a, **k: FilesGemini())
    log = JobLog()
    out = T.run_team(LAState(*WOOLSEY, grid=grid), log)
    assert out["mode"] == "llm" and out["fallback_reason"] is None, out.get("fallback_reason")
    events = [e for e in log.entries if e.get("kind")]
    route = {e["meta"]["agent"]: e["meta"]["called"] for e in events if e["kind"] == "route"}
    assert route == {"fire_agent": True, "wind_agent": True, "heat_agent": False, "brownout_agent": False, "buildings_agent": False}
    assert [e["meta"]["agent"] for e in events if e["kind"] == "spawn"] == ["fire_agent", "wind_agent"]
    assert {DISPATCH, findings_path("fire_agent"), findings_path("fire_agent", "md"), findings_path("wind_agent")} <= set(out["vfs"])
    assert {e["meta"]["path"] for e in events if e["kind"] == "vfs_read"} == {findings_path("fire_agent"), findings_path("wind_agent")}
    step = out["plans"][0]["steps"][0]  # a file that was never written cannot be cited, whatever the model claims
    assert step["raised_by"] == ["fire_agent"] and step["raised_from"] == [findings_path("fire_agent")]

    _, _, fallback = _run(grid, WOOLSEY)
    kinds = lambda evs: {e["kind"] for e in evs}  # noqa: E731
    assert kinds(events) == kinds(fallback) == set(EVENT_KINDS) - {"tool"} | {"tool"}
    assert all(e["kind"] in EVENT_KINDS and isinstance(e["meta"], dict) and isinstance(e["t"], float) for e in events + fallback)
