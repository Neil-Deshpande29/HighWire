"""The deep-agents team driven by rules: real agent calls through the graph, no AI model, no network, no key."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents import scripted as S  # noqa: E402
from backend.agents import team as T  # noqa: E402
from backend.agents.mock_planner import DISPATCH, LA_AGENTS, run_mock  # noqa: E402
from backend.agents.tools import EVENT_KINDS, JobLog, findings_path  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402
from backend.la.state import LAState  # noqa: E402

STORM = ("E1", "2018-11-08T19:00:00+00:00")  # Woolsey: 29 lines over the fire alarm
CALM = ("E1", "2018-11-08T08:00:00+00:00")
HEAT = ("H2", "2022-09-01T05:00:00+00:00")  # five overloaded lines: a five-step plan


@pytest.fixture(scope="module")
def grid():
    return LAGrid()


@pytest.fixture(scope="module")
def storm_run(grid):
    log = JobLog()
    out = T.run_team(LAState(*STORM, grid=grid), log)
    return out, log


def test_the_default_planner_is_the_rule_driven_agent_team(storm_run, monkeypatch):
    out, log = storm_run
    assert T.planner_choice() == "agents"
    assert out["planner"] == "agents" and out["mode"] == "mock" and out["fallback_reason"] is None
    for value, want in (("gemini", "gemini"), ("AI", "gemini"), ("rules", "rules"), ("agents", "agents"), ("auto", "agents"), ("", "agents")):
        monkeypatch.setenv("AGENT_PLANNER", value)
        assert T.planner_choice() == want


def test_every_step_is_a_real_agent_call(storm_run):
    """The orchestrator and the specialists act through the deep-agents graph: hand-offs, tool reads, files written and
    read back through the graph's own file system, plans judged by the physics tool."""
    out, log = storm_run
    events = [e for e in log.entries if e.get("kind")]
    kinds = [e["kind"] for e in events]
    assert set(kinds) == set(EVENT_KINDS)
    assert kinds.index("route") < kinds.index("spawn") < kinds.index("vfs_read") < len(kinds) - 1 and kinds[-1] == "plans"
    route = {e["meta"]["agent"]: e["meta"] for e in events if e["kind"] == "route"}
    called = [a for a in LA_AGENTS if route[a]["called"]]
    assert set(route) == set(LA_AGENTS) and not route["heat_agent"]["called"] and "70%" in route["heat_agent"]["reason"]  # reasons arrive unfiltered
    assert sorted(e["meta"]["agent"] for e in events if e["kind"] == "spawn") == sorted(called)
    tools = {(e["agent"], e["meta"]["tool"]) for e in events if e["kind"] == "tool"}
    assert {("fire_agent", "get_grid_summary"), ("fire_agent", "get_site_context"), ("wind_agent", "get_storm_damage"), ("brownout_agent", "get_critical_sites"),
            ("buildings_agent", "get_building_impact"), ("coordinator", "ls"), ("coordinator", "get_candidate_actions")} <= tools
    assert not any(a == "heat_agent" for a, _ in tools)  # an agent that was not called does nothing
    for a in called:
        done = next(e for e in events if e["kind"] == "agent_done" and e["meta"]["agent"] == a)
        assert done["meta"]["wrote"] is True and json.loads(out["vfs"][findings_path(a)])["agent"] == a
    assert [e["meta"]["path"] for e in events if e["kind"] == "vfs_read"] == [findings_path(a) for a in called]
    assert any(e["agent"] == "physics" and "SAFE" in e["message"] for e in log.entries)  # simulate_plan ran on the plan
    assert "fire_agent: CALL" in out["vfs"][DISPATCH] and "heat_agent: SKIP" in out["vfs"][DISPATCH]


def test_it_reaches_the_same_plans_as_the_rules_alone(storm_run, grid):
    out, _ = storm_run
    alone = run_mock(LAState(*STORM, grid=grid), JobLog(), pace=0)
    assert [p["actions"] for p in out["plans"]] == [p["actions"] for p in alone["plans"]]
    assert [p["result"] for p in out["plans"]] == [p["result"] for p in alone["plans"]] and out["agent_findings"] == alone["agent_findings"]
    for p in out["plans"]:
        for s in p["steps"]:
            assert s["raised_from"] and set(s["raised_from"]) <= set(out["vfs"])


def test_a_plan_of_five_steps_and_an_hour_with_nothing_to_do(grid):
    log = JobLog()
    heat = T.run_team(LAState(*HEAT, grid=grid), log)
    assert heat["planner"] == "agents" and len(heat["plans"][0]["actions"]) == 5 and heat["plans"][0]["result"]["is_safe"]
    assert any(e["agent"] == "physics" and "SAFE" in e["message"] for e in log.entries)  # all five steps went through simulate_plan
    log = JobLog()
    calm = T.run_team(LAState(*CALM, grid=grid), log)
    kinds = {e["kind"] for e in log.entries if e.get("kind")}
    assert calm["planner"] == "agents" and calm["plans"] == [] and {"route", "plans"} <= kinds
    assert not any(e.get("kind") == "tool" and e["meta"]["tool"] == "simulate_plan" for e in log.entries)


def test_the_script_is_only_what_the_rules_worked_out(grid):
    script, out, quiet = S.build_script(LAState(*STORM, grid=grid))
    assert script["called"] == [a for a in LA_AGENTS if f"{a}: CALL" in script["dispatch"]]
    assert set(script["files"]) == {findings_path(a, ext) for a in script["called"] for ext in ("json", "md")}
    assert [[s["action"] for s in p["steps"]] for p in script["plans"]] == [[s["action"] for s in p["steps"]] for p in out["plans"]]
    assert script["alerts"] == []  # not an Operate run: the orchestrator may not text anyone
    model = S.RulesModel(script=script)
    assert model.bind_tools([]) is model and model._llm_type == "rewire-rules"


def test_if_the_agent_graph_cannot_run_the_plain_rules_answer(grid, monkeypatch):
    def broken(state, log):
        log("coordinator", "calling Fire", "route", agent="fire_agent", called=True, reason="x")
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(S, "run", broken)
    log = JobLog()
    out = T.run_team(LAState(*STORM, grid=grid), log)
    assert out["planner"] == "rules" and out["plans"] and out["fallback_reason"] is None
    assert any("could not be run" in e["message"] for e in log.entries)
    spawned = [e for e in log.entries if e.get("kind") == "spawn"]
    assert len({e["meta"]["agent"] for e in spawned}) == len(spawned)  # the broken attempt left nothing half-drawn


def test_asking_for_plain_rules_skips_the_agent_graph(grid, monkeypatch):
    monkeypatch.setenv("AGENT_PLANNER", "rules")
    monkeypatch.setattr(S, "run", lambda state, log: pytest.fail("the agent graph must not be used"))
    out = T.run_team(LAState(*CALM, grid=grid), JobLog())
    assert out["planner"] == "rules" and out["fallback_reason"] is None
