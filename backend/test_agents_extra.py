"""Operator feedback, the synthesis graph, the Buildings agent, Vexcel tree counts. Offline; no keys, no network."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import routes_la as R  # noqa: E402
from backend.agents import synthesis  # noqa: E402
from backend.agents.mock_planner import run_mock  # noqa: E402
from backend.agents.tools import JobLog, make_tools  # noqa: E402
from backend.la import feedback as F  # noqa: E402
from backend.la import record as REC  # noqa: E402
from backend.la import trees as T  # noqa: E402
from backend.la import vexcel as V  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402
from backend.la.state import LAState  # noqa: E402

T_WOOLSEY = "2018-11-08T19:00:00+00:00"


@pytest.fixture(scope="module")
def grid():
    return LAGrid()


@pytest.fixture()
def no_feedback(monkeypatch, tmp_path):
    monkeypatch.setattr(F, "FILE", tmp_path / "operator_feedback.json")
    return tmp_path


def _run(grid):
    st = LAState("E1", T_WOOLSEY, grid=grid)
    log = JobLog()
    return st, run_mock(st, log, pace=0), log


# ---------------------------------------------------------------- the Buildings agent
def test_buildings_agent_ranks_what_each_line_feeds(grid, no_feedback):
    _, out, _ = _run(grid)
    found = out["agent_findings"]["buildings_agent"]
    reach = [f for f in found if f["issue"].startswith("cutting it reaches tier")]
    assert reach and all(f["severity"] >= 2 for f in reach)
    assert any("tier 1" in f["issue"] and "hospital" in f["evidence"] for f in reach)  # a hospital is named as tier 1
    assert any(f["target"] == "cut first" for f in found) and any(f["target"] == "records" for f in found)


def test_building_impact_tool_gives_tiers_and_says_where_records_come_from(grid):
    st = LAState("E1", T_WOOLSEY, grid=grid)
    tool = next(t for t in make_tools(st, JobLog(), "buildings_agent", ["get_building_impact"]))
    out = json.loads(tool(json.dumps(["L558", "L226", "nope"])))
    assert out["lines"]["L558"]["tiers"][0]["tier"] == 1 and out["lines"]["L558"]["customers"] > 0
    assert out["lines"]["L226"]["tiers"] == [] and out["lines"]["nope"] == {"error": "unknown line"}
    assert [t["tier"] for t in out["ladder"]] == [1, 2, 3, 4, 5] and "from_costar" in out["records"]
    assert "error" in json.loads(tool("not json"))


# ---------------------------------------------------------------- feedback goes back to the coordinator
def test_feedback_is_stored_and_summarised(no_feedback):
    plan = {"id": "P1", "title": "t", "result": {"customers_out": 132500, "hospitals_on_backup": 3}}
    assert F.add(plan, [], "")["reasons"] == [] and F.entries() == []  # nothing said, nothing stored
    F.add(plan, ["too_many_customers", "made up", "too_many_customers"], "  keep   Chatsworth on  ", event="E1")
    F.add(plan, ["critical_site"], "")
    s = F.summary()
    assert s["n"] == 2 and [(r["id"], r["n"]) for r in s["reasons"]] == [("too_many_customers", 1), ("critical_site", 1)]
    assert s["notes"] == ["keep Chatsworth on"] and "Operator note: keep Chatsworth on" in F.as_text()
    F.clear()
    assert F.summary() == {"n": 0, "reasons": [], "notes": []} and F.as_text() == ""


def test_coordinator_acts_on_feedback_in_the_next_run(grid, no_feedback):
    _, before, _ = _run(grid)
    assert before["feedback_used"] == [] and not any(p.get("from_feedback") for p in before["plans"])
    F.add({"id": "P1", "result": {}}, ["too_many_customers"], "the valley hospitals worry me")
    _, after, log = _run(grid)
    used = after["feedback_used"]
    assert [u["reason"] for u in used] == ["too_many_customers", "note"] and used[0]["applied"].startswith("Added a smaller shut-off")
    smaller = next(p for p in after["plans"] if p.get("from_feedback"))
    best = next(p for p in after["plans"] if not p.get("from_feedback"))
    assert smaller["result"]["customers_out"] < best["result"]["customers_out"]  # it does what was asked
    assert not smaller["result"]["is_safe"]  # and the physics check still says what that costs: risky lines stay on
    assert any("operator feedback on record" in e["message"] for e in log.entries if e["agent"] == "coordinator")


def test_rejecting_through_the_route_stores_the_reason(grid, no_feedback):
    st, out, _ = _run(grid)
    st.plans, st.plan_status = {p["id"]: p for p in out["plans"]}, {p["id"]: "pending" for p in out["plans"]}
    R.LA["state"] = st
    REC.clear()
    try:
        got = R.la_reject("P1", R.LARoleIn(reasons=["critical_site"], note="ask the hospitals first"))
        assert got["plan_status"]["P1"] == "rejected" and got["feedback"]["n"] == 1
        entry = REC.entries()[-1]
        assert entry["kind"] == "rejected" and "hospital" in entry["text"] and "ask the hospitals first" in entry["text"]
        assert R.la_feedback()["notes"] == ["ask the hospitals first"] and "critical_site" in R.la_feedback()["choices"]
    finally:
        R.LA["state"] = None
        REC.clear()


# ---------------------------------------------------------------- how the team's work came together
def test_synthesis_graph_is_built_from_what_the_agents_produced(grid, no_feedback):
    _, out, log = _run(grid)
    g = synthesis.build(out, log.entries)
    by_id = {a["id"]: a for a in g["agents"]}
    assert set(by_id) == set(synthesis.NAMES)
    assert all(by_id[a]["findings"] == len(out["agent_findings"].get(a, [])) for a in by_id)
    assert by_id["fire_agent"]["raised_steps"] >= 1 and by_id["buildings_agent"]["findings"] >= 1
    assert g["coordinator"]["candidates"] == out["stats"]["candidates"] > 0
    assert g["physics"]["screened"] == out["stats"]["screened"] >= g["physics"]["safe"] > 0 and g["physics"]["verified"] >= len(out["plans"])
    assert [p["id"] for p in g["plans"]] == [p["id"] for p in out["plans"]] and g["plans"][0]["safe"]
    assert "fire_agent" in g["plans"][0]["raised_by"]
    switched = [frozenset(t for a in p["actions"] for t in (a.get("targets") or [a["target"]])) for p in out["plans"]]
    assert len(set(switched)) == len(switched) and [p["id"] for p in out["plans"]] == [f"P{k + 1}" for k in range(len(switched))]  # no twin plans


# ---------------------------------------------------------------- Vexcel: trees near the wires
def test_trees_are_counted_inside_the_corridor_and_only_once(monkeypatch, tmp_path):
    from site_agent import facts as SF

    monkeypatch.setattr(T, "DIR", tmp_path)
    T.line_trees.cache_clear()
    spots = SF.line_points("L558")
    on_line = spots[0]

    def detect(wkt, label, threshold):
        assert label == "tree"
        # one tree on the line (reported for every spot, as overlapping squares would), one tree 1 km away
        return [{"confidence": 0.7, "lat": on_line[0], "lon": on_line[1]}, {"confidence": 0.9, "lat": on_line[0] + 0.01, "lon": on_line[1] + 0.01}]

    got = T.count("L558", detect=detect)
    assert got["trees"] == 1 and got["spots"] == len(spots) and got["covered"]
    assert T.line_trees("L558") == {"trees": 1, "spots": len(spots), "corridor_m": T.CORRIDOR_M, "source": "Vexcel", "many": False}

    def refused(wkt, label, threshold):
        raise V.NotCovered("outside")

    assert not T.count("L220", detect=refused)["covered"]
    assert T.line_trees("L220") is None and T.line_trees("L1") is None  # not covered and never counted both read as unknown
    T.line_trees.cache_clear()


def test_vexcel_client_shapes_requests_like_the_quickstart(monkeypatch):
    sent = {}

    class Reply:
        status_code, ok, content = 200, True, b"jpeg"

        def json(self):
            return {"features": [{"properties": {"label": "tree", "detections": [
                {"imageId": "ortho-1", "confidence": 0.66, "bbox": "POLYGON ((-118.6 34.2, -118.5 34.2, -118.5 34.3, -118.6 34.3, -118.6 34.2))"}]}}]}

    def post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        return Reply()

    import requests

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setenv("VEXCEL_API_KEY", "test-key")
    assert V.ortho(V.rect_wkt(34.2, -118.7, 34.3, -118.6), 512, 620) == b"jpeg"
    assert sent["url"].endswith("/v2/ortho/extract") and sent["headers"] == {"X-API-KEY": "test-key"}
    assert sent["body"]["destination-dimensions"] == "512,620" and sent["body"]["srid"] == 4326 and sent["body"]["wkt"].startswith("POLYGON((-118.7 34.2,")
    found = V.detect(V.square_wkt(34.25, -118.55, 150), "tree")
    assert found == [{"confidence": 0.66, "lon": pytest.approx(-118.55), "lat": pytest.approx(34.25)}]
    assert sent["body"]["model-input"]["elements"] == [{"label": "tree", "model": "sam3", "detection-threshold": 0.5}]
    Reply.status_code, Reply.ok = 403, False
    with pytest.raises(V.NotCovered):
        V.ortho("POLYGON((0 0,1 0,1 1,0 1,0 0))")
    monkeypatch.delenv("VEXCEL_API_KEY")
    with pytest.raises(RuntimeError):
        V.ortho("POLYGON((0 0,1 0,1 1,0 1,0 0))")


def test_on_a_heat_day_the_planner_trims_demand_before_switching_an_overloaded_line_off(grid, no_feedback):
    st = LAState("H1", "2020-08-14T21:00:00+00:00", grid=grid)  # one line a hair over its rating
    over = [v for v in st.sim["violations"] if "loading" in v]
    assert over and not any("fire risk" in v for v in st.sim["violations"])
    out = run_mock(st.copy_for_planning(), JobLog(), pace=0)
    best = out["plans"][0]
    assert best["result"]["is_safe"] and not out.get("no_safe_option")
    kinds = {s["action"]["type"] for s in best["steps"]}
    assert "open_line" not in kinds and "shed_load" in kinds  # switching it off would drop its load on its neighbours
    assert "of its rating" in best["steps"][0]["why"] and "shutoff" not in best["steps"][0]["why"]
