"""Shared situation brief: schema, no hindsight, size, caching, and that every agent path carries it."""
import json
import warnings

import pytest

from backend.agents import team as T
from backend.agents.brief import brief_text, build_brief
from backend.agents.mock_planner import run_mock
from backend.agents.tools import make_tools
from backend.la import replay as RP
from backend.la.state import LAState

warnings.filterwarnings("ignore")

EID, T_UTC = "E3", "2025-01-08T02:00:00+00:00"
KEYS = {"version", "event", "t_utc", "scenario", "fire", "storm", "grid", "weather", "site", "trust", "headlines"}
HINDSIGHT = {"outages", "fires", "actual_customers_out"}


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)


@pytest.fixture(scope="module")
def state():
    return LAState(EID, T_UTC)


def _keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from _keys(v)


def test_schema(state):
    b = build_brief(state)
    assert set(b) == KEYS
    assert set(b["fire"]) == {"line_alarm", "region_index", "region_alarm", "n_over_alarm", "shut_off_candidates", "patrol_corridors"}
    assert set(b["grid"]) == {"load_mw", "unserved_mw", "n_violations", "overloaded", "critical_on_backup"}
    assert 1 <= len(b["headlines"]) <= 5


def test_numbers_copied_from_replay(state):
    assert build_brief(state)["fire"]["n_over_alarm"] == RP.hour(EID, t=T_UTC)["n_over_alarm"]


def test_no_hindsight(state):
    assert not HINDSIGHT & set(_keys(build_brief(state)))


def test_small_and_cached(state):
    b = build_brief(state)
    assert len(brief_text(b)) < 6000
    assert build_brief(state) is b


def test_tool_returns_brief(state):
    tool = make_tools(state, lambda *a: None, "fire_agent", ["get_situation_brief"])[0]
    out = tool()
    assert json.loads(out) == json.loads(brief_text(build_brief(state)))
    assert out == brief_text(build_brief(state))


def test_mock_output_carries_brief(state):
    lines = []
    out = run_mock(state, lambda a, m: lines.append((a, m)), "test", pace=0)
    assert out["brief"]["headlines"]
    assert any(m.startswith("situation brief:") for _, m in lines)


def test_la_prompts_carry_brief(state):
    lines = []
    subagents, prompt = T._la_prompts(state, lambda a, m: lines.append((a, m)))
    text = brief_text(build_brief(state))
    assert {s["name"] for s in subagents} == set(T.LA_SUBAGENTS)
    for s in subagents:
        assert text in s["system_prompt"] and "Situation brief" in s["system_prompt"]
    assert text in prompt
    assert lines == [("coordinator", "situation brief: " + build_brief(state)["headlines"][0])]
    # the synthetic path is unchanged
    assert all("Situation brief" not in s["system_prompt"] for s in T._subagent_specs(state, lambda *a: None, T.SUBAGENTS))


def test_synthetic_state_gets_brief():
    from backend.state import STATE

    b = build_brief(STATE)
    assert set(b) == KEYS and b["headlines"]
    assert len(brief_text(b)) < 6000
