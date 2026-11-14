"""Deterministic facts about a line: what the data already says, before anyone looks at a photo.

Everything here is computed from files the app already ships (grid_la.json, LANDFIRE samples at towers, 2020 Census
tracts, OSM critical sites, the replay weather). Gemini's description is checked against these facts; it never
replaces them.
"""
from __future__ import annotations

import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.la import consequence as C  # noqa: E402
from backend.la import data as D  # noqa: E402
from backend.la import engine as E  # noqa: E402

FUEL_CLASS = [  # LANDFIRE FBFM40 ranges -> plain label
    ((101, 109), "grass"), ((121, 124), "grass-shrub"), ((141, 149), "shrub"), ((161, 165), "timber understory"),
    ((181, 189), "timber litter"), ((201, 204), "slash"), ((91, 91), "urban / developed"), ((93, 93), "agriculture"),
    ((98, 98), "water"), ((99, 99), "barren"),
]
ZONE_FUEL = E.FUEL


def fuel_label(code: int) -> str:
    for (lo, hi), name in FUEL_CLASS:
        if lo <= int(code) <= hi:
            return name
    return "non-burnable" if int(code) < 100 else "other fuel"


@lru_cache
def _towers() -> dict:
    """Flattened tower arrays in grid order (the same order LAGrid and the LANDFIRE samples use)."""
    g = D.grid()
    tb, lat, lon, elev, zone = [], [], [], [], []
    for i, b in enumerate(g["branches"]):
        T = b["towers"]
        tb += [i] * len(T["lat"])
        lat += T["lat"]
        lon += T["lon"]
        elev += T["elev"]
        zone += T["zone"]
    out = {"tb": np.array(tb), "lat": np.array(lat), "lon": np.array(lon), "elev": np.array(elev), "zone": np.array(zone)}
    lf = D.P / "landfire_towers.npz"
    if lf.exists():
        z = np.load(lf)
        if int(z["n_towers"]) == len(tb):
            out["fbfm"] = z["FBFM40_c"]
            out["recept"] = np.maximum.reduce([E.fuel_receptivity(z[f"FBFM40_{k}"]) for k in ("c", "l", "r")])
            out["veg_h"] = E.veg_height(z["EVH_c"])
    return out


@lru_cache
def _index() -> dict:
    g = D.grid()
    return {b["id"]: i for i, b in enumerate(g["branches"])}



def branch(line_id: str) -> dict:
    k = _index().get(line_id)
    if k is None:
        raise KeyError(f"unknown line {line_id}")
    return D.grid()["branches"][k]


MAX_POINTS = 4       # photos per line
MIN_SPACING_KM = 1.5  # photos (~1.2 km across) do not overlap


def fire_points(line_id: str, k: int = MAX_POINTS, min_km: float = MIN_SPACING_KM) -> list[int]:
    """Up to k towers along the line (indices into the flattened tower arrays), most fire-prone first: highest
    LANDFIRE spark receptivity x zone fuel, ties broken by elevation, each at least `min_km` from the ones already
    picked so the photos cover different stretches. Fixed per line, so cached photos stay valid at any hour."""
    t = _towers()
    m = np.where(t["tb"] == _index()[line_id])[0]
    zf = np.array([ZONE_FUEL[z] for z in t["zone"][m]])
    rec = t["recept"][m] if "recept" in t else np.ones(len(m))
    fuel = np.round(rec * zf, 6)
    order = np.lexsort((-t["elev"][m], -fuel))  # by fuel score, then elevation (lexsort: last key is primary)
    coslat = np.cos(np.radians(34.0))
    picked: list[int] = []
    for j in order:
        cand = int(m[j])
        if all(np.hypot((t["lat"][cand] - t["lat"][p]) * 110.54, (t["lon"][cand] - t["lon"][p]) * 111.32 * coslat) >= min_km
               for p in picked):
            picked.append(cand)
            if len(picked) == k:
                break
    return picked


def line_point(line_id: str) -> tuple[float, float]:
    """The line's single most fire-prone tower."""
    t = _towers()
    p = fire_points(line_id, k=1)[0]
    return float(t["lat"][p]), float(t["lon"][p])


def line_points(line_id: str) -> list[tuple[float, float]]:
    t = _towers()
    return [(float(t["lat"][p]), float(t["lon"][p])) for p in fire_points(line_id)]


def alarm_lines() -> list[str]:
    """Overhead lines that cross the calibrated fire alarm in any saved replay, worst first."""
    from backend.la import replay as RP

    alarm = D.calibration()["fire"]["line_alarm"]
    g = D.grid()
    peak = np.zeros(len(g["branches"]))
    for e in D.events():
        try:
            d = RP.load(e)
        except FileNotFoundError:
            continue
        peak = np.maximum(peak, np.nanmax(d["line_risk"], axis=0))
    cable = np.array([b["cable"] for b in g["branches"]])
    order = np.argsort(-peak)
    return [g["branches"][k]["id"] for k in order if peak[k] >= alarm and not cable[k]]


def hour_weather(event_id: str, i: int, lat: float, lon: float) -> dict:
    """Replay weather (gust, humidity, wind direction) at a point and hour, from the saved replay grids."""
    from backend.la import replay as RP

    d = RP.load(event_id)
    i = max(0, min(int(i), len(d["time"]) - 1))
    j = int(np.abs(d["wx_lats"] - lat).argmin())
    k = int(np.abs(d["wx_lons"] - lon).argmin())
    return {"gust_ms": round(float(d["wx_gust"][i][j, k]), 1), "rh_pct": round(float(d["wx_rh"][i][j, k]), 0),
            "wind_from_deg": round(float(d["wx_dir"][i][j, k]), 0)}


def _point(tw: dict, p: int) -> dict:
    pt = {"lat": round(float(tw["lat"][p]), 5), "lon": round(float(tw["lon"][p]), 5), "elev_m": round(float(tw["elev"][p]), 0),
          "zone": str(tw["zone"][p])}
    if "fbfm" in tw:
        pt["landfire_fuel"] = fuel_label(int(tw["fbfm"][p]))
        pt["spark_receptivity"] = round(float(tw["recept"][p]), 2)
        pt["veg_height_m"] = round(float(tw["veg_h"][p]), 1)
    return pt


def line_facts(line_id: str, event_id: str | None = None, i: int | None = None, t: str | None = None) -> dict:
    """Everything the data says about a line: its photo points (most fire-prone towers along it), fuel along the
    whole line, and, given an event hour as replay index `i` or ISO time `t`, the weather and what is downwind of each
    point. `point` and `downwind` repeat the most fire-prone point and the worst downwind cone for quick reading."""
    if event_id is not None and i is None and t:
        from backend.la import replay as RP

        i = RP._hour_index(RP.load(event_id), t, None)
    b = branch(line_id)
    tw = _towers()
    m = np.where(tw["tb"] == _index()[line_id])[0]
    pts = [_point(tw, p) for p in fire_points(line_id)]
    facts = {"line": line_id, "name": b.get("name"), "kv": b["kv"], "zone": b["zone"], "length_km": round(b["length_m"] / 1000, 1),
             "n_towers": int(len(m)), "points": pts, "point": pts[0]}
    if "fbfm" in tw:
        mix = Counter(fuel_label(c) for c in tw["fbfm"][m])
        facts["fuel_mix"] = {name: round(n / len(m), 2) for name, n in mix.most_common(5)}
        facts["burnable_share"] = round(float(np.mean(tw["recept"][m] > 0.05)), 2)
        facts["max_veg_height_m"] = round(float(tw["veg_h"][m].max()), 1)
    if event_id is not None and i is not None:
        for pt in pts:
            pt["weather"] = hour_weather(event_id, i, pt["lat"], pt["lon"])
            pt["downwind"] = C.downwind(pt["lat"], pt["lon"], pt["weather"]["wind_from_deg"])
        facts["weather"] = pts[0]["weather"]
        facts["downwind"] = max((pt["downwind"] for pt in pts), key=lambda d: d["people"])
    return facts
