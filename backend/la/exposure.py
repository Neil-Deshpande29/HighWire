"""Fire exposure: the buildings downwind of a line that is still live at high fire risk.

The counterpart of buildings.outage_impact. Turning a line off puts the buildings behind it on backup power: that is
the outage consequence, the cost of acting. Leaving it on keeps the buildings downwind exposed if it sparks: that is
the fire exposure, the cost of not acting. The screen shows the two side by side and never adds them together.

Same cone as consequence.downwind (10 km, 30 degrees either side of where the wind blows) and the same building rows
as buildings.py, so a CoStar file widens both lists at once. Exposure is "inside the cone of a live high-risk line",
not a prediction that the building burns.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from . import buildings as B
from .consequence import COSL, HALF_ANGLE_DEG, LENGTH_KM

MAX_SITES = 40


def exposed(points: list[tuple[float, float, float, str]]) -> dict:
    """Buildings inside the downwind cone of any of `points` [(lat, lon, wind_from_deg, line_id), ...].
    Each building is counted once and names the nearest such line."""
    rows = B.rows()
    if not rows or not points:
        return {"by_kind": {}, "score": 0, "hospitals": 0, "n": 0, "critical": 0, "homes": 0, "severity": None, "sites": []}
    lat, lon = np.array([r["lat"] for r in rows]), np.array([r["lon"] for r in rows])
    near = np.full(len(rows), np.inf)
    line = np.full(len(rows), "", dtype=object)
    cosmax = np.cos(np.radians(HALF_ANGLE_DEG))
    for plat, plon, wind_from, lid in points:
        toward = np.radians((wind_from + 180.0) % 360.0)
        dx, dy = (lon - plon) * 111.32 * COSL, (lat - plat) * 110.54
        along, dist = dx * np.sin(toward) + dy * np.cos(toward), np.hypot(dx, dy)
        inside = (along > 0) & (dist <= LENGTH_KM) & (np.where(dist > 0, along / np.maximum(dist, 1e-9), 1.0) >= cosmax)
        closer = inside & (dist < near)
        near[closer], line[closer] = dist[closer], lid
    hit = [k for k in range(len(rows)) if np.isfinite(near[k])]
    weight = lambda k: B.WEIGHTS.get(rows[k]["kind"], B.WEIGHTS["other"])  # noqa: E731
    ranked = sorted(hit, key=lambda k: (-weight(k), near[k], rows[k]["name"]))
    counts = Counter(rows[k]["kind"] for k in hit)
    return {
        "by_kind": {k: counts[k] for k in sorted(counts, key=lambda k: (-B.WEIGHTS.get(k, B.WEIGHTS["other"]), k))},
        "score": int(sum(weight(k) for k in hit)),
        "hospitals": int(counts.get("hospital", 0)),
        "n": len(hit),
        "critical": int(sum(B.tier_of(rows[k]["kind"]) <= B.CRITICAL_TIERS for k in hit)),  # tiers 1 to 3
        "homes": int(sum(rows[k].get("units") or 0 for k in hit if rows[k]["kind"] in ("residential", "senior_housing"))),
        "severity": B.severity(rows[ranked[0]]["kind"]) if ranked else None,  # the worst single building decides the level
        "sites": [{"name": rows[k]["name"], "kind": rows[k]["kind"], "source": rows[k].get("source", "osm"),
                   "severity": B.severity(rows[k]["kind"]), "line": str(line[k]), "distance_km": round(float(near[k]), 1),
                   "units": rows[k].get("units")}
                  for k in ranked[:MAX_SITES]],
    }
