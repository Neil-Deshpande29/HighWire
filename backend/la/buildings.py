"""Buildings behind each substation, and how much it matters when one loses grid power.

When two lines are equally risky, which one to cut first depends on what each feeds: a hospital is not an office.

A building row is {id, name, kind, bus, lat, lon, backup_hours, source}. Rows come from two places and are treated alike:
  * "osm":    the grid's critical sites (OpenStreetMap hospitals and water plants). Always present.
  * "costar": property records in COSTAR_FILE, when that file exists. It is licensed data, so it is git-ignored; an
              exporter that calls the CoStar API only has to write a JSON list of
              {id, name, property_type, secondary_type?, lat, lon}. Each property is assigned to the nearest substation,
              the same rule data/build_grid.py uses to place load.

`score` is a triage weight for ranking choices. It is not a count of people and not a dollar figure. We model lines of
66 kV and above, so "loses power" means the substation that serves the area is cut off, not that a feeder was traced
to the building.
"""
from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache

import numpy as np

from . import data as D

COSTAR_FILE = D.P / "costar" / "buildings.json"

# How much one building of each kind counts when it loses grid power. A judgement, in three tiers: life safety and
# lifelines first, then places that cannot ride through an outage, then everything else.
WEIGHTS = {
    "hospital": 10, "emergency": 8, "water": 8,
    "medical": 6, "data_center": 5, "cold_storage": 4, "school": 4,
    "residential": 2, "industrial": 2, "lodging": 2, "office": 1, "retail": 1, "other": 1,
}
# The priority ladder: the same judgement as WEIGHTS, grouped into five named tiers, top tier protected first. Every
# building kind belongs to exactly one tier, and a higher tier never has a lower weight than a tier below it.
TIERS = [
    {"tier": 1, "name": "Life safety", "kinds": ["hospital", "emergency"], "why": "people are in danger within hours without power"},
    {"tier": 2, "name": "Lifelines", "kinds": ["water"], "why": "the city cannot run or fight a fire without them"},
    {"tier": 3, "name": "Cannot ride through an outage", "kinds": ["medical", "data_center", "cold_storage", "school"],
     "why": "care, data, food or children depend on power"},
    {"tier": 4, "name": "Homes and industry", "kinds": ["residential", "lodging", "industrial"], "why": "people are home or at work, but safe"},
    {"tier": 5, "name": "Offices and shops", "kinds": ["office", "retail", "other"], "why": "they can close for the day"},
]
MAX_SITES = 40  # names listed per impact; the counts always cover everything
HIGH_FROM, MEDIUM_FROM = 8, 4  # weight at or above which a building is a high or a medium warning


def severity(kind: str) -> str:
    """Warning level for one building losing grid power: high (life safety, lifelines), medium, or low."""
    w = WEIGHTS.get(kind, WEIGHTS["other"])
    return "high" if w >= HIGH_FROM else "medium" if w >= MEDIUM_FROM else "low"


def tier_of(kind: str) -> int:
    """Which rung of the priority ladder a building kind sits on (1 is protected first)."""
    return next((t["tier"] for t in TIERS if kind in t["kinds"]), TIERS[-1]["tier"])


def ladder(rows) -> list[dict]:
    """The priority ladder filled in for a set of buildings: one entry per tier, top first, with how many buildings of
    that tier are in the set and the first few names."""
    out = []
    for t in TIERS:
        mine = sorted((r for r in rows if tier_of(r["kind"]) == t["tier"]), key=lambda r: (-WEIGHTS.get(r["kind"], 1), r["name"]))
        out.append({"tier": t["tier"], "name": t["name"], "why": t["why"], "kinds": t["kinds"], "n": len(mine),
                    "names": [r["name"] for r in mine[:3]]})
    return out


def kind_of(property_type: str | None, secondary_type: str | None = None) -> str:
    """Map a CoStar property type (and optional secondary type) to one of the kinds in WEIGHTS."""
    p, s = (property_type or "").lower(), (secondary_type or "").lower()
    text = f"{p} {s}"
    if "hospital" in text:
        return "hospital"
    if "health" in p or "medical" in text:
        return "medical"
    if "data center" in text or "telecom" in text:
        return "data_center"
    if "cold" in s or "refriger" in s:
        return "cold_storage"
    if any(w in text for w in ("police", "fire station", "emergency")):
        return "emergency"
    if "school" in text or "education" in text:
        return "school"
    if any(w in p for w in ("multifamily", "multi-family", "residential", "student")) or "apartment" in text:
        return "residential"
    if "hospitality" in p or "hotel" in text:
        return "lodging"
    if "office" in p:
        return "office"
    if "retail" in p:
        return "retail"
    if "industrial" in p or "flex" in p:
        return "industrial"
    return "other"


def _nearest_bus(lat: np.ndarray, lon: np.ndarray) -> list[str]:
    from scipy.spatial import cKDTree

    buses = D.grid()["buses"]
    k = np.cos(np.radians(34.0))  # shrink longitude so distances are roughly isotropic at LA's latitude
    tree = cKDTree(np.c_[[b["lat"] for b in buses], [b["lon"] * k for b in buses]])
    _, idx = tree.query(np.c_[lat, lon * k])
    return [buses[int(i)]["id"] for i in idx]


def _costar_rows() -> list[dict]:
    if not COSTAR_FILE.exists():
        return []
    located = [p for p in json.loads(COSTAR_FILE.read_text()) if p.get("lat") is not None and p.get("lon") is not None]
    if not located:
        return []
    buses = _nearest_bus(np.array([float(p["lat"]) for p in located]), np.array([float(p["lon"]) for p in located]))
    return [{"id": f"costar:{p.get('id', k)}", "name": p.get("name") or "Unnamed property", "kind": kind_of(p.get("property_type"), p.get("secondary_type")),
             "bus": bus, "lat": float(p["lat"]), "lon": float(p["lon"]), "backup_hours": None, "source": "costar"}
            for k, (p, bus) in enumerate(zip(located, buses))]


@lru_cache(maxsize=1)
def _rows() -> tuple[dict, ...]:
    osm = [{"id": f"osm:{k}", "name": c["name"], "kind": c["type"], "bus": c["bus"], "lat": c["lat"], "lon": c["lon"],
            "backup_hours": float(c["backup_hours"]), "source": "osm"} for k, c in enumerate(D.grid()["critical_sites"])]
    return tuple(osm + _costar_rows())


def rows() -> tuple[dict, ...]:
    """Every building row, OpenStreetMap first, then CoStar when its file is present."""
    return _rows()


@lru_cache(maxsize=1)
def by_bus() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in _rows():
        out.setdefault(r["bus"], []).append(r)
    return out


def sources() -> list[str]:
    return sorted({r["source"] for r in _rows()})


def clear_cache() -> None:
    """Forget loaded rows (after COSTAR_FILE is written or replaced)."""
    _rows.cache_clear()
    by_bus.cache_clear()


def impact_of(rows: list[dict]) -> dict:
    """Summarise a set of buildings that lose grid power: counts by kind (heaviest first), the triage score, the names."""
    weight = lambda r: WEIGHTS.get(r["kind"], WEIGHTS["other"])  # noqa: E731
    counts = Counter(r["kind"] for r in rows)
    ranked = sorted(rows, key=lambda r: (-weight(r), r["name"]))
    return {
        "by_kind": {k: counts[k] for k in sorted(counts, key=lambda k: (-WEIGHTS.get(k, WEIGHTS["other"]), k))},
        "score": int(sum(weight(r) for r in rows)),
        "hospitals": int(counts.get("hospital", 0)),
        "sites": [{"name": r["name"], "kind": r["kind"], "bus": r["bus"], "backup_hours": r.get("backup_hours"), "source": r.get("source", "osm"),
                   "severity": severity(r["kind"])} for r in ranked[:MAX_SITES]],
        "n": len(rows),
        "severity": severity(ranked[0]["kind"]) if ranked else None,  # the worst single building decides the level
        "ladder": [{"tier": t["tier"], "name": t["name"], "n": t["n"]} for t in ladder(rows)],
    }


def summary() -> dict:
    """What building records are loaded: where they come from, how many of each kind, and the ladder they fill."""
    all_rows = rows()
    kinds = Counter(r["kind"] for r in all_rows)
    return {"sources": sources(), "n": len(all_rows), "costar": sum(r["source"] == "costar" for r in all_rows),
            "by_kind": {k: kinds[k] for k in sorted(kinds, key=lambda k: (-WEIGHTS.get(k, 1), k))}, "ladder": ladder(all_rows)}


def outage_impact(bus_ids) -> dict:
    """What loses grid power when these substations are cut off."""
    served = by_bus()
    return impact_of([r for b in bus_ids for r in served.get(b, [])])
