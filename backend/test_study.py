"""UI-redesign backend additions: /la/simulate, the limit_line derate, Study condition overrides, and Study co-pilot runs.
None of these may change a model or mutate the Operate state."""
import os
import warnings

import numpy as np
import pytest

os.environ["MOCK_PACE"] = "0"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import app as A  # noqa: E402
from backend.la import data as D  # noqa: E402
from backend.la import engine as E  # noqa: E402
from backend.la.state import LAState  # noqa: E402

warnings.filterwarnings("ignore")
T_WIND = "2025-01-09T22:00:00Z"  # E3, fire-risk peak hour: 30+ lines above the fire alarm
T_HEAT = "2020-08-18T20:00:00Z"  # H1, peak line loading


@pytest.fixture(scope="module")
def grid():
    return E.LAGrid()


@pytest.fixture(scope="module")
def client(grid):
    A.LA["grid"] = grid
    return TestClient(A.app)


def test_limit_line_caps_rating(grid):
    st = LAState("H1", T_HEAT, grid=grid)
    base = st.sim["lines"]
    lid = max(base, key=lambda k: base[k]["loading_pct"] if base[k]["energized"] else -1)
    pct = 0.5 * base[lid]["loading_pct"]  # cap below today's flow -> must be flagged against the cap
    r = st.simulate([{"type": "limit_line", "target": lid, "pct": pct}])
    assert r["lines"][lid]["loading_pct"] == pytest.approx(base[lid]["loading_pct"] * 100 / pct, rel=0.02)
    assert r["lines"][lid]["cap_pct"] == pytest.approx(pct)
    assert any(lid in v and "operator limit" in v for v in r["violations"])
    # flows themselves are unchanged (a derate is a limit, not a redispatch) and the state is untouched
    assert r["lines"][lid]["flow_mw"] == base[lid]["flow_mw"]
    assert st.caps == {} and st.sim["lines"][lid]["loading_pct"] == base[lid]["loading_pct"]
    bad = st.simulate([{"type": "limit_line", "target": lid, "pct": 0}])
    assert not bad["is_safe"] and any("pct" in v for v in bad["violations"])


def test_conditions_scale_inputs_without_mutating(grid):
    st = LAState("E3", T_WIND, grid=grid)
    alarm = D.calibration()["fire"]["line_alarm"]
    n_hot = lambda s: sum(l["energized"] and l["fire_risk"] >= alarm for l in s.sim["lines"].values())  # noqa: E731
    risk_sum = lambda s: sum(l["fire_risk"] for l in s.sim["lines"].values())  # noqa: E731
    before = (n_hot(st), risk_sum(st), st.sim["totals"]["load_mw"], st.hour_idx, st.ldwp)

    windier = st.with_conditions({"gust_pct": 30})
    assert risk_sum(windier) > risk_sum(st) and n_hot(windier) >= n_hot(st)
    wetter = st.with_conditions({"rh_pct": 100})
    assert risk_sum(wetter) < risk_sum(st)
    hotter = st.with_conditions({"temp_c": 10})
    t0 = np.mean([l["temp_c"] for l in st.sim["lines"].values()])
    assert np.mean([l["temp_c"] for l in hotter.sim["lines"].values()]) > t0 + 5
    busier = st.with_conditions({"demand_pct": 20})
    assert busier.sim["totals"]["load_mw"] == pytest.approx(st.sim["totals"]["load_mw"] * 1.2, rel=0.01)
    assert "STUDY" in busier.scenario
    # Operate state untouched; zero conditions = identical physics
    assert (n_hot(st), risk_sum(st), st.sim["totals"]["load_mw"], st.hour_idx, st.ldwp) == before
    same = st.with_conditions({"gust_pct": 0, "rh_pct": 0})
    assert risk_sum(same) == pytest.approx(risk_sum(st))


def test_simulate_endpoint_returns_plan_result_and_leaves_operate_alone(client, grid):
    A.LA["state"] = LAState("E3", T_WIND, grid=grid)
    operate_before = A.LA["state"].view()
    base = client.post("/la/simulate", json={"event": "E3", "t": T_WIND}).json()
    for k in ("is_safe", "unserved_mw", "customers_out", "critical_on_backup", "violations", "watch", "lines", "situation"):
        assert k in base
    nl = len(grid.branches)
    assert len(base["lines"]["risk"]) == nl and len(base["lines"]["energized"]) == nl
    assert base["situation"]["n_high_risk"] > 0 and base["situation"]["people_downwind"] > 0
    hot = base["top_lines"][0]["id"]
    off = client.post("/la/simulate", json={"event": "E3", "t": T_WIND, "actions": [{"type": "open_line", "target": hot}]}).json()
    k = [b["id"] for b in grid.branches].index(hot)
    assert off["lines"]["energized"][k] is False and off["lines"]["risk"][k] == 0
    assert off["situation"]["n_high_risk"] == base["situation"]["n_high_risk"] - 1
    windy = client.post("/la/simulate", json={"event": "E3", "t": T_WIND, "conditions": {"gust_pct": 20}}).json()
    assert windy["situation"]["n_high_risk"] >= base["situation"]["n_high_risk"]
    assert windy["situation"]["max_gust"] == pytest.approx(base["situation"]["max_gust"] * 1.2, rel=0.01)
    assert A.LA["state"].view() == operate_before
    assert client.post("/la/simulate", json={"event": "NOPE"}).status_code == 404


def test_copilot_run_on_study_state(client, grid):
    A.LA["state"] = LAState("E3", T_WIND, grid=grid)
    operate = A.LA["state"]
    lid = next(iter(operate.lines))
    r = client.post("/la/copilot/run", json={"event": "E3", "t": T_WIND, "actions": [{"type": "open_line", "target": lid}],
                                             "conditions": {"gust_pct": 20}}).json()
    assert r["study"] is True and lid in r["state"]["opened"]
    assert A.LA["state"] is operate and lid not in operate.operator_opened  # Operate untouched
    study = A.LA["study"]
    assert study.conditions["gust_pct"] == 20 and "STUDY" in study.scenario
    plain = client.post("/la/copilot/run", json={"event": "E3", "t": T_WIND}).json()
    assert plain["study"] is False and A.LA["state"] is not operate


def test_fire_checks_name_the_igniting_substation(client):
    s = client.get("/la/series/E1").json()
    assert len(s["n_high_risk"]) == len(s["t_utc"]) and max(s["n_high_risk"]) > 0
    chk = {c["fire"]: c for c in s["fire_checks"]}["Woolsey Fire"]
    assert chk["first_flag_utc"] <= chk["ignition_utc"]  # flagged no later than ignition
    sub = chk["substation"]
    assert sub and sub["name"].lower() in chk["facility"].lower()  # read from the official facility text
    # a plan that switches off every flagged sub-transmission line at the first flag leaves that substation unsupplied
    from backend.la.state import LAState

    st = LAState("E1", chk["first_flag_utc"])
    alarm = __import__("backend.la.data", fromlist=["x"]).calibration()["fire"]["line_alarm"]
    hot = [l for l, v in st.sim["lines"].items() if v["energized"] and v["fire_risk"] >= alarm and st.lines[l]["kv"] < 180 and not st.lines[l]["cable"]]
    sim = st.simulate([{"type": "deenergize_lines", "target": "flagged", "targets": hot}])
    assert not sim["buses"][sub["id"]]["energized"]
