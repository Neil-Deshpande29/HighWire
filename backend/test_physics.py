"""Physics sanity checks. Run: python -m pytest backend"""
import copy

from backend import scenarios
from backend.physics import conductor_temp, simulate
from backend.state import GridState


def fresh(scenario="Normal"):
    s = GridState()
    s.set_scenario(scenario)
    return s


def test_higher_current_raises_temperature():
    lo = conductor_temp(200, 25, 2.0, 800, 0)
    hi = conductor_temp(700, 25, 2.0, 800, 0)
    assert hi > lo + 5


def test_low_wind_high_elevation_runs_hotter_than_sea_level():
    sea = conductor_temp(500, 25, 3.0, 800, 0)
    ridge = conductor_temp(500, 25, 1.0, 800, 1200)
    same_wind_ridge = conductor_temp(500, 25, 3.0, 800, 1200)
    assert ridge > sea
    assert same_wind_ridge > sea  # thin air alone cools less


def test_opening_loop_line_reroutes_flow():
    s = fresh()
    before = s.sim["lines"]
    after = simulate(s, [{"type": "open_line", "target": "V4"}])["lines"]
    assert after["V4"]["flow_mw"] == 0
    assert abs(after["C3"]["flow_mw"]) > abs(before["C3"]["flow_mw"]) + 5
    assert sum(b["unserved_mw"] for b in simulate(s, [{"type": "open_line", "target": "V4"}])["buses"].values()) == 0


def test_heat_wave_has_ridge_clearance_violation():
    s = fresh("Heat Wave")
    ridge = [v for v in s.sim["violations"] if "clearance" in v and s.lines[v.split()[1].rstrip(":")]["zone"] == "ridge"]
    assert ridge, s.sim["violations"]


def test_normal_is_safe_and_green():
    s = fresh()
    assert s.sim["is_safe"]
    assert all(l["loading_pct"] < 70 for l in s.sim["lines"].values())


def test_wind_storm_flags_ridge_and_corroborates_one_report():
    s = fresh("Wind Storm")
    assert any(v.startswith("Line R") and "wind blowout" in v for v in s.sim["violations"])
    reps = s.reports_view()
    assert len(reps) == 3 and sum(r["corroborated"] for r in reps) == 1


def test_coastal_flood_flags_energized_flooded_bus():
    s = fresh("Coastal Flood")
    assert any("flooded" in v for v in s.sim["violations"])
    r = simulate(s, [{"type": "deenergize_bus", "target": "B9"}, {"type": "deenergize_bus", "target": "B11"}])
    assert r["is_safe"] and not r["buses"]["B10"]["on_backup"]


def test_simulate_does_not_mutate_state():
    s = fresh("Wind Storm")
    snap = copy.deepcopy(s.snapshot())
    simulate(s, [{"type": "open_line", "target": "R2"}, {"type": "shed_load", "target": "B7", "mw": 20}])
    assert s.snapshot() == snap


def test_all_scenarios_run():
    for name in scenarios.SCENARIOS:
        assert "totals" in fresh(name).sim
