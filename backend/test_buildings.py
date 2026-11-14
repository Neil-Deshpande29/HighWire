"""Buildings behind each substation: what kind they are, and how that ranks which lines to cut. Offline; no keys."""
import json
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.agents.tools import verify_output  # noqa: E402
from backend.la import buildings as B  # noqa: E402
from backend.la import data as D  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402
from backend.la.state import LAState  # noqa: E402

T_WIND = "2025-01-08T21:00:00+00:00"  # E3, 9 lines over the fire alarm


@pytest.fixture(scope="module")
def grid():
    return LAGrid()


@pytest.fixture(scope="module")
def wind(grid):
    return LAState("E3", T_WIND, grid=grid)


def test_every_site_at_a_substation_is_kept():
    """B398 feeds four water plants and a hospital. Keeping one site per substation used to drop the hospital."""
    rows = B.by_bus()["B398"]
    assert Counter(r["kind"] for r in rows) == Counter(c["type"] for c in D.grid()["critical_sites"] if c["bus"] == "B398")
    assert "Adventist Health Simi Valley" in [r["name"] for r in rows]


def test_impact_counts_sites_not_substations():
    buses = ["B386", "B398", "B506"]
    sites = [c for c in D.grid()["critical_sites"] if c["bus"] in buses]
    impact = B.outage_impact(buses)
    assert impact["hospitals"] == sum(c["type"] == "hospital" for c in sites) >= 2
    assert impact["by_kind"]["water"] == sum(c["type"] == "water" for c in sites)
    assert {"Adventist Health Simi Valley", "UCLA West Hills Medical Center"} <= {s["name"] for s in impact["sites"]}
    assert all(s["backup_hours"] for s in impact["sites"] if s["kind"] == "hospital")  # each site keeps its own backup hours


def test_no_buildings_no_impact():
    assert B.outage_impact([]) == {"by_kind": {}, "score": 0, "hospitals": 0, "sites": [], "n": 0}


def test_a_hospital_outweighs_any_number_of_offices_we_would_plausibly_see():
    hospital = B.impact_of([{"name": "h", "kind": "hospital", "bus": "B1"}])
    offices = B.impact_of([{"name": f"o{k}", "kind": "office", "bus": "B1"} for k in range(5)])
    assert hospital["score"] > offices["score"] > 0
    assert B.WEIGHTS["hospital"] > B.WEIGHTS["water"] >= B.WEIGHTS["medical"] > B.WEIGHTS["office"]


def test_costar_property_rows_use_the_same_seam(tmp_path, monkeypatch, grid):
    """A CoStar export is a list of property rows. Each is given a kind and the nearest substation; nothing else changes."""
    b = grid.buses[0]
    rows = [
        {"id": "p1", "name": "Synthetic Tower", "property_type": "Office", "lat": b["lat"] + 0.001, "lon": b["lon"]},
        {"id": "p2", "name": "Synthetic Cold Store", "property_type": "Industrial", "secondary_type": "Refrigeration/Cold Storage",
         "lat": b["lat"], "lon": b["lon"] + 0.001},
        {"id": "p3", "name": "Synthetic Clinic", "property_type": "Health Care", "lat": b["lat"], "lon": b["lon"]},
        {"id": "p4", "name": "No location", "property_type": "Retail"},
    ]
    f = tmp_path / "buildings.json"
    f.write_text(json.dumps(rows))
    monkeypatch.setattr(B, "COSTAR_FILE", f)
    B.clear_cache()
    try:
        mine = {r["name"]: r for r in B.by_bus()[b["id"]] if r["source"] == "costar"}
        assert {n: r["kind"] for n, r in mine.items()} == {"Synthetic Tower": "office", "Synthetic Cold Store": "cold_storage",
                                                           "Synthetic Clinic": "medical"}
        assert "costar" in B.sources() and "osm" in B.sources()
        assert B.outage_impact([b["id"]])["by_kind"]["office"] == 1
    finally:
        monkeypatch.undo()
        B.clear_cache()
    assert B.sources() == ["osm"]


def test_simulate_says_which_buildings_lose_power(wind):
    flagged = [v.split(":")[0].replace("Line ", "") for v in wind.sim["violations"] if "fire risk" in v]
    sim = wind.simulate([{"type": "deenergize_lines", "target": "flagged", "targets": flagged}])
    out = sim["totals"]["buildings_out"]
    dark = sim["totals"]["critical_on_backup"]
    assert out["hospitals"] == sum(c["type"] == "hospital" for c in D.grid()["critical_sites"] if c["bus"] in dark) >= 2
    assert "Adventist Health Simi Valley" in [s["name"] for s in out["sites"]]
    assert wind.simulate([])["totals"]["buildings_out"]["n"] == 0  # nothing switched, nothing dark


def test_cutting_one_line_names_what_it_feeds(wind):
    """L249 feeds B398. The operator should see the hospital behind it before deciding, not only a water plant."""
    impact = wind.cut_impact("L249")
    assert impact["hospitals"] >= 1 and "Adventist Health Simi Valley" in [s["name"] for s in impact["sites"]]
    assert wind.cut_impact("L249") is impact  # cached per state: the list on screen asks for it on every refresh


def test_fire_lines_carry_the_cut_impact(wind):
    rows, _, n = wind.fire_lines()
    assert n == 9 and all("if_cut" in r for r in rows[:8])
    assert all(set(r["if_cut"]) >= {"hospitals", "score", "by_kind"} for r in rows[:8])


def test_plans_that_spare_critical_buildings_rank_first(grid):
    """Two safe plans: the one that leaves a hospital on backup ranks second, even though it cuts less load."""
    calm = LAState("E3", "2025-01-06T08:00:00+00:00", grid=grid)
    assert calm.sim["is_safe"]
    cuts = {lid: calm.cut_impact(lid) for lid in calm.lines}  # topology only, so the whole grid is quick to scan
    safe_mw = lambda lid: (lambda r: r["totals"]["unserved_mw"] if r["is_safe"] else None)(calm.simulate([{"type": "open_line", "target": lid}]))  # noqa: E731
    hospital_cuts = sorted((l for l, c in cuts.items() if c["hospitals"] and c["customers"]), key=lambda l: cuts[l]["customers"])
    free_cuts = sorted((l for l, c in cuts.items() if c["score"] == 0 and c["customers"]), key=lambda l: -cuts[l]["customers"])
    with_hospital = next(((l, mw) for l in hospital_cuts[:10] if (mw := safe_mw(l))), None)
    without = next(((l, mw) for l in free_cuts[:10] if (mw := safe_mw(l)) and with_hospital and mw > with_hospital[1]), None)
    assert with_hospital and without, "need a safe hospital-free cut that sheds more load than a safe cut behind a hospital"
    plans = [{"id": "A", "title": "a", "steps": [{"action": {"type": "open_line", "target": with_hospital[0]}, "why": "", "raised_by": []}]},
             {"id": "B", "title": "b", "steps": [{"action": {"type": "open_line", "target": without[0]}, "why": "", "raised_by": []}]}]
    out = verify_output(calm, {"plans": plans})
    assert [p["actions"][0]["target"] for p in out["plans"]] == [without[0], with_hospital[0]]
    assert out["plans"][0]["result"]["buildings_out"]["score"] == 0 < out["plans"][1]["result"]["buildings_out"]["score"]
    assert out["plans"][1]["result"]["hospitals_on_backup"] >= 1
