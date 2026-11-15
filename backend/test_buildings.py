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
    empty = B.outage_impact([])
    assert {k: v for k, v in empty.items() if k != "ladder"} == {"by_kind": {}, "score": 0, "hospitals": 0, "sites": [], "n": 0, "tours": [], "n_tours": 0, "critical": 0,
                                                                  "homes": 0, "severity": None}
    assert [t["n"] for t in empty["ladder"]] == [0] * len(B.TIERS)


def test_priority_ladder_matches_the_weights():
    """Every kind sits on exactly one rung, and a higher rung never weighs less than a lower one."""
    kinds = [k for t in B.TIERS for k in t["kinds"]]
    assert sorted(kinds) == sorted(B.WEIGHTS) and [t["tier"] for t in B.TIERS] == [1, 2, 3, 4, 5]
    for above, below in zip(B.TIERS, B.TIERS[1:]):
        assert min(B.WEIGHTS[k] for k in above["kinds"]) >= max(B.WEIGHTS[k] for k in below["kinds"])
    assert B.tier_of("hospital") == 1 and B.tier_of("water") == 2 and B.tier_of("school") == 3 and B.tier_of("never heard of it") == 5
    filled = B.outage_impact(["B398"])["ladder"]  # four water plants and a hospital
    assert [(t["tier"], t["n"]) for t in filled[:2]] == [(1, 1), (2, 4)] and sum(t["n"] for t in filled) == 5
    info = B.summary()
    assert info["n"] == sum(t["n"] for t in info["ladder"]) == sum(info["by_kind"].values()) and "osm" in info["sources"]


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


def test_warning_levels_are_set_in_code():
    assert [B.severity(k) for k in ("hospital", "water", "emergency", "medical", "school", "office", "anything else")] == \
        ["high", "high", "high", "medium", "medium", "low", "low"]
    assert B.outage_impact([])["severity"] is None
    assert B.outage_impact(["B398"])["severity"] == "high"  # one hospital is enough, however many other buildings


def test_each_plan_step_names_the_five_buildings_that_matter_most(wind):
    flagged = [v.split(":")[0].replace("Line ", "") for v in wind.sim["violations"] if "fire risk" in v]
    steps = [{"action": {"type": "open_line", "target": "L220"}, "why": "", "raised_by": []},
             {"action": {"type": "deenergize_lines", "target": "flagged", "targets": flagged}, "why": "", "raised_by": []}]
    plan = verify_output(wind, {"plans": [{"id": "P1", "title": "t", "steps": steps}]})["plans"][0]
    first, second = (s["buildings"] for s in plan["steps"])
    assert first["n"] == 0 and first["severity"] is None and first["sites"] == []  # L220 alone cuts nothing off
    assert second["severity"] == "high" and 0 < len(second["sites"]) <= 5 < second["n"]
    assert [s["kind"] for s in second["sites"][:2]] == ["hospital", "hospital"]  # heaviest first, then by name
    assert second["sites"] == sorted(second["sites"], key=lambda s: (-B.WEIGHTS[s["kind"]], s["name"]))
    assert all(s["severity"] == B.severity(s["kind"]) for s in second["sites"])
    assert plan["result"]["buildings_out"]["n"] == first["n"] + second["n"]  # the steps add up to the plan


def test_costar_extract_is_read_by_column_name(tmp_path):
    """The extract's layout is not published: columns are found by name, rows outside the grid are dropped, and a
    file with no coordinates is skipped with a note instead of being guessed at."""
    from backend.la import costar_import as CI

    (tmp_path / "la.csv").write_text(
        "Property ID,Property Name,PropertyType,Secondary Type,Latitude,Longitude,RBA,Year Built\n"
        "1,Valley Medical Plaza,Health Care,Hospital,34.20,-118.45,250000,1988\n"
        "2,Sunset Tower,Office,,34.09,-118.38,90000,1972\n"
        "2,Sunset Tower (again),Office,,34.09,-118.38,90000,1972\n"
        "3,Houston Warehouse,Industrial,Warehouse,29.76,-95.37,400000,2001\n")
    (tmp_path / "leases.csv").write_text("LeaseID,Tenant,SF\n9,Acme,1000\n")
    rows, notes = CI.convert(tmp_path)
    assert [r["id"] for r in rows] == ["1", "2"]  # the duplicate id and the Houston row are gone
    assert rows[0] == {"id": "1", "name": "Valley Medical Plaza", "property_type": "Health Care", "secondary_type": "Hospital",
                       "lat": 34.20, "lon": -118.45, "size_sf": "250000", "year_built": "1988", "address": None}
    assert [B.kind_of(r["property_type"], r["secondary_type"]) for r in rows] == ["hospital", "office"]
    assert any("leases.csv: skipped, no latitude and longitude" in n for n in notes) and any("la.csv: kept 2 of 4 rows" in n for n in notes)
    assert CI.find_columns(["LAT", "Lng", "Building Name", "Primary Property Type"]) == {
        "lat": "LAT", "lon": "Lng", "name": "Building Name", "property_type": "Primary Property Type"}


def test_costar_kit_is_read_as_its_own_notes_describe(tmp_path, monkeypatch):
    """A tiny made-up kit in the real kit's layout: names come from the enums, homes from the apartment record, the
    year from the construction-complete event, and only tenancies with no end date count as current tenants."""
    from backend.la import costar_import as CI

    lat, lon = 34.05, -118.25  # downtown Los Angeles, inside the grid's area
    (tmp_path / "data").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "schemas" / "enums.json").write_text(json.dumps({
        "PropertyType": {"1": "Hospitality", "3": "Land", "5": "Office", "11": "Multifamily", "13": "Healthcare"},
        "PropertySubtype": {"0": "Unknown", "58": "MultiFamily_Apartments", "30": "Hospitality_Hotel", "29": "HealthCare_Hospital"},
        "ApartmentLifestyleType": {"1": "All", "2": "Senior"}, "ConstructionEventType": {"2": "ConstructionComplete", "4": "RenovationFinished"},
        "IndustryType": {"0": "Unknown", "15": "HealthCareAndSocialAssistance", "16": "LawFirms"}}))
    addr = lambda dy, dx=0.0: {"latitude": lat + dy, "longitude": lon + dx, "streetNumber": "100", "streetName": "Test", "streetType": "St", "city": "Los Angeles"}  # noqa: E731
    props = [
        {"id": 1, "primaryAddress": addr(0.001), "propertyType": 11, "propertySubtype": 58, "buildingName": "Sunrise Villas",
         "propertyApartment": {"totalNumUnits": 120, "apartmentLifestyleType": 2}, "constructionEvents": [{"eventType": 4, "year": 2015}, {"eventType": 2, "year": 1990}]},
        {"id": 2, "primaryAddress": addr(0.002), "propertyType": 11, "propertySubtype": 58, "propertyApartment": {"totalNumUnits": 40, "apartmentLifestyleType": 1}},
        {"id": 3, "primaryAddress": addr(0.003), "propertyType": 5, "propertySubtype": 0, "buildingName": "Care Tower", "rentableBuildingAreaSquareFeet": 50000.0},
        {"id": 4, "primaryAddress": addr(0.004), "propertyType": 5, "propertySubtype": 0, "buildingName": "Law Tower"},
        {"id": 5, "primaryAddress": addr(0.005), "propertyType": 1, "propertySubtype": 30, "buildingName": "Grand Hotel"},
        {"id": 6, "primaryAddress": addr(0.006), "propertyType": 3, "propertySubtype": 0, "buildingName": "Empty lot"},
        {"id": 7, "primaryAddress": {"latitude": 29.76, "longitude": -95.37}, "propertyType": 5, "propertySubtype": 0, "buildingName": "Houston Tower"},
    ]
    (tmp_path / "data" / "commercial-property.jsonl").write_text("\n".join(json.dumps(p) for p in props))
    # the kit's starter list: one row per property and Matterport model; a property scanned twice has two rows
    (tmp_path / "commercial-properties.csv").write_text("PropertyId,MatterportModelId,Address,City,State\n"
                                                         "1,AAA111aaa11,100 Test St,Los Angeles,CA\n1,BBB222bbb22,100 Test St,Los Angeles,CA\n"
                                                         "1,AAA111aaa11,100 Test St,Los Angeles,CA\n2,,100 Test St,Los Angeles,CA\n4,CCC333ccc33,100 Test St,Los Angeles,CA\n")
    tenancy = lambda pid, tenant, to=None, industry=0: {"propertyId": pid, "tenantLocationId": tenant, "toDate": to, "industryType": industry}  # noqa: E731
    (tmp_path / "data" / "location-occupancy.jsonl").write_text("\n".join(json.dumps(t) for t in [
        tenancy(3, 901), tenancy(3, 902), tenancy(3, None, industry=16), tenancy(3, 903, to="2020-01-01T00:00:00Z"),  # two clinics, one law firm, one gone
        tenancy(4, 904), tenancy(4, None, industry=16)]))
    (tmp_path / "data" / "company-detail.jsonl").write_text("\n".join(json.dumps(c) for c in [
        {"id": 901, "profile": {"industryType": 15}}, {"id": 902, "profile": {"industryType": 15}},
        {"id": 903, "profile": {"industryType": 15}}, {"id": 904, "profile": {"industryType": 16}}]))

    rows, notes = CI.convert(tmp_path)
    by_id = {r["id"]: r for r in rows}
    assert sorted(by_id) == [1, 2, 3, 4, 5]  # the land parcel and the Houston tower are left out
    assert by_id[1] == {"id": 1, "name": "Sunrise Villas", "property_type": "Multifamily", "secondary_type": "MultiFamily_Apartments",
                        "lat": lat + 0.001, "lon": lon, "size_sf": None, "year_built": 1990, "address": "100 Test St, Los Angeles",
                        "units": 120, "lifestyle": "Senior", "heavy_power": None, "tenants": None, "tours": ["AAA111aaa11", "BBB222bbb22"]}
    assert by_id[2]["tours"] == [] and by_id[4]["tours"] == ["CCC333ccc33"] and "2 of those properties have a Matterport 3D tour" in notes[-1]
    assert by_id[2]["name"] == "100 Test St" and by_id[3]["tenants"] == {"current": 3, "known": 3, "health": 2}
    assert by_id[4]["tenants"] == {"current": 2, "known": 2, "health": 0} and "3 current" not in notes[1] and "5 current tenancies in 2" in notes[1]

    f = tmp_path / "buildings.json"
    f.write_text(json.dumps(rows))
    monkeypatch.setattr(B, "COSTAR_FILE", f)
    B.clear_cache()
    try:
        mine = {r["name"]: r for r in B.rows() if r["source"] == "costar"}
        assert {n: r["kind"] for n, r in mine.items()} == {"Sunrise Villas": "senior_housing", "100 Test St": "residential", "Care Tower": "medical",
                                                           "Law Tower": "office", "Grand Hotel": "lodging"}
        impact = B.impact_of(list(mine.values()))
        assert impact["homes"] == 160 and impact["critical"] == 2  # senior housing and the medical office are tier 3
        assert [(t["tier"], t["n"]) for t in impact["ladder"]] == [(1, 0), (2, 0), (3, 2), (4, 2), (5, 1)]
        assert next(s for s in impact["sites"] if s["name"] == "Sunrise Villas")["units"] == 120
        # "See inside": the buildings that lose power and have a 3D tour, the one that matters most first
        assert impact["n_tours"] == 2 and impact["tours"] == [
            {"name": "Sunrise Villas", "kind": "senior_housing", "units": 120, "models": ["AAA111aaa11", "BBB222bbb22"]},
            {"name": "Law Tower", "kind": "office", "units": None, "models": ["CCC333ccc33"]}]
    finally:
        monkeypatch.undo()
        B.clear_cache()


def test_kinds_from_costar_names():
    assert B.kind_of("Hospitality", "Hospitality_Hotel") == "lodging"  # not a hospital: the word is inside "hospitality"
    assert B.kind_of("Healthcare", "HealthCare_Hospital") == B.kind_of("Health Care", "Hospital") == "hospital"
    assert B.kind_of("Office", "Office_Medical") == "medical" and B.kind_of("Specialty", "Specialty_Schools") == "school"
    assert B.kind_of("Multifamily", "MultiFamily_Apartments") == "residential"
    assert B.kind_of("Multifamily", "MultiFamily_Apartments", "Senior") == "senior_housing" and B.kind_of("Office", None, "Senior") == "office"
    assert B.kind_of("Office", None, None, {"known": 4, "health": 2}) == "medical" and B.kind_of("Office", None, None, {"known": 4, "health": 1}) == "office"
    assert B.kind_of("Office", None, None, {"known": 1, "health": 1}) == "office"  # one tenant is not enough to call it a medical site
    assert B.kind_of("GeneralRetail", "GeneralRetail_Restaurant") == "retail" and B.kind_of("Flex", "Flex_RAndD") == "industrial"
    assert B.tier_of("senior_housing") == 3 and B.severity("senior_housing") == "medium"


def test_a_costar_hospital_next_to_a_mapped_hospital_is_listed_once(tmp_path, monkeypatch):
    known = next(r for r in B.rows() if r["kind"] == "hospital")
    f = tmp_path / "buildings.json"
    f.write_text(json.dumps([
        {"id": "same", "name": "Same hospital", "property_type": "Healthcare", "secondary_type": "HealthCare_Hospital", "lat": known["lat"] + 0.0005, "lon": known["lon"]},
        {"id": "other", "name": "Another hospital", "property_type": "Healthcare", "secondary_type": "HealthCare_Hospital", "lat": known["lat"] + 0.05, "lon": known["lon"]}]))
    monkeypatch.setattr(B, "COSTAR_FILE", f)
    B.clear_cache()
    try:
        assert [r["name"] for r in B.rows() if r["source"] == "costar"] == ["Another hospital"]
    finally:
        monkeypatch.undo()
        B.clear_cache()
