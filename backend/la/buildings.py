"""Buildings behind each substation, and how much it matters when one loses grid power.

When two lines are equally risky, which one to cut first depends on what each feeds: a hospital is not an office.

A building row is {id, name, kind, bus, lat, lon, backup_hours, source, units, size_sf}. Rows come from two places and
are treated alike:
  * "osm":    the grid's critical sites (OpenStreetMap hospitals and water plants). Always present.
  * "costar": property records in COSTAR_FILE, when that file exists. backend/la/costar_import.py writes it from the
              CoStar data kit (licensed, so git-ignored) as a JSON list of {id, name, property_type, secondary_type,
              lat, lon} plus, where CoStar has them, apartment homes (`units`), the community's lifestyle (senior,
              student) and its current tenants by industry. Each property is assigned to the nearest substation, the
              same rule data/build_grid.py uses to place load.

`score` is a triage weight for ranking choices. It is not a count of people and not a dollar figure. We model lines of
66 kV and above, so "loses power" means the substation that serves the area is cut off, not that a feeder was traced
to the building.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache

import numpy as np

from . import data as D

COSTAR_FILE = D.P / "costar" / "buildings.json"

# How much one building of each kind counts when it loses grid power. A judgement, in three tiers: life safety and
# lifelines first, then places that cannot ride through an outage, then everything else.
WEIGHTS = {
    "hospital": 10, "emergency": 8, "water": 8,
    "medical": 6, "data_center": 5, "senior_housing": 5, "cold_storage": 4, "school": 4,
    "residential": 2, "industrial": 2, "lodging": 2, "office": 1, "retail": 1, "other": 1,
}
# The priority ladder: the same judgement as WEIGHTS, grouped into five named tiers, top tier protected first. Every
# building kind belongs to exactly one tier, and a higher tier never has a lower weight than a tier below it.
TIERS = [
    {"tier": 1, "name": "Life safety", "kinds": ["hospital", "emergency"], "why": "people are in danger within hours without power"},
    {"tier": 2, "name": "Lifelines", "kinds": ["water"], "why": "the city cannot run or fight a fire without them"},
    {"tier": 3, "name": "Cannot ride through an outage", "kinds": ["medical", "data_center", "senior_housing", "cold_storage", "school"],
     "why": "care, data, food, older residents or children depend on power"},
    {"tier": 4, "name": "Homes and industry", "kinds": ["residential", "lodging", "industrial"], "why": "people are home or at work, but safe"},
    {"tier": 5, "name": "Offices and shops", "kinds": ["office", "retail", "other"], "why": "they can close for the day"},
]
MAX_SITES = 40  # names listed per impact; the counts always cover everything
MAX_TOURED = 6  # buildings offered with a "See inside" tour per impact, the ones that matter most first
HIGH_FROM, MEDIUM_FROM = 8, 4  # weight at or above which a building is a high or a medium warning
CRITICAL_TIERS = 3  # tiers 1 to 3 are the "critical sites" the screen counts; homes and offices are listed, not counted there
SAME_PLACE_M = 200.0  # a CoStar hospital this close to an OpenStreetMap hospital is the same hospital


def severity(kind: str) -> str:
    """Warning level for one building losing grid power: high (life safety, lifelines), medium, or low."""
    w = WEIGHTS.get(kind, WEIGHTS["other"])
    return "high" if w >= HIGH_FROM else "medium" if w >= MEDIUM_FROM else "low"


# Plain words for each kind, singular and plural, for the sentences the agents and the bulletin write.
WORDS = {
    "hospital": ("hospital", "hospitals"), "emergency": ("emergency service", "emergency services"), "water": ("water plant", "water plants"),
    "medical": ("medical site", "medical sites"), "data_center": ("data center", "data centers"), "senior_housing": ("senior housing site", "senior housing sites"),
    "cold_storage": ("cold store", "cold stores"), "school": ("school", "schools"), "residential": ("apartment building", "apartment buildings"),
    "lodging": ("hotel", "hotels"), "industrial": ("industrial site", "industrial sites"), "office": ("office", "offices"), "retail": ("shop", "shops"),
    "other": ("other building", "other buildings"),
}


def words(by_kind: dict) -> str:
    """"2 hospitals, 106 apartment buildings" from counts by kind."""
    return ", ".join(f"{n:,} {WORDS.get(k, (k, k + 's'))[0 if n == 1 else 1]}" for k, n in by_kind.items())


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


def kind_of(property_type: str | None, secondary_type: str | None = None, lifestyle: str | None = None, tenants: dict | None = None) -> str:
    """Map a CoStar property type and subtype to one of the kinds in WEIGHTS. Works on CoStar's names with or without
    spaces ("Health Care" / "HealthCare_Hospital", "Multi-Family" / "MultiFamily_Apartments").
    Two refinements use what else CoStar knows: an apartment community whose lifestyle is Senior is senior housing,
    and an office where at least half of the current tenants with a known industry are in health care is a medical site.
    `tenants` is {"known": n, "health": n}."""
    p, s = (property_type or "").lower(), (secondary_type or "").lower()
    text = f"{p} {s}"
    if "hospitality" in p or "hotel" in text:  # before "hospital": the word is inside "hospitality"
        kind = "lodging"
    elif re.search(r"hospital(?!ity)", text):
        kind = "hospital"
    elif "health" in p or "medical" in text:
        kind = "medical"
    elif "data center" in text or "datacenter" in text or "telecom" in text:
        kind = "data_center"
    elif "cold" in s or "refriger" in s:
        kind = "cold_storage"
    elif any(w in text for w in ("police", "fire station", "emergency")):
        kind = "emergency"
    elif "school" in text or "education" in text:
        kind = "school"
    elif any(w in p for w in ("multifamily", "multi-family", "residential", "student")) or "apartment" in text:
        kind = "residential"
    elif "office" in p:
        kind = "office"
    elif "retail" in p:
        kind = "retail"
    elif "industrial" in p or "flex" in p:
        kind = "industrial"
    else:
        kind = "other"
    if kind == "residential" and (lifestyle or "").lower() == "senior":
        return "senior_housing"
    if kind == "office" and tenants and tenants.get("known", 0) >= 2 and 2 * tenants.get("health", 0) >= tenants["known"]:
        return "medical"
    return kind


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
    num = lambda v: int(float(v)) if v not in (None, "") else None  # noqa: E731
    return [{"id": f"costar:{p.get('id', k)}", "name": p.get("name") or "Unnamed property",
             "kind": kind_of(p.get("property_type"), p.get("secondary_type"), p.get("lifestyle"), p.get("tenants")),
             "bus": bus, "lat": float(p["lat"]), "lon": float(p["lon"]), "backup_hours": None, "source": "costar",
             "units": num(p.get("units")), "size_sf": num(p.get("size_sf")),
             "tours": [str(m) for m in (p.get("tours") or []) if m]}  # Matterport 3D tours of the building, where CoStar has them
            for k, (p, bus) in enumerate(zip(located, buses))]


@lru_cache(maxsize=1)
def _rows() -> tuple[dict, ...]:
    osm = [{"id": f"osm:{k}", "name": c["name"], "kind": c["type"], "bus": c["bus"], "lat": c["lat"], "lon": c["lon"],
            "backup_hours": float(c["backup_hours"]), "source": "osm"} for k, c in enumerate(D.grid()["critical_sites"])]
    known = [(r["lat"], r["lon"]) for r in osm if r["kind"] == "hospital"]
    near = lambda r: any(np.hypot((r["lat"] - la) * 110_540, (r["lon"] - lo) * 92_300) < SAME_PLACE_M for la, lo in known)  # noqa: E731
    return tuple(osm + [r for r in _costar_rows() if not (r["kind"] == "hospital" and near(r))])  # one hospital, listed once


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
                   "severity": severity(r["kind"]), "units": r.get("units")} for r in ranked[:MAX_SITES]],
        "n": len(rows),
        # buildings among these with a Matterport 3D tour (CoStar): the heaviest first, then the most homes
        "tours": [{"name": r["name"], "kind": r["kind"], "units": r.get("units"), "models": r["tours"][:4]}
                  for r in sorted((r for r in rows if r.get("tours")), key=lambda r: (-weight(r), -(r.get("units") or 0), r["name"]))[:MAX_TOURED]],
        "n_tours": int(sum(bool(r.get("tours")) for r in rows)),
        "critical": int(sum(tier_of(r["kind"]) <= CRITICAL_TIERS for r in rows)),  # tiers 1 to 3
        "homes": int(sum(r.get("units") or 0 for r in rows if r["kind"] in ("residential", "senior_housing"))),  # apartment homes, from CoStar
        "severity": severity(ranked[0]["kind"]) if ranked else None,  # the worst single building decides the level
        "ladder": [{"tier": t["tier"], "name": t["name"], "n": t["n"]} for t in ladder(rows)],
    }


def summary() -> dict:
    """What building records are loaded: where they come from, how many of each kind, and the ladder they fill."""
    all_rows = rows()
    kinds = Counter(r["kind"] for r in all_rows)
    return {"sources": sources(), "n": len(all_rows), "costar": sum(r["source"] == "costar" for r in all_rows),
            "homes": int(sum(r.get("units") or 0 for r in all_rows if r["kind"] in ("residential", "senior_housing"))),
            "by_kind": {k: kinds[k] for k in sorted(kinds, key=lambda k: (-WEIGHTS.get(k, 1), k))}, "ladder": ladder(all_rows)}


def outage_impact(bus_ids) -> dict:
    """What loses grid power when these substations are cut off."""
    served = by_bus()
    return impact_of([r for b in bus_ids for r in served.get(b, [])])
