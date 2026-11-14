"""Mock planner + API flow (no API key needed)."""
import os

os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["MOCK_PACE"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from backend.agents.mock_planner import run_mock  # noqa: E402
from backend.agents.tools import JobLog  # noqa: E402
from backend.app import app  # noqa: E402
from backend.state import GridState  # noqa: E402


def test_mock_planner_wind_storm_with_open_valley_line():
    s = GridState()
    s.set_scenario("Wind Storm")
    s.toggle_line("V4")
    log = JobLog()
    out = run_mock(s, log, pace=0)
    assert not out["no_safe_option"]
    p1 = out["plans"][0]
    assert p1["result"]["is_safe"] and len(p1["actions"]) <= 3
    assert any("REJECTED" in e["message"] for e in log.entries)


def test_mock_planner_every_scenario_has_safe_plan_or_nothing_to_do():
    for sc in ["Normal", "Heat Wave", "Wind Storm", "Coastal Flood"]:
        s = GridState()
        s.set_scenario(sc)
        out = run_mock(s, JobLog(), pace=0)
        assert not out["no_safe_option"], sc


def test_api_demo_flow():
    import time

    c = TestClient(app)
    assert c.post("/reset").status_code == 200
    st = c.post("/scenario", json={"name": "Wind Storm"}).json()
    assert st["sim"]["violations"]
    st = c.post("/lines/V4/toggle").json()
    assert st["lines"][7]["status"] == "open"
    rep = c.post("/reports", json={"text": "wire down on the ridge road, sparking"}).json()["report"]
    assert rep["line"]
    job = c.post("/copilot/run").json()["job_id"]
    for _ in range(100):
        j = c.get(f"/copilot/{job}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    pid = j["output"]["plans"][0]["id"]
    res = c.post(f"/plans/{pid}/approve").json()
    assert "Treat all downed wires as live" in res["bulletin"]["text"]
    assert res["state"]["sim"]["is_safe"]
    assert c.get("/bulletin").json()["plan_id"] == pid
