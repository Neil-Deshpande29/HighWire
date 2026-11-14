"""Shared helpers for calibration and the validation checks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.la import data as D  # noqa: E402
from backend.la import engine as E  # noqa: E402

RES = D.results_dir()
STATION_ZONE_EXCLUDE = {"KPMD"}  # Palmdale is outside the bbox (north of the San Gabriels)
TZ = "America/Los_Angeles"


def save(name: str, obj: dict):
    (RES / f"{name}.json").write_text(json.dumps(obj, indent=1, default=float))


def zone_at(lat, lon):
    """Same zone rule as data/build_grid.py, from the processed terrain."""
    from scipy.ndimage import distance_transform_edt, map_coordinates

    T = D.terrain()
    el, la, lo = T["elev"], T["lats"], T["lons"]
    cell = (la[1] - la[0]) * 110540
    ds = distance_transform_edt(~(el <= 0)) * cell
    gy, gx = np.gradient(el, cell, (lo[1] - lo[0]) * 111320 * np.cos(np.radians(34)))
    sl = np.degrees(np.arctan(np.hypot(gx, gy)))
    c = [(np.atleast_1d(lat) - la[0]) / (la[1] - la[0]), (np.atleast_1d(lon) - lo[0]) / (lo[1] - lo[0])]
    e, d, s = (map_coordinates(a, c, order=1, mode="nearest") for a in (el, ds, sl))
    return np.where(e >= 500, "mountain", np.where((e >= 300) & (s >= 10), "mountain",
                    np.where(e >= 150, "valley", np.where((d < 4000) & (e < 60), "coast", "basin"))))


def station_hourly(start_utc, end_utc) -> pd.DataFrame:
    """ASOS hourly: METAR at HH:53 is assigned to hour HH+1 (Open-Meteo hourly values describe the preceding hour).
    gust = max(reported gust, sustained wind) within the hour; temp/rh/wind = last obs in the hour."""
    a = D.asos()
    a = a[(a.valid >= pd.Timestamp(start_utc)) & (a.valid < pd.Timestamp(end_utc)) & ~a.station.isin(STATION_ZONE_EXCLUDE)].copy()
    # QC: drop physically implausible gusts (> 45 m/s ~ 100 mph at these airport stations) and gusts below the mean wind
    bad = (a.gust_ms > 45) | (a.gust_ms < a.wind_ms)
    a.loc[bad, "gust_ms"] = np.nan
    a.loc[a.wind_ms > 45, "wind_ms"] = np.nan
    a = a.assign(hour=a.valid.dt.ceil("h"), g=np.fmax(a.gust_ms.fillna(-1), a.wind_ms.fillna(-1)).replace(-1, np.nan))
    h = a.groupby(["station", "hour"]).agg(lat=("lat", "first"), lon=("lon", "first"), gust_ms=("g", "max"),
                                           wind_ms=("wind_ms", "last"), tmpc=("tmpc", "last"), relh=("relh", "last")).reset_index()
    return h


def grid_at_points(wx, lats, lons, var, hours_idx=None):
    w = E.interp_weights(np.asarray(lats), np.asarray(lons), wx["lats"], wx["lons"])
    arr = wx[var] if hours_idx is None else wx[var][hours_idx]
    return E.bilinear(arr, w)


def pair_station_grid(eid, cal=None, var_obs="gust_ms", var_grid="wind_gusts_10m"):
    """Station obs vs gridded values at the station location, for one event window (+pad). Long DataFrame."""
    wx = D.weather(eid)
    t0 = pd.Timestamp(wx["time"][0]).tz_localize("UTC")
    t1 = pd.Timestamp(wx["time"][-1]).tz_localize("UTC")
    st = station_hourly(t0, t1 + pd.Timedelta(hours=1))
    meta = st.groupby("station")[["lat", "lon"]].first()
    vals = grid_at_points(wx, meta.lat.values, meta.lon.values, var_grid)  # (T, nst)
    times = pd.DatetimeIndex(wx["time"].astype("datetime64[ns]")).tz_localize("UTC")
    g = pd.DataFrame(vals, index=times, columns=meta.index).stack().rename("grid").reset_index()
    g.columns = ["hour", "station", "grid"]
    m = st.merge(g, on=["hour", "station"])
    m["zone"] = m.station.map(dict(zip(meta.index, zone_at(meta.lat.values, meta.lon.values))))
    m["event"] = eid
    return m


def apply_gust(cal, zone, g):
    return np.maximum(np.array([cal["gust"]["a"][z] for z in zone]) + np.array([cal["gust"]["b"][z] for z in zone]) * g, 0)


def la_outages():
    """LA County hourly customers out (EAGLE-I)."""
    return D.outages()["06037"]


def event_hindcast(eid, cal=None, grid=None, thermal=True):
    """Hindcast an event's core window (no pad). Returns (times UTC DatetimeIndex, result dict)."""
    grid = grid or E.LAGrid()
    ev = D.events()[eid]
    wx = D.weather(eid)
    hrs = E.hours_for(wx, ev["start_utc"], ev["end_utc"])
    ld, sc = E.event_demand(wx["time"][hrs])
    r = E.hindcast(grid, wx, hrs, ld, sc, cal=cal, thermal=thermal)
    times = pd.DatetimeIndex(wx["time"][hrs].astype("datetime64[ns]")).tz_localize("UTC")
    return times, r


def outage_baseline(eid, county="06037"):
    """Median customers out over the configured days before the event start (EAGLE-I)."""
    ev = D.events()[eid]
    days = D.config()["outage"]["baseline_days_before"]
    o = D.outages()[county]
    s = pd.Timestamp(ev["start_utc"])
    return float(o[(o.index >= s - pd.Timedelta(days=days)) & (o.index < s)].median())


def local_hour_features(idx_utc: pd.DatetimeIndex):
    loc = idx_utc.tz_convert(TZ)
    return loc.hour.values, (loc.dayofweek.values >= 5).astype(int), loc


def pop_weighted_temp(wx_series, hours_idx=None):
    """Population-weighted 2 m temperature over the region from the coarse series grid."""
    pop = D.population()
    lats, lons = wx_series["lats"], wx_series["lons"]
    j = np.clip(np.round((pop.lat.values - lats[0]) / (lats[1] - lats[0])).astype(int), 0, len(lats) - 1)
    i = np.clip(np.round((pop.lon.values - lons[0]) / (lons[1] - lons[0])).astype(int), 0, len(lons) - 1)
    w = np.zeros((len(lats), len(lons)))
    np.add.at(w, (j, i), pop["pop"].values)
    w /= w.sum()
    T = wx_series["temperature_2m"] if hours_idx is None else wx_series["temperature_2m"][hours_idx]
    return (T * w[None]).sum((1, 2))


def first_spike(series_vals, threshold, sustain=3):
    """Index of the first hour starting a run of >= `sustain` consecutive hours above threshold (None if never)."""
    above = np.asarray(series_vals) > threshold
    run = 0
    for i, a in enumerate(above):
        run = run + 1 if a else 0
        if run >= sustain:
            return i - sustain + 1
    return None


def nudge_loso(m: pd.DataFrame, L_km: float, w0: float) -> pd.DataFrame:
    """Station nudging (what the wind agent does with live ASOS): each hour, the gridded gust at a station is shifted by
    a distance-weighted mean of the OTHER stations' (obs - grid) residuals, weight exp(-d/L_km), shrunk by w0.
    Leave-one-station-out by construction (a station never sees its own observation), so it is a fair accuracy test.
    m = pair_station_grid(...) output. Returns long DataFrame hour, station, obs, grid, nudged."""
    st = m.groupby("station")[["lat", "lon"]].first()
    dx = (st.lon.values - st.lon.values[:, None]) * 111.32 * np.cos(np.radians(34))
    dy = (st.lat.values - st.lat.values[:, None]) * 110.54
    W = np.exp(-np.hypot(dx, dy) / L_km)
    np.fill_diagonal(W, 0.0)
    po = m.pivot_table(index="hour", columns="station", values="gust_ms").reindex(columns=st.index)
    pg = m.pivot_table(index="hour", columns="station", values="grid").reindex(columns=st.index)
    R = (po - pg).values
    ok = np.isfinite(R)
    corr = (np.nan_to_num(R) @ W.T) / (ok.astype(float) @ W.T + w0)
    nud = np.maximum(pg.values + corr, 0.0)
    out = pd.DataFrame({"hour": np.repeat(po.index.values, po.shape[1]), "station": np.tile(st.index.values, po.shape[0]),
                        "obs": po.values.ravel(), "grid": pg.values.ravel(), "nudged": nud.ravel()})
    return out.dropna(subset=["obs", "grid"])


def series_storm(cal=None) -> pd.DataFrame:
    """Hourly storm hazards over 2019-2024 on the coarse series grid, for every candidate damage onset (cached)."""
    from backend.la import storm as S

    cal = cal or D.calibration()
    cache = RES / f"series_storm_2019_2024_c{cal['fire']['c50']:g}.csv"
    if cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    g = E.LAGrid()
    s = D.weather("series_2019_2024")
    pr = np.load(D.P / "weather" / "series_precip_2019_2024.npz")["precipitation"]
    T = len(s["time"])
    cols = {f"H{o}": np.zeros(T) for o in S.ONSET_GRID}
    cols["Rn"] = np.zeros(T)
    for a in range(0, T, 744):
        idx = np.arange(a, min(a + 744, T))
        hz = S.hazards(g, s, idx, pr, onsets=list(S.ONSET_GRID), cal=cal)
        for o in S.ONSET_GRID:
            cols[f"H{o}"][idx] = hz["H"][o]
        cols["Rn"][idx] = hz["Rn"]
    cols["max_gust_raw"] = s["wind_gusts_10m"].reshape(T, -1).max(1)
    times = pd.DatetimeIndex(s["time"].astype("datetime64[ns]")).tz_localize("UTC")
    df = pd.DataFrame(cols, index=times)
    df.to_csv(cache)
    return df


def auc(score, label):
    m = np.isfinite(score) & np.isfinite(label)
    s, y = score[m], label[m].astype(bool)
    if y.sum() == 0 or (~y).sum() == 0:
        return np.nan
    r = pd.Series(s).rank().values
    return float((r[y].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (~y).sum()))
