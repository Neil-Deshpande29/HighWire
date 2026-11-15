"""The risk watch (when to call the agents), approved switch-offs that carry forward in time, and the run's overall
confidence. Offline; no keys; no text is ever sent."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import notify as N  # noqa: E402
from backend import routes_la as R  # noqa: E402
from backend.agents import synthesis  # noqa: E402
from backend.agents.mock_planner import run_mock  # noqa: E402
from backend.agents.tools import JobLog  # noqa: E402
from backend.la import replay as RP  # noqa: E402
from backend.la import watch as W  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402
from backend.la.state import LAState  # noqa: E402

CALM, RISING, STORM, LATER = (f"2018-11-08T{h}:00:00+00:00" for h in ("08", "15", "19", "22"))  # Woolsey day, UTC


@pytest.fixture(scope="module")
def grid():
    return LAGrid()


def test_the_watch_is_green_then_red_before_any_line_crosses_its_alarm():
    calm, rising, storm = W.at("E1", CALM), W.at("E1", RISING), W.at("E1", STORM)
    assert calm["level"] == "green" and calm["reasons"] == [] and calm["score"] < calm["yellow_from"]
    assert storm["level"] == "red" and storm["score"] >= storm["red_from"] and storm["reasons"]
    # red is early: the hour the watch first turns red, no line is over its fire alarm yet
    n = len(RP.load("E1")["time"])
    first_red = next(k for k in range(n) if W.at("E1", i=k)["level"] == "red")
    assert RP.hour("E1", i=first_red)["n_over_alarm"] == 0 and RP.hour("E1", i=11)["n_over_alarm"] > 0 and first_red < 11
    assert rising["score"] >= calm["score"]
    assert {c["name"] for c in storm["checks"]} == {"fire", "loading", "storm", "wind", "heat"}
    assert storm["checks"] == sorted(storm["checks"], key=lambda c: -c["counts"])


def test_a_rising_check_counts_for_where_it_is_heading_and_only_the_past_is_used():
    storm = W.at("E1", STORM)
    fire = next(c for c in storm["checks"] if c["name"] == "fire")
    assert fire["per_hour"] > W.RISING and fire["heading"] == pytest.approx(fire["now"] + fire["per_hour"] * W.LOOKAHEAD_H, abs=0.002)
    assert fire["counts"] == max(fire["now"], fire["heading"]) and "rising" in storm["reasons"][0]
    d = RP.load("E1")
    h = RP._hour_index(d, STORM, None)
    later = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in d.items()}
    for k in ("line_risk", "loading", "line_gust", "wx_temp", "storm_index"):
        later[k][h + 1:] = 0  # wipe every later hour: the watch for this hour must not change
    try:
        RP.load.cache_clear()
        import unittest.mock as mock

        with mock.patch.object(RP, "load", lambda eid: later):
            assert W.at("E1", STORM) == storm
    finally:
        RP.load.cache_clear()
    assert "fire risk on the riskiest line up" in W.trend_line("E1", STORM)  # the agents are told how fast it is moving


def test_the_hour_the_screen_fetches_carries_the_watch(monkeypatch):
    monkeypatch.setitem(R.LA, "state", None)
    h = R.replay("E1", i=11)
    assert h["watch"]["level"] == "red" and h["standing"] == {"opened": []}
    assert R.replay("E1", i=0)["watch"]["level"] == "green"


def test_an_approved_switch_off_stays_in_force_as_the_clock_moves_on(grid, monkeypatch):
    from backend.la import authority as A
    from backend.la import record as REC

    st = LAState("E1", STORM, grid=grid)
    out = run_mock(st, JobLog(), pace=0)
    st.plans, st.plan_status = {p["id"]: p for p in out["plans"]}, {p["id"]: "pending" for p in out["plans"]}
    pid = next(p["id"] for p in out["plans"] if p["result"]["is_safe"])
    role = "supervisor" if A.of_result(st.plans[pid]["result"])["level"] == "supervisor" else "operator"
    monkeypatch.setitem(R.STANDING, "event", None)
    monkeypatch.setitem(R.STANDING, "lines", {})
    R.LA["state"] = st
    REC.clear()
    try:
        R.la_approve(pid, R.LARoleIn(role=role))
        off = sorted(st.operator_opened)
        assert off and sorted(R.replay("E1", t=LATER)["standing"]["opened"]) == off  # three hours on: still off
        assert R.replay("E1", t=RISING)["standing"]["opened"] == []  # but not before the approval
        assert R.replay("E3", i=5)["standing"]["opened"] == []  # and not in another scenario
        # the agents called at the later hour plan on the grid as it stands
        r = R.la_copilot_run(R.LARunIn(event="E1", t=LATER))
        later = R.LA["state"]
        assert r["study"] is False and sorted(later.operator_opened) == off and str(later.t_utc) != str(st.t_utc)
        assert sorted(later.view()["opened"]) == off
        R.la_reset()
        assert R.replay("E1", t=LATER)["standing"]["opened"] == []
    finally:
        R.LA["state"] = None
        R.STANDING.update(event=None, lines={})
        REC.clear()


def test_red_sends_one_text_when_the_agents_are_called(grid, monkeypatch):
    sent = []
    for name, value in {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "token", "TWILIO_FROM_NUMBER": "+15005550006",
                        "ALERT_PHONE": "+15005550009"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(N, "_twilio_send", lambda message, to: sent.append(message) or "SM1")
    monkeypatch.setattr(N, "SYNC", True)
    storm = LAState("E1", STORM, grid=grid)
    e = N.on_red(storm, W.at("E1", STORM))
    assert e and sent[0].startswith("Rewire: the risk watch is RED (Thu 11 AM). ") and "The agents are working on a plan" in sent[0]
    assert N.on_red(storm, W.at("E1", STORM)) is None and len(sent) == 1  # calling the agents again does not text again
    assert N.on_red(LAState("E1", CALM, grid=grid), W.at("E1", CALM)) is None  # green: no text
    assert len(N.on_hour(storm)) == 2 and len(sent) == 3  # and at most the worst line per condition on top of it


def test_overall_confidence_is_high_when_trouble_is_found_and_drops_for_a_possible_miss(grid):
    """Finding trouble and saying so is not marked down, however much switching the fix takes. What costs confidence is
    a possible missed or understated problem, and thin or disagreeing evidence."""
    out = run_mock(LAState("E1", STORM, grid=grid), JobLog(), pace=0)
    c = synthesis.confidence(out)
    assert out["do_nothing"]["violations"] and len(out["plans"][0]["actions"][0].get("targets", [])) >= 10  # a big fix
    assert c["score"] >= 0.9 and c["level"] == "high"
    assert synthesis.build(out, [])["confidence"] == c
    # across scenarios the average is in the nineties; the lowest is the hour that reads as fine while 27 Red Flag
    # warnings are up and a station measured stronger gusts than the model: the possible miss
    scores = [synthesis.confidence(run_mock(LAState(e, t, grid=grid), JobLog(), pace=0))["score"]
              for e, t in (("E1", STORM), ("E3", "2025-01-08T02:00:00+00:00"), ("H1", "2020-08-14T21:00:00+00:00"), ("E1", CALM))]
    assert sum(scores) / len(scores) >= 0.9 and scores.index(min(scores)) == 1 and min(scores) < 0.8

    understated = {**out, "agent_findings": {"wind_agent": [{"issue": "measured gusts stronger than the model's", "severity": 3}]}}
    assert synthesis.confidence(understated)["score"] == pytest.approx(c["score"] - 0.10, abs=0.001)
    best = out["plans"][0]
    unsafe = synthesis.confidence({**out, "plans": [{**best, "result": {**best["result"], "is_safe": False}}]})
    assert unsafe["score"] == pytest.approx(c["score"] - 0.08, abs=0.001) and "no plan passes the physics check yet" in unsafe["reasons"]
    # the costly mistake is a miss: an all-clear while a Red Flag warning is up is trusted less than a found problem
    all_clear = {"plans": [], "do_nothing": {"violations": []}, "agent_findings": {}}
    assert synthesis.confidence(all_clear) == {"score": 0.97, "level": "high", "reasons": []}
    warned = synthesis.confidence({**all_clear, "brief": {"weather": {"n_redflag_active": 4}}})
    assert warned["score"] == 0.87 and warned["reasons"] == ["the grid reads as within limits while warning signs are up"]
    assert warned["score"] < c["score"]
