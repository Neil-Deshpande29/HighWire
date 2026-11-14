"""Risk register for one hour: every line at high fire risk, with how likely it is to cause harm and what the harm
would be, kept in separate columns.

Likelihood is the calibrated fire index against its alarm, with the physical readings behind it (gust, humidity,
clearance to vegetation). Consequence has two sides that are never added together:
  * if the line stays on and sparks: who is downwind (people, hospitals, water plants);
  * if the line is switched off: who loses grid power (customers, buildings by kind).
A 220-500 kV import line is listed without a switch-off column, because plans keep those on and patrol them.

The bands are display thresholds for the screen's matrix, stated in the payload. They change no model and no plan.
"""
from __future__ import annotations

import numpy as np

from . import buildings as B
from . import consequence as C
from . import data as D
from .state import CUSTOMERS_PER_MW, SUBTRANSMISSION_MAX_KV

ELEVATED_FROM = 0.6  # share of the alarm from which a line counts as elevated; the map's orange uses the same step
PEOPLE_HIGH, PEOPLE_MEDIUM = 50_000, 5_000


def exposure_band(dw: dict) -> str:
    """Downwind consequence band: high for a hospital or a large population, medium for a lifeline or a town, else low."""
    if dw["hospitals"] or dw["people"] >= PEOPLE_HIGH:
        return "high"
    return "medium" if dw["water_plants"] or dw["people"] >= PEOPLE_MEDIUM else "low"


def register(st, actions=None) -> dict:
    """The register for `st` with `actions` applied: one row per live overhead line at or above the alarm, riskiest
    first, a count of elevated lines by downwind band, and the likelihood-by-consequence matrix."""
    from site_agent import greenery as G

    sim = st.simulate(actions or [])
    g = st.grid
    alarm = D.calibration()["fire"]["line_alarm"]
    wx, h = st.wx, st.hour_idx
    wdir = wx["wind_direction_10m"][h]
    base_off = st._cut_off(actions or [])
    rows, matrix = [], {"high": {"high": [], "medium": [], "low": []}, "elevated": {"high": 0, "medium": 0, "low": 0}}
    n_elevated = 0
    for k, b in enumerate(g.branches):
        line = sim["lines"][b["id"]]
        if b["cable"] or not line["energized"] or line["fire_risk"] < ELEVATED_FROM * alarm:
            continue
        lat, lon = np.array(b["geometry"]).mean(0)[:2]
        wind_from = float(wdir[int(np.abs(wx["lats"] - lat).argmin()), int(np.abs(wx["lons"] - lon).argmin())])
        dw = C.downwind(float(lat), float(lon), wind_from)
        band = exposure_band(dw)
        if line["fire_risk"] < alarm:
            n_elevated += 1
            matrix["elevated"][band] += 1
            continue
        bulk = b["kv"] >= SUBTRANSMISSION_MAX_KV
        cut = None
        if not bulk:  # what switching this one line off would cost, on top of the actions already taken
            newly = st._cut_off(list(actions or []) + [{"type": "open_line", "target": b["id"]}]) - base_off
            cut = B.outage_impact(sorted(newly))
            cut["customers"] = int(sum(st.buses[x]["load_mw"] for x in newly) * CUSTOMERS_PER_MW)
        matrix["high"][band].append(b["id"])
        rows.append({
            "id": b["id"], "name": b["name"], "kv": b["kv"], "zone": b["zone"], "class": "bulk" if bulk else "sub",
            "index": round(line["fire_risk"], 3), "ratio": round(line["fire_risk"] / alarm, 2),
            "gust_ms": line["gust_ms"], "rh_pct": line["rh_pct"], "clearance_m": line["clearance_m"], "loading_pct": line["loading_pct"],
            "downwind": dw, "exposure_band": band, "if_cut": cut, "greenery": G.line_greenery(b["id"]),
        })
    rows.sort(key=lambda r: -r["index"])
    for band in matrix["high"]:
        matrix["high"][band].sort(key=lambda lid: -next(r["index"] for r in rows if r["id"] == lid))
    return {"t_utc": st.t_utc.isoformat(), "alarm": alarm, "elevated_from": round(ELEVATED_FROM * alarm, 3), "n_high": len(rows),
            "n_elevated": n_elevated, "rows": rows, "matrix": matrix,
            "bands": {"people_high": PEOPLE_HIGH, "people_medium": PEOPLE_MEDIUM, "cone_km": C.LENGTH_KM}}
