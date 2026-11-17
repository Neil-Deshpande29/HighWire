"""Weather stations: what the airport sensors measured, next to the weather the model ran on.

The fire and storm models run on gridded weather (Open-Meteo reanalysis for replays, its forecast for "Now"). Stations
are the check on that input. Where a station measured stronger gusts than the grid gave the model, the risk near that
station is probably understated, and the screen says so instead of hiding it.

Replays read the recorded ASOS archive already in the repo (data/processed/asos_la.csv.gz, from Iowa State's
mesonet). Only reports up to the hour on screen are read, so nothing here leaks what happened later. The live forecast
asks the National Weather Service for each station's latest report.

Conventions match validation/common.py (check V2): a station's gust for hour HH is the largest of its reported gusts
and sustained winds in the hour ending at HH (Open-Meteo's hourly values describe the preceding hour); temperature
and humidity are the last report in that hour; gusts above 45 m/s or below the sustained wind are dropped as bad
readings; KPMD sits outside the weather grid and is left out.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd

from . import data as D
from . import engine as E

STATIONS = {
    "KBUR": "Burbank Airport", "KCQT": "Downtown Los Angeles (USC)", "KEMT": "El Monte Airport",
    "KLAX": "Los Angeles International Airport", "KLGB": "Long Beach Airport", "KSMO": "Santa Monica Airport",
    "KVNY": "Van Nuys Airport", "KWHP": "Whiteman Airport, Pacoima",
}
# A measured gust this far above the model's is flagged. About the model's own error in windy hours (V2: RMSE 5.9 m/s).
GUST_TOL_MS = 5.0
RH_TOL_PCT = 15.0  # measured humidity this far below the model's is flagged: the air is drier than the model assumed
DRY_BELOW_PCT = 30.0  # ...but only when the measured air is itself dry; a gap at 45% humidity changes nothing for fire
MAX_GUST_MS = 45.0
LIVE_URL = "https://api.weather.gov/stations/{id}/observations/latest"
LIVE_TTL_S = 600  # ask each station at most every ten minutes
LIVE_MAX_AGE_H = 2.0  # an older "latest" report counts as not reporting
ARCHIVE_SOURCE = "ASOS airport weather stations (Iowa Environmental Mesonet archive)"
LIVE_SOURCE = "National Weather Service station reports"

_live: dict = {"at": 0.0, "obs": {}}


def _rows() -> pd.DataFrame:
    """The archive's reports for our stations, in file order. Read in pieces, with the station held as a category:
    read whole (D.asos), the 4.9 million rows take about 1 GB of memory, which the hosted copy does not have."""
    ours = pd.CategoricalDtype(list(STATIONS))
    parts = []
    for c in pd.read_csv(D.P / f"asos_{D.REGION}.csv.gz", parse_dates=["valid"], dtype={"station": "category"}, chunksize=400_000):
        c = c[c.station.isin(list(STATIONS))]
        parts.append(c.assign(station=c.station.astype(ours)))  # one set of categories, so the pieces join as a category
    a = pd.concat(parts, ignore_index=True)
    a["valid"] = a["valid"].dt.tz_localize("UTC")
    return a


@lru_cache(maxsize=1)
def _archive():
    """Recorded reports for the stations inside the weather grid, bad gusts removed, in time order."""
    a = _rows().sort_values("valid").reset_index(drop=True)
    gust = a.gust_ms.where((a.gust_ms <= MAX_GUST_MS) & ~(a.gust_ms < a.wind_ms))
    wind = a.wind_ms.where(a.wind_ms <= MAX_GUST_MS)
    a = a.assign(gust_ms=gust, wind_ms=wind, g=np.fmax(gust, wind))
    return a, a.valid.dt.tz_convert(None).to_numpy().astype("datetime64[ns]")


@lru_cache(maxsize=1)
def places() -> dict[str, tuple[float, float]]:
    """Station id -> (lat, lon), from the archive."""
    first = _archive()[0].groupby("station", observed=True)[["lat", "lon"]].first()  # a station never moves
    return {s: (float(r.lat), float(r.lon)) for s, r in first.iterrows()}


def _num(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 1)


def recorded(t_utc) -> dict[str, dict]:
    """What each station reported in the hour ending at `t_utc`. Stations with no report in that hour are absent."""
    a, valid = _archive()
    ts = pd.Timestamp(t_utc)
    end = (ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo else ts).to_datetime64()
    lo, hi = np.searchsorted(valid, end - np.timedelta64(1, "h"), "right"), np.searchsorted(valid, end, "right")
    out = {}
    for sid, rows in a.iloc[lo:hi].groupby("station"):
        last = lambda col: rows[col].dropna().iloc[-1] if rows[col].notna().any() else None  # noqa: E731
        gust = rows.g.max()
        if not np.isfinite(gust):
            continue
        out[sid] = {"at_utc": rows.valid.iloc[-1].isoformat(), "gust_ms": _num(gust), "wind_ms": _num(last("wind_ms")),
                    "rh_pct": _num(last("relh")), "temp_c": _num(last("tmpc"))}
    return out


def _fetch_latest(sid: str) -> dict | None:
    """One station's latest report from the National Weather Service, or None when it cannot be read."""
    import requests

    try:
        r = requests.get(LIVE_URL.format(id=sid), timeout=8,
                         headers={"User-Agent": "rewire-hackathon-research/0.1", "Accept": "application/geo+json"})
        r.raise_for_status()
        p = r.json()["properties"]
    except Exception:  # noqa: BLE001  any network or format problem means "no report"
        return None
    val = lambda k: (p.get(k) or {}).get("value")  # noqa: E731
    kmh = lambda v: None if v is None else v / 3.6  # noqa: E731  the service reports wind in km/h
    wind, gust = kmh(val("windSpeed")), kmh(val("windGust"))
    best = max([v for v in (wind, gust) if v is not None], default=None)
    if best is None or not p.get("timestamp"):
        return None
    return {"at_utc": p["timestamp"], "gust_ms": _num(best), "wind_ms": _num(wind), "rh_pct": _num(val("relativeHumidity")),
            "temp_c": _num(val("temperature"))}


def live(fetch=None, now: float | None = None) -> dict[str, dict]:
    """Each station's latest report, fresh enough to use. Asked at most every LIVE_TTL_S; `fetch` is injectable for tests."""
    now = time.time() if now is None else now
    if fetch is None and now - _live["at"] < LIVE_TTL_S:
        return _live["obs"]
    ids = list(STATIONS)
    with ThreadPoolExecutor(max_workers=len(ids)) as pool:
        got = dict(zip(ids, pool.map(fetch or _fetch_latest, ids)))
    obs = {}
    for sid, o in got.items():
        if not o:
            continue
        age_h = (pd.Timestamp(now, unit="s", tz="UTC") - pd.Timestamp(o["at_utc"]).tz_convert("UTC")).total_seconds() / 3600
        if age_h <= LIVE_MAX_AGE_H:
            obs[sid] = o
    if fetch is None:
        _live.update(at=now, obs=obs)
    return obs


def _model_at(wx: dict, hour_idx: np.ndarray, ids: list[str]) -> list[dict]:
    """The gridded weather the model used, at each station, for that station's hour index."""
    pts = places()
    lats, lons = np.array([pts[s][0] for s in ids]), np.array([pts[s][1] for s in ids])
    w = E.interp_weights(lats, lons, wx["lats"], wx["lons"])
    pick = lambda var: E.bilinear(wx[var][hour_idx], w)[np.arange(len(ids)), np.arange(len(ids))]  # noqa: E731
    gust, wind, rh, temp = pick("wind_gusts_10m"), pick("wind_speed_10m"), pick("relative_humidity_2m"), pick("temperature_2m")
    return [{"gust_ms": _num(gust[k]), "wind_ms": _num(wind[k]), "rh_pct": _num(rh[k]), "temp_c": _num(temp[k])} for k in range(len(ids))]


def check(st, fetch=None, now: float | None = None, offline: bool = False) -> dict:
    """Measured against modelled weather for the hour of `st` (a replay), or for right now (the live forecast).

    Each station row carries what it measured, what the model used at that spot and hour, and a flag:
    `model_low` (measured gusts stronger than the model's: risk nearby is probably understated), `model_high` (the
    cautious side), `agrees`, or `no_report`. `drier` marks dry air (30% humidity or less) that is also well below what the model assumed.
    `offline` never asks the network: a live check then uses the reports already held (the planner calls it this way).
    """
    is_live = st.eid == "FX"
    obs = (_live["obs"] if offline else live(fetch, now)) if is_live else recorded(st.t_utc)
    ids = sorted(STATIONS)
    wx_time = st.wx["time"].astype("datetime64[h]")
    if is_live:  # compare each report with the forecast hour that covers the moment it was taken
        hours = []
        for sid in ids:
            at = pd.Timestamp(obs[sid]["at_utc"]).tz_convert("UTC").tz_localize(None).ceil("h") if sid in obs else None
            hours.append(st.hour_idx if at is None else int(np.clip(np.searchsorted(wx_time, at.to_datetime64()), 0, len(wx_time) - 1)))
        hour_idx = np.array(hours)
    else:
        hour_idx = np.full(len(ids), st.hour_idx)
    model = _model_at(st.wx, hour_idx, ids)
    pts = places()
    rows = []
    for sid, m in zip(ids, model):
        o = obs.get(sid)
        row = {"id": sid, "name": STATIONS[sid], "lat": pts[sid][0], "lon": pts[sid][1], "at_utc": o["at_utc"] if o else None,
               "measured": o and {k: o[k] for k in ("gust_ms", "wind_ms", "rh_pct", "temp_c")}, "model": m,
               "gust_diff_ms": None, "rh_diff_pct": None, "flag": "no_report", "drier": False}
        if o and m["gust_ms"] is not None:
            diff = m["gust_ms"] - o["gust_ms"]
            row["gust_diff_ms"] = round(diff, 1)
            if o["rh_pct"] is not None and m["rh_pct"] is not None:
                row["rh_diff_pct"] = round(m["rh_pct"] - o["rh_pct"], 0)
            row["flag"] = "model_low" if diff <= -GUST_TOL_MS else "model_high" if diff >= GUST_TOL_MS else "agrees"
            row["drier"] = (o["rh_pct"] is not None and m["rh_pct"] is not None and o["rh_pct"] <= DRY_BELOW_PCT
                            and m["rh_pct"] - o["rh_pct"] >= RH_TOL_PCT)
        rows.append(row)
    reporting = [r for r in rows if r["flag"] != "no_report"]
    low = [r["id"] for r in rows if r["flag"] == "model_low"]
    drier = [r["id"] for r in rows if r["drier"]]
    return {
        "mode": "live" if is_live else "recorded", "source": LIVE_SOURCE if is_live else ARCHIVE_SOURCE,
        "t_utc": st.t_utc.isoformat(), "stations": rows, "n": len(rows), "reporting": len(reporting),
        "agree": sum(r["flag"] == "agrees" for r in rows), "model_low": low,
        "model_high": [r["id"] for r in rows if r["flag"] == "model_high"], "drier": drier,
        "level": "none" if not reporting else "warn" if low or drier else "ok",
        "tolerance": {"gust_ms": GUST_TOL_MS, "rh_pct": RH_TOL_PCT},
    }
