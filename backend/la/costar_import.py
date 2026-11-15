"""Turn the CoStar data kit into the building list the planner and the Buildings agent read.

The kit is licensed. It is handed out in the hackathon's track Discord (/costar-keys) and must stay out of git.
Unzip it anywhere on this machine, then:

    python -m backend.la.costar_import /path/to/costar-hackathon-kit

It writes data/processed/costar/buildings.json (git-ignored): one row per property inside the grid's area.
backend/la/buildings.py reads that file, maps each property to a rung of the priority ladder and assigns it to its
nearest substation.

Two inputs are understood:
  * the hackathon kit folder (it contains data/commercial-property.jsonl and schemas/enums.json). Read as its own
    documentation describes: type and subtype names from the enums, location from primaryAddress, apartment homes
    from propertyApartment.totalNumUnits, lifestyle (senior, student), year built from the construction-complete
    event, and the current tenants (location-occupancy records with no toDate) counted by industry.
  * any other table (.csv, .xlsx, .json, .geojson, or a folder of them). Columns are found by name ("Latitude",
    "PropertyType", ...); the command prints what it used and skips a file with no coordinates rather than guessing.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from . import buildings as B
from . import data as D

# What each field may be called, most specific first. Headers are compared with spaces and punctuation removed.
COLUMNS = {
    "lat": ["latitude", "propertylatitude", "lat", "y"],
    "lon": ["longitude", "propertylongitude", "lon", "lng", "long", "x"],
    "id": ["propertyid", "costarid", "buildingid", "id"],
    "name": ["propertyname", "buildingname", "name"],
    "property_type": ["propertytype", "primarypropertytype", "propertytypename", "type"],
    "secondary_type": ["secondarytype", "propertysubtype", "secondarypropertytype", "buildingsubtype", "subtype"],
    "size_sf": ["rba", "rentablebuildingarea", "buildingsize", "buildingarea", "totalbuildingsf", "squarefeet", "sqft", "gla"],
    "year_built": ["yearbuilt", "builtyear"],
    "address": ["propertyaddress", "streetaddress", "address"],
}
READABLE = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json", ".geojson"}
KIT_PROPERTIES, KIT_ENUMS = "data/commercial-property.jsonl", "schemas/enums.json"
KIT_TENANCIES, KIT_COMPANIES = "data/location-occupancy.jsonl", "data/company-detail.jsonl"
KIT_MODELS = "commercial-properties.csv"  # PropertyId, MatterportModelId: the 3D tours CoStar holds of each property
MAX_TOURS = 6  # tours kept per property (a large community can have dozens of unit scans)
BUILT = "ConstructionComplete"  # the construction event that gives the year built
HEALTH = "HealthCareAndSocialAssistance"  # the tenant industry that makes an office a medical site


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def find_columns(headers) -> dict[str, str]:
    """Which header holds each field: an exact name first, then a header that contains the name."""
    by_norm = {_norm(h): h for h in headers}
    found = {}
    for field, names in COLUMNS.items():
        hit = next((by_norm[n] for n in names if n in by_norm), None)
        if hit is None:
            hit = next((h for n in names if len(n) > 3 for k, h in by_norm.items() if n in k), None)
        if hit is not None:
            found[field] = hit
    return found


def read_table(path: Path) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str)  # needs openpyxl; pandas says so if it is missing
    if ext in (".json", ".geojson"):
        data = json.loads(path.read_text())
        if isinstance(data, dict) and "features" in data:  # GeoJSON: properties, plus the point as lon/lat
            rows = []
            for f in data["features"]:
                row = dict(f.get("properties") or {})
                g = f.get("geometry") or {}
                if g.get("type") == "Point":
                    row.setdefault("longitude", g["coordinates"][0])
                    row.setdefault("latitude", g["coordinates"][1])
                rows.append(row)
            return pd.DataFrame(rows)
        return pd.DataFrame(data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list)))
    return pd.read_csv(path, dtype=str, sep=None, engine="python")  # the separator is sniffed: comma, tab or semicolon


def _area() -> tuple[float, float, float, float]:
    ter = D.terrain()
    return float(np.min(ter["lats"])), float(np.max(ter["lats"])), float(np.min(ter["lons"])), float(np.max(ter["lons"]))


def _jsonl(path: Path):
    with path.open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _kit_tenants(source: Path, wanted: set, industries: dict) -> dict:
    """Current tenants of each wanted property: {property id: {"current": n, "known": n with an industry, "health": n}}.
    A tenancy is current when it has no toDate. The industry comes from the linked company when there is one, as the
    kit's own notes say, and from the tenancy record otherwise."""
    tenancies = [o for o in _jsonl(source / KIT_TENANCIES) if o.get("propertyId") in wanted and o.get("toDate") is None]
    linked = {o["tenantLocationId"] for o in tenancies if o.get("tenantLocationId") is not None}
    company_industry = {}
    if linked and (source / KIT_COMPANIES).exists():
        for c in _jsonl(source / KIT_COMPANIES):
            if c.get("id") in linked:
                company_industry[c["id"]] = (c.get("profile") or {}).get("industryType")
    out: dict = {}
    for o in tenancies:
        code = company_industry.get(o.get("tenantLocationId")) or o.get("industryType") or 0
        name = industries.get(str(code), "Unknown")
        t = out.setdefault(o["propertyId"], {"current": 0, "known": 0, "health": 0})
        t["current"] += 1
        t["known"] += name != "Unknown"
        t["health"] += name == HEALTH
    return out


def _kit_tours(source: Path) -> dict:
    """{property id: [Matterport model id, ...]} from the kit's starter list, in file order, without repeats."""
    import csv

    out: dict = {}
    with open(source / KIT_MODELS, newline="") as f:
        for row in csv.DictReader(f):
            pid, mid = (row.get("PropertyId") or "").strip(), (row.get("MatterportModelId") or "").strip()
            if pid and mid and mid not in out.setdefault(pid, []):
                out[pid].append(mid)
    return out


def convert_kit(source: Path) -> tuple[list[dict], list[str]]:
    """Read the hackathon kit and return (commercial properties inside the grid's area, notes on what was read).
    Land parcels are left out: there is no building on them to lose power."""
    enums = json.loads((source / KIT_ENUMS).read_text())
    name = lambda group, v: enums.get(group, {}).get(str(v)) if v is not None else None  # noqa: E731
    south, north, west, east = _area()
    inside, total = [], 0
    for r in _jsonl(source / KIT_PROPERTIES):
        total += 1
        a = r.get("primaryAddress") or {}
        lat, lon = a.get("latitude"), a.get("longitude")
        if lat is not None and lon is not None and south <= lat <= north and west <= lon <= east and name("PropertyType", r.get("propertyType")) != "Land":
            inside.append(r)
    notes = [f"{KIT_PROPERTIES}: {total:,} properties, {len(inside):,} with a building inside the Los Angeles grid"]
    tenants = {}
    if (source / KIT_TENANCIES).exists():
        tenants = _kit_tenants(source, {r["id"] for r in inside}, enums.get("IndustryType", {}))
        notes.append(f"{KIT_TENANCIES}: {sum(t['current'] for t in tenants.values()):,} current tenancies in {len(tenants):,} of those properties")
    tours = {}
    if (source / KIT_MODELS).exists():
        tours = _kit_tours(source)
        with_tour = sum(str(r["id"]) in tours for r in inside)
        notes.append(f"{KIT_MODELS}: {with_tour:,} of those properties have a Matterport 3D tour")
    rows = []
    for r in inside:
        a, apt = r["primaryAddress"], r.get("propertyApartment") or {}
        street = " ".join(str(a[k]) for k in ("streetNumber", "streetName", "streetType") if a.get(k))
        built = next((e.get("year") for e in r.get("constructionEvents") or [] if name("ConstructionEventType", e.get("eventType")) == BUILT), None)
        size = r.get("rentableBuildingAreaSquareFeet") or r.get("grossBuildingAreaSquareFeet")
        rows.append({
            "id": r["id"], "name": r.get("buildingName") or street or "Unnamed property",
            "property_type": name("PropertyType", r.get("propertyType")), "secondary_type": name("PropertySubtype", r.get("propertySubtype")),
            "lat": float(a["latitude"]), "lon": float(a["longitude"]), "size_sf": int(size) if size else None, "year_built": built,
            "address": ", ".join(x for x in (street, a.get("city")) if x) or None,
            "units": apt.get("totalNumUnits"), "lifestyle": name("ApartmentLifestyleType", apt.get("apartmentLifestyleType")),
            "heavy_power": ((r.get("propertyIndustrial") or r.get("propertyFlex") or {}).get("electric") or {}).get("hasHeavyPower"),
            "tenants": tenants.get(r["id"]),
            "tours": tours.get(str(r["id"]), [])[:MAX_TOURS],  # Matterport model ids: what the inside of it looks like
        })
    return rows, notes


def convert(source: Path) -> tuple[list[dict], list[str]]:
    """Read the kit, or a table or folder of tables, and return (properties inside the grid's area, notes on what was read)."""
    if source.is_dir() and (source / KIT_PROPERTIES).exists() and (source / KIT_ENUMS).exists():
        return convert_kit(source)
    files = sorted(p for p in source.rglob("*") if p.suffix.lower() in READABLE) if source.is_dir() else [source]
    south, north, west, east = _area()
    out, notes, seen = [], [], set()
    for f in files:
        try:
            table = read_table(f)
        except Exception as e:  # noqa: BLE001  one unreadable file should not stop the others
            notes.append(f"{f.name}: could not be read ({str(e)[:80]})")
            continue
        cols = find_columns(table.columns)
        if "lat" not in cols or "lon" not in cols:
            notes.append(f"{f.name}: skipped, no latitude and longitude columns among {list(table.columns)[:12]}")
            continue
        notes.append(f"{f.name}: {len(table):,} rows; using " + ", ".join(f"{k} = '{v}'" for k, v in cols.items()))
        lat, lon = pd.to_numeric(table[cols["lat"]], errors="coerce"), pd.to_numeric(table[cols["lon"]], errors="coerce")
        inside = lat.between(south, north) & lon.between(west, east)
        text = lambda row, k: (str(row[cols[k]]).strip() if k in cols and pd.notna(row[cols[k]]) else None)  # noqa: E731
        before = len(out)
        for n, row in table[inside].iterrows():
            pid = text(row, "id") or f"{f.stem}:{n}"
            if pid in seen:
                continue
            seen.add(pid)
            out.append({"id": pid, "name": text(row, "name") or text(row, "address") or "Unnamed property",
                        "property_type": text(row, "property_type"), "secondary_type": text(row, "secondary_type"),
                        "lat": float(lat[n]), "lon": float(lon[n]), "size_sf": text(row, "size_sf"), "year_built": text(row, "year_built"),
                        "address": text(row, "address")})
        notes.append(f"{f.name}: kept {len(out) - before:,} of {len(table):,} rows (inside the Los Angeles grid, one per property id)")
    return out, notes


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    source = Path(argv[0]).expanduser()
    if not source.exists():
        print(f"Nothing at {source}")
        return 2
    rows, notes = convert(source)
    print("\n".join(notes))
    if not rows:
        print("No properties were read. Nothing was written.")
        return 1
    B.COSTAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    B.COSTAR_FILE.write_text(json.dumps(rows))
    B.clear_cache()
    kinds = Counter(B.kind_of(r["property_type"], r["secondary_type"], r.get("lifestyle"), r.get("tenants")) for r in rows)
    homes = sum(int(r.get("units") or 0) for r in rows)
    print(f"Wrote {len(rows):,} properties to {B.COSTAR_FILE} (git-ignored)" + (f", with {homes:,} apartment homes." if homes else "."))
    for t in B.TIERS:
        n = sum(kinds[k] for k in t["kinds"])
        print(f"  tier {t['tier']} {t['name']}: {n:,}" + (f" ({', '.join(f'{kinds[k]:,} {k}' for k in t['kinds'] if kinds[k])})" if n else ""))
    print("Restart the backend. The Buildings agent and every plan now use these records.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
