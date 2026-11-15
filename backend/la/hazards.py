"""Hazards to draw on the map: where something is going on, so the picture shows it and not only the numbers.

Two kinds, kept apart on purpose.

`detected(event, t)`: what Rewire's own numbers show this hour, grouped into places. Lines over (or heading for)
their fire alarm, lines the storm model expects to be knocked down, and overloaded lines are clustered by where they
are; each cluster is one hazard with a centre, a size, how bad it is and which way the wind would carry a spark. Read
straight from the hour's arrays (no power flow), and only from this hour and earlier ones, so a replay never knows
what came next.

`live()`: what the public warning services say right now, for the live view. Active National Weather Service alerts for
the region (Red Flag, high wind, flood, tornado, hurricane, heat, ...) and recent earthquakes from the USGS feed. Both
are free and need no key. The answer is cached for a short while and shared by everyone using this server; with no
network it is simply empty.
"""
from __future__ import annotations

import json
import math
import time
import urllib.request

import numpy as np

from . import data as D
from . import replay as RP

CLUSTER_KM = 9.0  # lines closer than this to a cluster's centre join it
MAX_HAZARDS = 7  # the map shows the worst few places, not every line
NEAR_ALARM = 0.85  # a line this close to its fire alarm counts as "heading for it" when it is also rising
RISING = 0.04  # ...by at least this share of the alarm over the last TREND_H hours
TREND_H = 2
DAMAGE_ACT, DAMAGE_WATCH = 0.5, 0.4  # storm model: chance a line is knocked down
LOAD_ACT, LOAD_WATCH = 100.0, 92.0  # percent of a line's rating
COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")

NWS_URL = "https://api.weather.gov/alerts/active?area=CA"
QUAKES_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
AGENT = "Rewire grid co-pilot (hackathon demo)"  # NWS asks every client to say what it is
LIVE_TTL_S = 120  # how long one answer from the warning services is reused
QUAKE_PAD_DEG = 0.6  # earthquakes this far outside the region still matter to it
COUNTIES = ("Los Angeles", "Ventura", "Orange", "San Bernardino")
NWS_KIND = (("red flag", "fire"), ("fire weather", "fire"), ("tornado", "tornado"), ("hurricane", "hurricane"), ("tropical storm", "hurricane"),
            ("thunderstorm", "storm"), ("flood", "rain"), ("rain", "rain"), ("wind", "wind"), ("heat", "heat"), ("winter", "storm"), ("dust", "wind"))


def _km(lat1, lon1, lat2, lon2) -> float:
    return math.hypot((lat1 - lat2) * 110.54, (lon1 - lon2) * 111.32 * math.cos(math.radians((lat1 + lat2) / 2)))


def _mid(branch: dict) -> tuple[float, float]:
    g = branch["geometry"]
    lat, lon = g[len(g) // 2][:2]
    return float(lat), float(lon)


def _clusters(items: list[tuple[int, float]], branches: list[dict]) -> list[dict]:
    """Greedy grouping of (branch index, weight) by place, worst first: a line joins the nearest cluster within
    CLUSTER_KM of its centre, or starts one."""
    out: list[dict] = []
    for k, w in sorted(items, key=lambda x: -x[1]):
        lat, lon = _mid(branches[k])
        best = min(out, key=lambda c: _km(lat, lon, c["lat"], c["lon"]), default=None)
        if best is not None and _km(lat, lon, best["lat"], best["lon"]) <= CLUSTER_KM:
            n = len(best["k"])
            best["lat"], best["lon"] = (best["lat"] * n + lat) / (n + 1), (best["lon"] * n + lon) / (n + 1)
            best["k"].append(k)
            best["w"] = max(best["w"], w)
        else:
            out.append({"lat": lat, "lon": lon, "k": [k], "w": w})
    for c in out:
        c["radius_km"] = round(min(14.0, max(3.0, 2.0 + max(_km(*_mid(branches[k]), c["lat"], c["lon"]) for k in c["k"]))), 1)
    return out


def _wind_at(d: dict, h: int, lat: float, lon: float) -> tuple[float, float]:
    """(direction the wind comes from in degrees, gust in m/s) at the weather grid cell nearest a place."""
    j, i = int(np.abs(d["wx_lats"] - lat).argmin()), int(np.abs(d["wx_lons"] - lon).argmin())
    return float(d["wx_dir"][h][j, i]), float(d["wx_gust"][h][j, i])


def _lines(n: int) -> str:
    return f"{n} line{'s' if n != 1 else ''}"


def detected(eid: str, t: str | None = None, i: int | None = None) -> list[dict]:
    """The places where this hour's numbers show trouble, worst first. Each is {id, kind ("fire", "storm",
    "overload"), level ("act" at a limit, "watch" heading for one), lat, lon, radius_km, title, detail, lines,
    wind_from_deg, gust_ms, rising}."""
    d = RP.load(eid)
    h = RP._hour_index(d, t, i)
    branches = D.grid()["branches"]
    box = D.grid()["bbox"]
    # only lines the map draws: overhead, with their middle inside the region (the grid reaches a little beyond it)
    on_map = np.array([box["lat_min"] <= la <= box["lat_max"] and box["lon_min"] <= lo <= box["lon_max"] for la, lo in map(_mid, branches)])
    overhead = np.array([not b["cable"] for b in branches]) & on_map
    alarm = D.calibration()["fire"]["line_alarm"]
    risk, load = d["line_risk"][h], np.where(on_map, d["loading"][h], 0.0)
    before = d["line_risk"][max(0, h - TREND_H)]
    out: list[dict] = []

    def add(kind: str, level: str, c: dict, title: str, detail: str, rising: bool = False):
        frm, gust = _wind_at(d, h, c["lat"], c["lon"])
        out.append({"id": f"{kind}-{branches[c['k'][0]]['id']}", "kind": kind, "level": level, "lat": round(c["lat"], 4), "lon": round(c["lon"], 4),
                    "radius_km": c["radius_km"], "title": title, "detail": detail, "lines": [branches[k]["id"] for k in c["k"][:8]],
                    "n_lines": len(c["k"]), "wind_from_deg": round(frm), "gust_ms": round(gust, 1), "rising": rising})

    # fire: over the alarm, or close to it and climbing
    over = [(k, float(risk[k]) / alarm) for k in np.flatnonzero(overhead & (risk >= alarm))]
    climbing = [(k, float(risk[k]) / alarm) for k in np.flatnonzero(overhead & (risk < alarm) & (risk >= NEAR_ALARM * alarm) & (risk - before >= RISING * alarm))]
    for c in _clusters(over, branches):
        frm, gust = _wind_at(d, h, c["lat"], c["lon"])
        went_up = bool(np.mean(risk[c["k"]] - before[c["k"]]) >= RISING * alarm)
        add("fire", "act", c, "High fire risk", f"{_lines(len(c['k']))} over the fire alarm; gusts {gust:.0f} m/s from the {COMPASS[int((frm + 22.5) // 45) % 8]}"
            + (", and rising" if went_up else "") + ".", went_up)
    for c in _clusters(climbing, branches):
        if any(_km(c["lat"], c["lon"], o["lat"], o["lon"]) <= CLUSTER_KM for o in out):
            continue  # already inside a fire hazard
        add("fire", "watch", c, "Fire risk climbing", f"{_lines(len(c['k']))} close to the fire alarm and rising fast.", True)
    # storm damage
    if "line_damage" in d:
        dmg = d["line_damage"][h]
        for level, lo, hi, title in (("act", DAMAGE_ACT, 9.0, "Storm damage likely"), ("watch", DAMAGE_WATCH, DAMAGE_ACT, "Storm damage possible")):
            items = [(k, float(dmg[k])) for k in np.flatnonzero(overhead & (dmg >= lo) & (dmg < hi))]
            for c in _clusters(items, branches):
                if level == "watch" and any(o["kind"] == "storm" and _km(c["lat"], c["lon"], o["lat"], o["lon"]) <= CLUSTER_KM for o in out):
                    continue
                add("storm", level, c, title, f"{_lines(len(c['k']))} with up to a {round(100 * c['w'])}% chance of being knocked down by wind or rain.")
    # overloads
    for level, lo, hi, title in (("act", LOAD_ACT, 1e9, "Lines overloaded"), ("watch", LOAD_WATCH, LOAD_ACT, "Lines close to their rating")):
        hit = (load > lo) if level == "act" else ((load >= lo) & (load <= hi))
        items = [(k, float(load[k])) for k in np.flatnonzero(hit)]
        for c in _clusters(items, branches):
            if level == "watch" and any(o["kind"] == "overload" and _km(c["lat"], c["lon"], o["lat"], o["lon"]) <= CLUSTER_KM for o in out):
                continue
            add("overload", level, c, title, f"{_lines(len(c['k']))} at up to {round(c['w'])}% of rating.")
    out.sort(key=lambda z: (z["level"] != "act", -z["n_lines"]))
    return out[:MAX_HAZARDS]


# --------------------------------------------------------------------------- the public warning services, live
_live: dict = {"at": 0.0, "data": None}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=7) as r:  # noqa: S310  fixed public endpoints
        return json.loads(r.read())


def _rings(geometry: dict | None) -> list[list[list[float]]]:
    """Outer rings of a GeoJSON Polygon or MultiPolygon as [[lon, lat], ...]."""
    if not geometry:
        return []
    polys = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"] if geometry["type"] == "MultiPolygon" else []
    return [[[float(x), float(y)] for x, y, *_ in poly[0]] for poly in polys if poly]


def _nws(payload: dict, box: dict, zones: dict) -> list[dict]:
    out = []
    for f in payload.get("features", []):
        p = f.get("properties") or {}
        if p.get("status") != "Actual" or p.get("messageType") == "Cancel":
            continue
        rings = _rings(f.get("geometry"))
        if not rings:  # issued for whole forecast zones: draw the ones this region has outlines for
            rings = [ring for u in ((p.get("geocode") or {}).get("UGC") or []) for ring in zones.get(u, [])]
        inside = [r for r in rings if any(box["lat_min"] <= y <= box["lat_max"] and box["lon_min"] <= x <= box["lon_max"] for x, y in r)]
        named = any(c in (p.get("areaDesc") or "") for c in COUNTIES)
        if not inside and not (named and not rings):
            continue
        event = p.get("event") or "Weather alert"
        kind = next((k for word, k in NWS_KIND if word in event.lower()), "weather")
        out.append({"id": p.get("id") or f.get("id") or event, "event": event, "kind": kind, "severity": p.get("severity") or "Unknown",
                    "headline": (p.get("headline") or event)[:200], "area": (p.get("areaDesc") or "")[:160], "ends_utc": p.get("ends") or p.get("expires"),
                    "rings": inside[:12]})
    order = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}
    out.sort(key=lambda a: order.get(a["severity"], 4))
    return out[:12]


def _quakes(payload: dict, box: dict) -> list[dict]:
    out = []
    for f in payload.get("features", []):
        p, (lon, lat, depth) = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates", [0, 0, 0])[:3]
        if not (box["lat_min"] - QUAKE_PAD_DEG <= lat <= box["lat_max"] + QUAKE_PAD_DEG and box["lon_min"] - QUAKE_PAD_DEG <= lon <= box["lon_max"] + QUAKE_PAD_DEG):
            continue
        out.append({"id": f.get("id"), "mag": p.get("mag"), "place": p.get("place"), "lat": lat, "lon": lon, "depth_km": depth,
                    "time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((p.get("time") or 0) / 1000))})
    out.sort(key=lambda q: -(q["mag"] or 0))
    return out[:8]


def live(fetch=_get, now: float | None = None) -> dict:
    """Active weather-service alerts and recent earthquakes for the region, for the live view. Never raises: a service
    that cannot be reached leaves its list empty and is named in `unreachable`."""
    now = time.time() if now is None else now
    if _live["data"] is not None and now - _live["at"] < LIVE_TTL_S and fetch is _get:
        return _live["data"]
    box, zones = D.grid()["bbox"], D.redflag().get("zones", {})
    zone_rings = {u: _rings(z.get("geometry")) for u, z in zones.items()}
    out = {"alerts": [], "quakes": [], "unreachable": [], "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
           "sources": ["National Weather Service active alerts", "USGS earthquake feed (magnitude 2.5 and up, past day)"]}
    try:
        out["alerts"] = _nws(fetch(NWS_URL), box, zone_rings)
    except Exception:  # noqa: BLE001  offline, or the service is down: the map just shows none
        out["unreachable"].append("National Weather Service")
    try:
        out["quakes"] = _quakes(fetch(QUAKES_URL), box)
    except Exception:  # noqa: BLE001
        out["unreachable"].append("USGS")
    if fetch is _get:
        _live.update(at=now, data=out)
    return out
