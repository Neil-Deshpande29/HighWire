"""Turn the CoStar data extract into the building list the planner and the Buildings agent read.

The extract is licensed. It is handed out in the hackathon's track Discord (/costar-keys replies with the download
link and its password) and must stay out of git. Save the file, or its unzipped folder, anywhere on this machine, then:

    python -m backend.la.costar_import /path/to/the/extract          a .csv, .xlsx, .json or .geojson file, or a folder

It writes data/processed/costar/buildings.json (git-ignored): one row per property inside the grid's area, with
{id, name, property_type, secondary_type, lat, lon, size_sf, year_built, address}. backend/la/buildings.py reads that
file, maps each property type to a rung of the priority ladder and assigns the property to its nearest substation.

The extract's exact column names are not published, so columns are found by name ("Latitude", "PropertyType", ...).
The command prints which column it used for what, and stops with a clear message when it finds no latitude and
longitude, rather than guessing.
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


def convert(source: Path) -> tuple[list[dict], list[str]]:
    """Read the extract (a file, or every readable file in a folder) and return (properties inside the grid's area,
    notes on what was read)."""
    files = sorted(p for p in source.rglob("*") if p.suffix.lower() in READABLE) if source.is_dir() else [source]
    ter = D.terrain()
    south, north, west, east = float(np.min(ter["lats"])), float(np.max(ter["lats"])), float(np.min(ter["lons"])), float(np.max(ter["lons"]))
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
    kinds = Counter(B.kind_of(r["property_type"], r["secondary_type"]) for r in rows)
    print(f"Wrote {len(rows):,} properties to {B.COSTAR_FILE} (git-ignored).")
    for t in B.TIERS:
        n = sum(kinds[k] for k in t["kinds"])
        print(f"  tier {t['tier']} {t['name']}: {n:,}" + (f" ({', '.join(f'{kinds[k]:,} {k}' for k in t['kinds'] if kinds[k])})" if n else ""))
    print("Restart the backend. The Buildings agent and every plan now use these records.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
