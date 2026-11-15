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
    assert set(b["fire"]) == {"line_alarm", "region_index", "region_alarm", "n_over_alarm", "n_shut_off", "n_patrol",
                              "shut_off_candidates", "patrol_corridors"}
    assert set(b["storm"]) == {"index", "alarm", "above_alarm", "recorded", "lines_damage_likely"}
    assert set(b["grid"]) == {"load_mw", "unserved_mw", "n_violations", "n_overloaded", "overloaded", "critical_on_backup"}
    assert set(b["weather"]) == {"max_gust_ms", "min_rh_pct", "max_temp_c", "n_redflag_active", "redflag_zones_active", "redflag_recorded"}
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
    assert lines[0] == ("coordinator", "situation brief: " + build_brief(state)["headlines"][0])
    assert all(a == "coordinator" and m.startswith("trend over the last hours") for a, m in lines[1:])  # how fast things are moving
    # the synthetic path is unchanged
    assert all("Situation brief" not in s["system_prompt"] for s in T._subagent_specs(state, lambda *a: None, T.SUBAGENTS))


def test_synthetic_state_gets_brief():
    from backend.state import STATE

    b = build_brief(STATE)
    assert set(b) == KEYS and b["headlines"]
    assert len(brief_text(b)) < 6000


def test_brief_names_no_real_fire():
    """No hindsight: replaying a storm, the brief must not reveal which fires actually started (V5/V8 headlines do)."""
    from backend.la import data as D

    st = LAState("E3", "2025-01-07T18:00:00+00:00")
    text = brief_text(build_brief(st)).lower()
    for f in D.fires().values():
        assert f["name"].split()[0].lower() not in text, f["name"]


BUSY = ("E1", "2018-11-09T19:00:00+00:00")  # many lines over the alarm: every capped list is shorter than its true count
REDFLAG = ("E3", "2025-01-08T21:00:00+00:00")  # more Red Flag zones in force than the brief lists
NO_HOMES = ("E1", "2018-11-10T22:00:00+00:00")  # lines over the alarm, but no Census tract centre in any of their cones


def _tier_counts(st):
    """(shut-off candidates, bulk corridors) counted straight from the state's simulation, split as state.simulate does."""
    from backend.la import data as D
    from backend.la.state import SUBTRANSMISSION_MAX_KV

    alarm = D.calibration()["fire"]["line_alarm"]
    hot = [st.lines[k]["kv"] for k, v in st.sim["lines"].items() if v["energized"] and v["fire_risk"] >= alarm and not st.lines[k]["cable"]]
    return sum(kv < SUBTRANSMISSION_MAX_KV for kv in hot), sum(kv >= SUBTRANSMISSION_MAX_KV for kv in hot)


def test_headline_counts_are_true_counts():
    """The headline numbers are the counts in the physics, not the lengths of the capped lists under them."""
    st = LAState(*BUSY)
    b = build_brief(st)
    f = b["fire"]
    n_shut, n_patrol = _tier_counts(st)
    assert n_shut > len(f["shut_off_candidates"]) and n_patrol > len(f["patrol_corridors"])  # the caps bite at this hour
    assert (f["n_shut_off"], f["n_patrol"]) == (n_shut, n_patrol)
    assert f["n_patrol"] == len(st.sim["watch"])  # the state's own patrol list has one entry per bulk corridor
    assert f["n_shut_off"] + f["n_patrol"] == f["n_over_alarm"] == RP.hour(BUSY[0], t=BUSY[1])["n_over_alarm"]
    head = b["headlines"][0]
    assert f"{n_shut} sub-transmission shut-off candidates (top {len(f['shut_off_candidates'])} listed)" in head
    assert f"{n_patrol} bulk corridors to patrol (top {len(f['patrol_corridors'])} listed)" in head

    rf = build_brief(LAState(*REDFLAG))
    w = rf["weather"]
    assert w["n_redflag_active"] == len(RP.hour(REDFLAG[0], t=REDFLAG[1])["redflag_active"]) == 27
    assert len(w["redflag_zones_active"]) == 12
    assert any("27 Red Flag zone(s) in force (12 listed)" in h for h in rf["headlines"])
    assert "listed" not in rf["headlines"][0]  # 7 and 2 lines at this hour: nothing capped, nothing to qualify


def test_cache_follows_every_operator_action():
    """A brief built before a bus is de-energized or an import is redispatched must not be served after it."""
    from backend.la import data as D

    st = LAState(*REDFLAG)
    before = build_brief(st)
    bus = max((b for b in st.sim["buses"].values() if not st.buses[b["id"]]["critical"]), key=lambda b: b["served_mw"])["id"]
    st.apply_actions([{"type": "deenergize_bus", "target": bus}])
    after = build_brief(st)
    assert after is not before
    assert after["grid"]["unserved_mw"] == round(st.sim["totals"]["unserved_mw"], 0) > before["grid"]["unserved_mw"]

    st = LAState(*REDFLAG)
    before = build_brief(st)
    st.apply_actions([{"type": "dispatch_gen", "target": D.grid()["imports"][0]["id"], "mw": 0.0}])
    after = build_brief(st)
    assert st.is_modified() and after is not before
    assert after["grid"]["n_violations"] == st.sim["totals"]["violation_count"]


def test_synthetic_brief_follows_scenario():
    """The toy region keeps one state object across scenarios, so its brief must not be cached past a scenario change."""
    from backend import scenarios
    from backend.state import GridState

    st = GridState()
    first = build_brief(st)
    other = next(n for n in scenarios.SCENARIOS if n != st.scenario)
    st.set_scenario(other)
    b = build_brief(st)
    assert b is not first and b["scenario"] == other and b["headlines"][0].startswith(other)
    assert build_brief(st) is b
    assert set(b["fire"]) == set(build_brief(LAState(*REDFLAG))["fire"])


def test_study_brief_says_which_blocks_are_recorded():
    """Study conditions and switching change the fire and grid facts; the storm index and Red Flag zones stay the
    recorded hour's, and the brief has to say so instead of passing them off as the modified state's."""
    st = LAState(*BUSY)
    b0 = build_brief(st)
    assert b0["storm"]["recorded"] is False and b0["weather"]["redflag_recorded"] is False
    assert not any("recorded hour" in h for h in b0["headlines"])

    windy = st.with_conditions({"gust_pct": 50})
    switched = st.with_conditions(None)
    switched.apply_actions([{"type": "open_line", "target": b0["fire"]["shut_off_candidates"][0]["id"]}])
    for mod in (windy, switched):
        b = build_brief(mod)
        assert b["storm"]["recorded"] is True and b["weather"]["redflag_recorded"] is True
        assert (b["storm"]["index"], b["storm"]["alarm"]) == (b0["storm"]["index"], b0["storm"]["alarm"])  # no invented number
        assert b["weather"]["redflag_zones_active"] == b0["weather"]["redflag_zones_active"]
        assert "recorded hour" in next(h for h in b["headlines"] if h.startswith("Storm-damage index"))
        assert "recorded hour" in next(h for h in b["headlines"] if "Red Flag" in h)
    assert build_brief(windy)["weather"]["max_gust_ms"] > b0["weather"]["max_gust_ms"]  # the gust itself is the study's


def test_people_counts_say_what_they_measure():
    """A people count is the 2020 Census population in one line's 10 km downwind cone: say so, and never print "~0"."""
    import backend.agents.brief as B

    assert all(s in B.__doc__ for s in ("people_downwind", "10 km downwind cone", "mid point", "2020 Census"))
    busy = build_brief(LAState(*BUSY))
    listed = busy["fire"]["shut_off_candidates"] + busy["fire"]["patrol_corridors"]
    worst = max(listed, key=lambda x: x["people_downwind"])
    head = next(h for h in busy["headlines"] if "downwind" in h)
    assert worst["id"] in head and f"~{worst['people_downwind']:,}" in head
    assert "10 km downwind cone" in head and "2020 Census" in head

    empty = build_brief(LAState(*NO_HOMES))
    listed = empty["fire"]["shut_off_candidates"] + empty["fire"]["patrol_corridors"]
    assert listed and all(x["people_downwind"] == 0 for x in listed)
    head = next(h for h in empty["headlines"] if "downwind" in h)
    assert "no homes counted" in head.lower() and "10 km downwind cone" in head and "2020 Census" in head
    assert not any("~0" in h for h in empty["headlines"] + busy["headlines"])
