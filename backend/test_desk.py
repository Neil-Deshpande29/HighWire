"""The operator desk's backend: station sensors, building fire exposure, the risk register, who may approve, and the
decision record. Offline; no keys. The live station path runs on a fake fetcher."""
import os
import sys

import pandas as pd
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import routes_la as R  # noqa: E402
from backend.agents.mock_planner import run_mock  # noqa: E402
from backend.agents.tools import JobLog  # noqa: E402
from backend.la import authority as A  # noqa: E402
from backend.la import buildings as B  # noqa: E402
from backend.la import exposure as X  # noqa: E402
from backend.la import record as REC  # noqa: E402
from backend.la import risk  # noqa: E402
from backend.la import sensors as SN  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402
from backend.la.state import LAState  # noqa: E402

T_WOOLSEY = "2018-11-08T19:00:00+00:00"  # E1 at 11 AM PST: 30 lines over the fire alarm


@pytest.fixture(scope="module")
def grid():
    return LAGrid()


@pytest.fixture(scope="module")
def woolsey(grid):
    return LAState("E1", T_WOOLSEY, grid=grid)


# ---------------------------------------------------------------- weather stations
def test_recorded_stations_are_compared_with_the_model(woolsey):
    chk = SN.check(woolsey)
    assert chk["mode"] == "recorded" and chk["n"] == len(SN.STATIONS) == 8 and chk["reporting"] == 8
    assert "KPMD" not in [r["id"] for r in chk["stations"]]  # outside the weather grid, as in validation V2
    for r in chk["stations"]:
        assert set(r["measured"]) == set(r["model"]) == {"gust_ms", "wind_ms", "rh_pct", "temp_c"}
        assert r["gust_diff_ms"] == pytest.approx(r["model"]["gust_ms"] - r["measured"]["gust_ms"], abs=0.11)
        assert r["flag"] == ("model_low" if r["gust_diff_ms"] <= -SN.GUST_TOL_MS else "model_high" if r["gust_diff_ms"] >= SN.GUST_TOL_MS else "agrees")
    assert chk["level"] == ("warn" if chk["model_low"] or chk["drier"] else "ok")


def test_station_reports_never_come_from_after_the_hour(woolsey):
    """No hindsight: a replay hour may only read reports from the hour that ends at it."""
    end = pd.Timestamp(T_WOOLSEY)
    for o in SN.recorded(end).values():
        at = pd.Timestamp(o["at_utc"])
        assert end - pd.Timedelta(hours=1) < at <= end


def test_live_stations_flag_gusts_the_model_missed(grid):
    live = LAState("FX", None, grid=grid)
    now = live.t_utc.timestamp()
    fresh = pd.Timestamp(now - 600, unit="s", tz="UTC").isoformat()
    stale = pd.Timestamp(now - 6 * 3600, unit="s", tz="UTC").isoformat()

    def fetch(sid):
        if sid == "KVNY":  # far windier and drier than any forecast cell
            return {"at_utc": fresh, "gust_ms": 44.0, "wind_ms": 30.0, "rh_pct": 1.0, "temp_c": 30.0}
        if sid == "KBUR":  # a report too old to trust
            return {"at_utc": stale, "gust_ms": 44.0, "wind_ms": 30.0, "rh_pct": 5.0, "temp_c": 30.0}
        return None  # the station did not answer

    chk = SN.check(live, fetch=fetch, now=now)
    by_id = {r["id"]: r for r in chk["stations"]}
    assert chk["mode"] == "live" and chk["reporting"] == 1 and chk["level"] == "warn"
    assert by_id["KVNY"]["flag"] == "model_low" and by_id["KVNY"]["drier"] and chk["model_low"] == ["KVNY"]
    assert by_id["KBUR"]["flag"] == by_id["KLAX"]["flag"] == "no_report"
    assert SN.check(live, fetch=lambda sid: None, now=now)["level"] == "none"


def test_offline_live_check_asks_no_network(grid, monkeypatch):
    monkeypatch.setattr(SN, "_fetch_latest", lambda sid: pytest.fail("the planner's station check must not use the network"))
    monkeypatch.setitem(SN._live, "obs", {})
    chk = SN.check(LAState("FX", None, grid=grid), offline=True)
    assert chk["reporting"] == 0 and chk["level"] == "none"


# ---------------------------------------------------------------- the two sides of the decision
def test_fire_exposure_names_buildings_downwind_of_live_lines(woolsey):
    hot = woolsey.hot_points()
    ids = {woolsey.grid.branches[k]["id"] for k, *_ in hot}
    exp = X.exposed([(lat, lon, wf, woolsey.grid.branches[k]["id"]) for k, lat, lon, wf in hot])
    assert exp["hospitals"] >= 1 and exp["severity"] == "high" and exp["n"] == sum(exp["by_kind"].values())
    assert all(s["line"] in ids and 0 <= s["distance_km"] <= 10 for s in exp["sites"])
    assert exp["sites"] == sorted(exp["sites"], key=lambda s: (-B.WEIGHTS[s["kind"]], s["distance_km"], s["name"]))
    assert X.exposed([]) == {"by_kind": {}, "score": 0, "hospitals": 0, "n": 0, "critical": 0, "homes": 0, "severity": None, "sites": []}
    assert exp["critical"] == exp["n"] and exp["homes"] == 0  # the public list is all hospitals and water plants, no apartments


def test_simulation_carries_exposure_and_authority(woolsey):
    idle = R._sim_payload(woolsey, [])
    assert idle["fire_exposure"]["hospitals"] == idle["situation"]["hospitals_downwind"] >= 1  # one cone, one count
    assert idle["authority"]["level"] == "blocked"  # doing nothing fails the physics check
    flagged = [v.split(":")[0].replace("Line ", "") for v in woolsey.sim["violations"] if "fire risk" in v]
    cut = R._sim_payload(woolsey, [{"type": "deenergize_lines", "target": "flagged", "targets": flagged}])
    assert cut["is_safe"] and cut["situation"]["n_high_risk"] < idle["situation"]["n_high_risk"]
    assert cut["situation"]["people_downwind"] < idle["situation"]["people_downwind"]  # the benefit side of the table
    assert cut["authority"]["level"] == "supervisor" and cut["authority"]["reasons"]


def test_risk_register_keeps_likelihood_and_consequence_apart(woolsey):
    reg = risk.register(woolsey)
    rows = reg["rows"]
    assert reg["n_high"] == len(rows) == len(woolsey.hot_points()) and reg["n_elevated"] == sum(reg["matrix"]["elevated"].values())
    assert [r["index"] for r in rows] == sorted((r["index"] for r in rows), reverse=True) and all(r["index"] >= reg["alarm"] for r in rows)
    assert sorted(sum(reg["matrix"]["high"].values(), [])) == sorted(r["id"] for r in rows)  # every line in exactly one cell
    for r in rows:
        assert r["exposure_band"] == risk.exposure_band(r["downwind"])
        assert (r["if_cut"] is None) == (r["class"] == "bulk")  # import lines are patrolled, never offered for switching
    assert risk.exposure_band({"people": 10, "hospitals": 1, "water_plants": 0}) == "high"
    assert risk.exposure_band({"people": 6_000, "hospitals": 0, "water_plants": 0}) == "medium"
    assert risk.exposure_band({"people": 10, "hospitals": 0, "water_plants": 0}) == "low"


# ---------------------------------------------------------------- who may approve, and the record
def test_authority_levels():
    assert A.assess(False, 0, 0)["level"] == "blocked"
    assert A.assess(True, A.OPERATOR_MAX_CUSTOMERS, 0) == {"level": "operator", "reasons": [], "basis": A.BASIS}
    over = A.assess(True, A.OPERATOR_MAX_CUSTOMERS + 1, 2)
    assert over["level"] == "supervisor" and len(over["reasons"]) == 2
    assert A.of_result({"is_safe": True, "customers_out": 10, "buildings_out": {"hospitals": 1}})["level"] == "supervisor"


def test_supervisor_plans_need_the_supervisor_and_everything_is_recorded(woolsey, grid):
    st = LAState("E1", T_WOOLSEY, grid=grid)
    out = run_mock(st, JobLog(), pace=0)
    st.plans, st.plan_status = {p["id"]: p for p in out["plans"]}, {p["id"]: "pending" for p in out["plans"]}
    pid = next(p["id"] for p in out["plans"] if A.of_result(p["result"])["level"] == "supervisor")
    R.LA["state"] = st
    REC.clear()
    try:
        with pytest.raises(HTTPException) as refused:
            R.la_approve(pid)  # the operator alone
        assert refused.value.status_code == 403 and "supervisor" in refused.value.detail
        assert st.plan_status[pid] == "pending" and st.operator_opened == []  # nothing was switched
        assert R.la_escalate(pid)["plan_status"][pid] == "escalated"
        done = R.la_approve(pid, R.LARoleIn(role="supervisor"))
        assert done["state"]["plan_status"][pid] == "approved" and st.operator_opened
        with pytest.raises(HTTPException):
            R.la_approve(pid, R.LARoleIn(role="intern"))
        kinds = [(e["kind"], e["role"]) for e in R.la_record()["entries"]]
        assert kinds == [("escalated", "operator"), ("approved", "supervisor"), ("bulletin", "system")]
        assert [e["n"] for e in REC.entries()] == [1, 2, 3]
        assert [e["kind"] for e in R.la_reset()["entries"]] == ["reset"] and R.LA["state"] is None
    finally:
        R.LA["state"] = None
        REC.clear()


def test_wind_agent_reports_the_station_check(woolsey):
    out = run_mock(woolsey.copy_for_planning(), JobLog(), pace=0)
    station = [f for f in out["agent_findings"]["wind_agent"] if "station" in f["issue"] or "station" in f["evidence"]]
    assert station, "the wind agent says what the weather stations measured against the model"


def test_outage_cost_uses_the_published_rates_and_rounds_to_what_they_support():
    from backend.la import costs

    assert costs.outage_cost(0)["usd"] == {"8": 0, "24": 0}
    c = costs.outage_cost(100_000)
    per_8h = 0.875 * 26.62 + 0.125 * 7708.0  # one average customer, eight hours, one in eight a business
    assert c["usd"]["8"] == pytest.approx(100_000 * per_8h, rel=0.05) and c["usd"]["24"] > c["usd"]["8"]
    assert len(str(c["usd"]["8"]).rstrip("0")) <= 2  # two significant figures, no false precision
    assert c["business_share"] == costs.BUSINESS_SHARE and "LBNL" in c["source"]


def test_every_simulation_carries_its_cost(woolsey):
    flagged = [v.split(":")[0].replace("Line ", "") for v in woolsey.sim["violations"] if "fire risk" in v]
    cut = R._sim_payload(woolsey, [{"type": "deenergize_lines", "target": "flagged", "targets": flagged}])
    assert cut["cost"]["customers"] == cut["customers_out"] > 0 and cut["cost"]["usd"]["8"] > 0
    assert R._sim_payload(woolsey, [])["cost"]["usd"] == {"8": 0, "24": 0}


def test_a_line_just_under_the_alarm_is_never_shown_or_counted_as_over_it(woolsey):
    from backend.la import data as D

    assert D.shown_risk(0.29996) == 0.299 and D.shown_risk(0.3) == 0.3 and D.shown_risk(0.30049) == 0.3
    idle = R._sim_payload(woolsey, [])
    alarm = D.calibration()["fire"]["line_alarm"]
    flagged = len([v for v in woolsey.sim["violations"] if "fire risk" in v]) + len(woolsey.sim["watch"])
    cable = [b["cable"] for b in woolsey.grid.branches]
    shown = sum(on and r >= alarm and not c for r, on, c in zip(idle["lines"]["risk"], idle["lines"]["energized"], cable))
    assert idle["situation"]["n_high_risk"] == flagged == shown  # one count on the status box, the agents and the map


def test_a_cut_does_not_count_demand_that_was_already_shed(woolsey):
    """Switching a line off only takes away what is still being served behind it."""
    from backend.la.state import CUSTOMERS_PER_MW

    off = [{"type": "open_line", "target": "L5"}]
    behind = sorted(woolsey._cut_off(off) - woolsey._cut_off([]))
    whole = woolsey.cut_impact("L5")["customers"]
    assert whole == int(sum(woolsey.buses[b]["load_mw"] for b in behind) * CUSTOMERS_PER_MW) > 0
    half = [{"type": "shed_load", "target": b, "mw": woolsey.buses[b]["load_mw"] / 2} for b in behind]
    after = woolsey.cut_impact("L5", half)["customers"]
    assert after == pytest.approx(whole / 2, abs=2)
    extra = woolsey.simulate(half + off)["totals"]["customers_out"] - woolsey.simulate(half)["totals"]["customers_out"]
    assert after == pytest.approx(extra, abs=50)  # the same answer the power flow gives
    row = next(r for r in risk.register(woolsey, half)["rows"] if r["id"] == "L5")
    assert row["if_cut"]["customers"] == after  # the risk list uses the same sum
