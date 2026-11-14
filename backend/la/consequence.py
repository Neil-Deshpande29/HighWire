"""Consequence layer: what is downwind of a line if it sparks. Context for the operator and the agents, NOT an input
to the physics scores (audit: keep consequence separate from the ignition probability).

Downwind cone from the line's riskiest point: bearing = direction the wind blows TOWARD, half-angle 30 deg, length 10 km
(a wind-driven fire's first hours). Counts 2020 Census population (tract centroids) and critical sites (OSM hospitals,
water plants) inside it."""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from . import data as D

LENGTH_KM, HALF_ANGLE_DEG = 10.0, 30.0
COSL = np.cos(np.radians(34.0))


@lru_cache
def _points():
    p = D.population()
    cs = D.grid()["critical_sites"]
    return (p.lat.values, p.lon.values, p["pop"].values.astype(float),
            np.array([c["lat"] for c in cs]), np.array([c["lon"] for c in cs]), cs)


def downwind(lat: float, lon: float, wind_from_deg: float) -> dict:
    plat, plon, pop, clat, clon, cs = _points()
    toward = np.radians((wind_from_deg + 180.0) % 360.0)
    ux, uy = np.sin(toward), np.cos(toward)

    def inside(la, lo):
        dx = (lo - lon) * 111.32 * COSL
        dy = (la - lat) * 110.54
        along = dx * ux + dy * uy
        dist = np.hypot(dx, dy)
        cosang = np.where(dist > 0, along / np.maximum(dist, 1e-9), 1.0)
        return (along > 0) & (dist <= LENGTH_KM) & (cosang >= np.cos(np.radians(HALF_ANGLE_DEG)))

    mp = inside(plat, plon)
    mc = inside(clat, clon)
    sites = [cs[i] for i in np.where(mc)[0]]
    return {"people": int(pop[mp].sum()), "hospitals": sum(s["type"] == "hospital" for s in sites),
            "water_plants": sum(s["type"] != "hospital" for s in sites), "sites": [s["name"] for s in sites[:4]],
            "toward_deg": round(float(np.degrees(toward)) % 360, 0)}


def downwind_union(points: list[tuple[float, float, float]]) -> dict:
    """People / hospitals inside ANY of several downwind cones [(lat, lon, wind_from_deg), ...], each counted once
    (overlapping cones of neighbouring lines would double count if summed). Used by the UI situation card."""
    plat, plon, pop, clat, clon, cs = _points()
    mp = np.zeros(len(plat), bool)
    mc = np.zeros(len(clat), bool)
    cosmax = np.cos(np.radians(HALF_ANGLE_DEG))
    for lat, lon, wfrom in points:
        toward = np.radians((wfrom + 180.0) % 360.0)
        ux, uy = np.sin(toward), np.cos(toward)
        for la, lo, m in ((plat, plon, mp), (clat, clon, mc)):
            dx, dy = (lo - lon) * 111.32 * COSL, (la - lat) * 110.54
            along, dist = dx * ux + dy * uy, np.hypot(dx, dy)
            m |= (along > 0) & (dist <= LENGTH_KM) & (np.where(dist > 0, along / np.maximum(dist, 1e-9), 1.0) >= cosmax)
    sites = [cs[i] for i in np.where(mc)[0]]
    return {"people": int(pop[mp].sum()), "hospitals": int(sum(s["type"] == "hospital" for s in sites)),
            "water_plants": int(sum(s["type"] != "hospital" for s in sites))}
