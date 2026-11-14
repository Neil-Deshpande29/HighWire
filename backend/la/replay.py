"""Serve the LA replay to the UI. Hourly numbers come from validation/results/replay_<EVENT>.npz (precomputed by
validation/build_replays.py); static geometry from data/processed. Nothing here calls an LLM."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

from . import data as D
from . import forecast as FC

COUNTIES = ["06037", "06111", "06059", "06071"]
DRY_RAIN = float(1 / (1 + np.exp(1.0)))  # rain hazard with zero rain (sigmoid floor); shown as 0% heavy rain
COUNTY_NAMES = {"06037": "Los Angeles", "06111": "Ventura", "06059": "Orange", "06071": "San Bernardino"}


def _r(x, nd=2):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


@lru_cache
def load(eid: str) -> dict:
    if eid == "FX":  # live forecast file is rebuilt in place: never cache it across refreshes
        load.cache_clear()
    z = np.load(D.results_dir() / f"replay_{eid}.npz")
    d = {k: z[k] for k in z.files}
    d["time"] = d["time"].astype("datetime64[h]")
    return d


def events_list():
    v4a = _json("v4a_replay")
    out = []
    for eid, e in D.events().items():
        rp = D.results_dir() / f"replay_{eid}.npz"
        out.append({"id": eid, "name": e["name"], "kind": e["kind"], "split": e["split"], "start": e["start"], "end": e["end"],
                    "has_replay": rp.exists(), "fires": e.get("fires", []), "v4a": (v4a or {}).get("events", {}).get(eid)})
    lv = FC.live_event()
    if lv and (D.results_dir() / "replay_FX.npz").exists():
        out.insert(0, {"id": "FX", "name": lv["name"], "kind": "forecast", "split": "none", "start": lv["start"], "end": lv["end"],
                       "has_replay": True, "fires": [], "v4a": None, "fetched_utc": lv["fetched_utc"]})
    return out


def event_meta(eid: str) -> dict:
    return FC.live_event() if eid == "FX" else D.events()[eid]


def _json(name):
    f = D.results_dir() / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else None


@lru_cache
def static() -> dict:
    g = D.grid()
    T = D.terrain()
    step = 2
    elev = T["elev"][::step, ::step]
    rf = D.redflag()
    b = g["bbox"]

    def simplify(geom):
        parts = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        out = []
        for p in parts:
            ring = np.array(p[0])
            if ring[:, 0].max() < b["lon_min"] - 0.2 or ring[:, 0].min() > b["lon_max"] + 0.2 or ring[:, 1].max() < b["lat_min"] - 0.2 or ring[:, 1].min() > b["lat_max"] + 0.2:
                continue
            k = max(1, len(ring) // 150)
            out.append([[round(x, 4), round(y, 4)] for x, y in ring[::k]])
        return out

    zones = {u: simplify(z["geometry"]) for u, z in rf["zones"].items()}
    zones = {u: z for u, z in zones.items() if z}
    fires = []
    for fid, f in D.fires().items():
        per = None
        if f.get("perimeter"):
            gp = f["perimeter"]
            parts = gp["coordinates"] if gp["type"] == "MultiPolygon" else [gp["coordinates"]]
            big = max(parts, key=lambda p: len(p[0]))[0]
            k = max(1, len(big) // 200)
            per = [[round(x, 4), round(y, 4)] for x, y in big[::k]]
        fires.append({"id": fid, "name": f["name"], "event": f["event"], "lat": f["lat"], "lon": f["lon"],
                      "ignition_utc": f["ignition_utc"].isoformat(), "utility_related": f["utility_related"],
                      "facility": f["facility"], "source_url": f["source_url"], "perimeter": per})
    big_subs = sorted([x for x in g["buses"] if x["kind"] == "substation" and x["kv"] >= 220 and not x["name"].startswith("Substation")],
                      key=lambda x: -x["kv"])
    return {
        "region": g["region"], "bbox": b, "proj": g["proj"], "modeled_note": g["modeled_note"],
        "terrain": {"lats": T["lats"][::step].round(4).tolist(), "lons": T["lons"][::step].round(4).tolist(),
                    "elev": np.round(elev).astype(int).tolist()},
        "branches": [{"id": l["id"], "kv": l["kv"], "kv_class": l["kv_class"], "cable": l["cable"], "name": l["name"], "zone": l["zone"],
                      "from_bus": l["from_bus"], "to_bus": l["to_bus"], "length_km": round(l["length_m"] / 1000, 2),
                      "rating_a": l["rating_a"], "geometry": l["geometry"]} for l in g["branches"]],
        "buses": [{"id": x["id"], "name": x["name"], "lat": x["lat"], "lon": x["lon"], "kv": x["kv"], "kind": x["kind"]} for x in g["buses"]],
        "labels": [{"id": x["id"], "name": x["name"], "lat": x["lat"], "lon": x["lon"], "kv": x["kv"]} for x in big_subs[:18]],
        "critical_sites": g["critical_sites"],
        "redflag_zones": zones,
        "fires": fires,
        "calibration": {"fire": D.calibration()["fire"], "storm": D.calibration().get("storm")},
    }


def _downwind(d, h, k):
    """People / critical sites in the 10 km downwind cone of line k at hour h (consequence layer, context only)."""
    from . import consequence as C

    br = D.grid()["branches"][k]
    lat, lon = np.array(br["geometry"]).mean(0)[:2]
    j = int(np.abs(d["wx_lats"] - lat).argmin())
    i = int(np.abs(d["wx_lons"] - lon).argmin())
    return C.downwind(float(lat), float(lon), float(d["wx_dir"][h][j, i]))


@lru_cache
def _redflag_windows():
    """Red Flag warnings parsed once (audit: timestamps were re-parsed with pandas on every request)."""
    return [(w["ugc"], pd.Timestamp(w["issue"]), pd.Timestamp(w["expire"])) for w in D.redflag()["events"]]


def _hour_index(d, t: str | None, i: int | None):
    n = len(d["time"])
    if i is not None:
        return max(0, min(n - 1, int(i)))
    if t:
        ts = np.datetime64(pd.Timestamp(t).tz_convert("UTC").tz_localize(None) if pd.Timestamp(t).tzinfo else pd.Timestamp(t), "h")
        return int(np.clip(np.searchsorted(d["time"], ts), 0, n - 1))
    return 0


def hour(eid: str, t: str | None = None, i: int | None = None) -> dict:
    d = load(eid)
    h = _hour_index(d, t, i)
    cal = D.calibration()["fire"]
    tt = pd.Timestamp(d["time"][h]).tz_localize("UTC")
    risk = d["line_risk"][h]
    g = D.grid()
    order = np.argsort(-risk)
    over = [int(k) for k in order if risk[k] >= cal["line_alarm"]]
    active = sorted({u for u, a, b in _redflag_windows() if a <= tt < b})
    fires = []
    for f in static()["fires"]:
        if f["event"] == eid:
            ig = pd.Timestamp(f["ignition_utc"])
            fires.append({"id": f["id"], "name": f["name"], "ignited": bool(tt >= ig.floor("h")),
                          "hours_since_ignition": round((tt - ig).total_seconds() / 3600, 1)})
    out = d["outages"][h]
    storm = None
    if "storm_index" in d:  # wind agent: storm-damage model (backend/la/storm.py)
        st_cal = D.calibration().get("storm") or {}
        dmg = d["line_damage"][h]
        top = [int(k) for k in np.argsort(-dmg)[:10] if dmg[k] >= 0.2]
        storm = {"index": _r(d["storm_index"][h], 3), "alarm": _r(st_cal.get("alarm"), 3),
                 "wind_hazard_pct": _r(100 * d["storm_hazard"][h], 1), "rain_hazard_pct": _r(100 * max(0.0, (float(d["rain_hazard"][h]) - DRY_RAIN) / (1 - DRY_RAIN)), 1),
                 "n_lines_damage_likely": int((dmg >= 0.5).sum()),
                 "top_lines": [{"id": g["branches"][k]["id"], "name": g["branches"][k]["name"], "kv": g["branches"][k]["kv"],
                                "zone": g["branches"][k]["zone"], "p_damage": _r(dmg[k], 2), "gust": _r(d["line_gust"][h][k], 1)} for k in top],
                 "max_precip_mm_h": _r(float(d["wx_precip"][h].max()), 1)}
    return {
        "event": eid, "i": h, "n": len(d["time"]), "t_utc": tt.isoformat(), "t_local": tt.tz_convert("America/Los_Angeles").isoformat(),
        "lines": {"risk": np.round(risk, 3).tolist(), "loading": np.round(d["loading"][h], 1).tolist(),
                  "temp": np.round(d["line_temp"][h], 1).tolist(), "clear": np.round(d["line_clear"][h], 2).tolist(),
                  "gust": np.round(d["line_gust"][h], 1).tolist(), "rh": np.round(d["line_rh"][h], 0).tolist(),
                  "flow": np.round(d["flow_mw"][h], 1).tolist()},
        "region_index": _r(d["region_index"][h], 1), "region_alarm": cal["region_alarm"], "line_alarm": cal["line_alarm"],
        "load_mw": _r(d["load_mw"][h], 0), "demand": {"LDWP": _r(d["demand_ldwp"][h], 0), "SCE": _r(d["demand_sce"][h], 0)},
        "outages": {COUNTY_NAMES[c]: _r(out[k], 0) for k, c in enumerate(COUNTIES)},
        "over_alarm": [{"id": g["branches"][k]["id"], "name": g["branches"][k]["name"], "kv": g["branches"][k]["kv"],
                        "zone": g["branches"][k]["zone"], "risk": _r(risk[k], 2), "gust": _r(d["line_gust"][h][k], 1),
                        "rh": _r(d["line_rh"][h][k], 0), "downwind": _downwind(d, h, k)} for k in over[:25]],
        "n_over_alarm": len(over),
        "max_loading": _r(float(np.nanmax(d["loading"][h])), 0),
        "n_overloaded": int((d["loading"][h] > 100).sum()),
        "weather": {"lats": d["wx_lats"].tolist(), "lons": d["wx_lons"].tolist(), "gust": np.round(d["wx_gust"][h], 1).tolist(),
                    "rh": np.round(d["wx_rh"][h], 0).tolist(), "temp": np.round(d["wx_temp"][h], 1).tolist(),
                    "dir": np.round(d["wx_dir"][h], 0).tolist(), "max_gust": _r(d["wx_gust"][h].max(), 1),
                    "min_rh": _r(d["wx_rh"][h].min(), 0), "max_temp": _r(d["wx_temp"][h].max(), 1)},
        "redflag_active": active, "fires": fires, "storm": storm,
        "line_damage": np.round(d["line_damage"][h], 2).tolist() if "line_damage" in d else None,
        "source": f"validation/results/replay_{eid}.npz",
    }


def series(eid: str) -> dict:
    d = load(eid)
    v4a = (_json("v4a_replay") or {}).get("events", {}).get(eid)
    return {"event": eid, "t_utc": [pd.Timestamp(x).tz_localize("UTC").isoformat() for x in d["time"]],
            "risk_index": np.round(d["region_index"], 1).tolist(),
            "storm_index": np.round(d["storm_index"], 3).tolist() if "storm_index" in d else None,
            "storm_alarm": (D.calibration().get("storm") or {}).get("alarm"),
            "la_out": [None if not np.isfinite(x) else float(x) for x in d["outages"][:, 0]],
            "four_county_out": [None if not np.isfinite(x) else float(x) for x in np.nansum(d["outages"], 1)],
            "load_mw": np.round(d["load_mw"], 0).tolist(),
            "max_loading": np.round(np.nanmax(d["loading"], 1), 1).tolist(),
            "region_alarm": D.calibration()["fire"]["region_alarm"], "v4a": v4a,
            "fires": [f for f in static()["fires"] if f["event"] == eid],
            "now_utc": (FC.live_event() or {}).get("fetched_utc") if eid == "FX" else None,
            "source": f"validation/results/replay_{eid}.npz, validation/results/v4a_replay.json"}


def validation_summary() -> dict:
    s = _json("summary") or {"cards": []}
    s["report_path"] = "validation/REPORT.md"
    s["calibration"] = _json("calibration_summary")
    return s


def event_context(eid: str, t: str | None = None) -> dict:
    """Compact facts for the agents (no arrays): what the replay and validation files say about this hour/event."""
    h = hour(eid, t)
    ev = event_meta(eid)
    v4a = (_json("v4a_replay") or {}).get("events", {}).get(eid)
    v5 = [c for c in (_json("v5_ignition") or {}).get("cases", []) if c["event"] == eid]
    return {"event": eid, "name": ev["name"], "split": ev["split"], "t_local": h["t_local"], "region_index": h["region_index"],
            "region_alarm": h["region_alarm"], "lines_over_alarm": h["n_over_alarm"], "top_lines": h["over_alarm"][:8],
            "actual_customers_out": h["outages"], "weather": {k: h["weather"][k] for k in ("max_gust", "min_rh", "max_temp")},
            "redflag_zones_active": h["redflag_active"], "fires": h["fires"],
            "storm_damage": h["storm"],
            "validation_for_event": {"v4a": v4a, "v5_cases": v5}, "sources": [h["source"], "validation/results/v4a_replay.json"]}
