"""V8. PREDICTION test: the same calibrated models driven by archived weather FORECASTS instead of recorded weather.
`<var>_previous_dayN` (Open-Meteo Previous Runs API) is what the forecast issued about N days earlier said for that hour,
i.e. what an operator could actually have known in advance. Nothing is refit here: calibration.json is used as is.

Part A (E3, Jan 2025 Eaton/Hurst/PSPS, test event): day-1 and day-2 ahead forecasts.
  * fire / storm alarm timing vs the first sustained LA County outage spike (warning time = alarm-vs-spike gap + 24 h x lead)
  * rank of the line nearest each test ignition (Eaton, Hurst) at ignition time, from the forecast
Part B (2024-01-20 .. 2024-12-31, every hour): day-ahead storm index vs the top-1% LA County outage hours (same
  threshold as V4b), against the same day-ahead forecast's max gust (same look-back rule as V4b).
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

from common import D, E, RES, auc, event_hindcast, first_spike, outage_baseline, save  # noqa: E402
from backend.la import storm as S  # noqa: E402
from checks.v5_ignition import pct_rank  # noqa: E402


def _load(name):
    z = np.load(D.P / "weather" / f"{name}.npz")
    d = {k: z[k] for k in z.files}
    d["time"] = d["time"].astype("datetime64[h]")
    return d


def _event_part(grid, cal, eid="E3"):
    ev = D.events()[eid]
    mult = D.config()["outage"]["spike_multiple"]
    base = outage_baseline(eid)
    out = {}
    for lead in (0, 1, 2):
        name = eid if lead == 0 else f"{eid}_fc{lead}"
        if not (D.P / "weather" / f"{name}.npz").exists():
            continue
        wx = D.weather(eid) if lead == 0 else _load(name)
        times, r = event_hindcast(eid, cal=cal, grid=grid, wx=wx)
        sti = S.event_storm(grid, eid, cal, wx=wx)["storm_index"]
        la = D.outages()["06037"].reindex(times).values
        ok = np.isfinite(la)
        fs = first_spike(np.nan_to_num(la), mult * base, sustain=3)
        spike_t = times[fs] if fs is not None else None
        # exploratory (same post-hoc metric as V4a, labeled as such): main surge = first hour >= 50% of the event peak
        surge_t = times[int(np.argmax(np.nan_to_num(la) >= 0.5 * np.nanmax(la)))] if ok.any() else None

        def alarm_info(series, thr):
            idx = np.where(series >= thr)[0]
            if not len(idx):
                return None, None, None, None
            t = times[idx[0]]
            gap = (spike_t - t).total_seconds() / 3600 if spike_t is not None else None
            warn = gap + 24 * lead if gap is not None else None
            warn_surge = (surge_t - t).total_seconds() / 3600 + 24 * lead if surge_t is not None else None
            return str(t), gap, warn, warn_surge

        fa = alarm_info(r["region_index"], cal["fire"]["region_alarm"])
        sa = alarm_info(sti, cal["storm"]["alarm"])
        cosl = np.cos(np.radians(34))
        overhead = ~grid.cable
        idx_oh = np.where(overhead)[0]
        ranks = []
        for fid, f in D.fires().items():
            if f["event"] != eid:
                continue
            t = pd.Timestamp(f["ignition_utc"]).floor("h")
            if t not in times:
                continue
            h = int(np.where(times == t)[0][0])
            dist = np.hypot(grid.tlat - f["lat"], (grid.tlon - f["lon"]) * cosl) * 111.0
            dist[~overhead[grid.tb]] = np.inf
            li = int(grid.tb[int(np.argmin(dist))])
            pos = int(np.where(idx_oh == li)[0][0])
            risk, gust = r["line_risk"][h][idx_oh], r["line_gust"][h][idx_oh]
            ranks.append({"fire": f["name"], "line": grid.branches[li]["id"], "model_top_pct": pct_rank(risk, pos),
                          "gust_only_top_pct": pct_rank(gust, pos), "line_risk": float(risk[pos]),
                          "above_line_alarm": bool(risk[pos] >= cal["fire"]["line_alarm"])})
        out[f"lead_{lead}d"] = {
            "weather": "recorded (reanalysis)" if lead == 0 else f"forecast issued ~{24 * lead} h ahead",
            "fire_first_alarm_utc": fa[0], "fire_alarm_vs_spike_h": fa[1], "fire_warning_time_h": fa[2],
            "storm_first_alarm_utc": sa[0], "storm_alarm_vs_spike_h": sa[1], "storm_warning_time_h": sa[2],
            "exploratory_fire_warning_to_main_surge_h": fa[3], "exploratory_storm_warning_to_main_surge_h": sa[3],
            "fire_spearman_la": float(spearmanr(r["region_index"][ok], la[ok])[0]),
            "storm_spearman_la": float(spearmanr(sti[ok], la[ok])[0]),
            "peak_forecast_gust_ms": float(np.nanmax(wx["wind_gusts_10m"][E.hours_for(wx, ev["start_utc"], ev["end_utc"])])),
            "ignitions": ranks}
    return {"event": eid, "first_outage_spike_utc": str(spike_t) if spike_t is not None else None,
            "exploratory_main_surge_utc": str(surge_t) if surge_t is not None else None, "outage_baseline_la": base,
            "spike_note": "the 2-day pre-event EAGLE-I window is mostly missing, so the baseline is tiny and a ~1,000-customer blip on Jan 6 "
                          "counts as the 'first sustained spike'; the main surge (>= 50% of peak) is reported as an exploratory metric",
            "by_lead": out}


def _year_part(grid, cal):
    """2024 day-ahead storm index vs top-1% LA County outage hours (V4b threshold)."""
    fc = _load("series_fc1_2024")
    st = cal["storm"]
    T = len(fc["time"])
    H, Rn = np.zeros(T), np.zeros(T)
    for a in range(0, T, 744):
        idx = np.arange(a, min(a + 744, T))
        hz = S.hazards(grid, fc, idx, fc["precipitation"], cal=cal)
        H[idx], Rn[idx] = hz["H"][st["onset"]], hz["Rn"]
    P = S.index_from_hazards(H, Rn, st)
    times = pd.DatetimeIndex(fc["time"].astype("datetime64[ns]")).tz_localize("UTC")
    gmax = pd.Series(fc["wind_gusts_10m"].reshape(T, -1).max(1)).rolling(6, min_periods=1).max().values
    v4b = (RES / "v4b_discrimination.json")
    import json

    q = json.loads(v4b.read_text())["positive_threshold_customers"]
    o = D.outages()["06037"].reindex(times).values
    y = (o > q).astype(float)
    m = np.isfinite(o)
    burn = times >= times[0] + pd.Timedelta(hours=48)  # stock spin-up
    m &= burn
    alarm = P >= st["alarm"]
    pos = (y == 1) & m
    # reanalysis-driven storm index over the same hours (upper bound: perfect weather knowledge)
    from common import series_storm

    ss = series_storm(cal).reindex(times)
    P_re = S.index_from_hazards(ss[f"H{st['onset']}"].values, ss["Rn"].values, st)
    return {"period": f"{times[0].date()}..{times[-1].date()}", "n_hours": int(m.sum()), "n_positive_hours": int(pos.sum()),
            "positive_threshold_customers": q,
            "auc_storm_day_ahead": auc(P[m], y[m]), "auc_gust_day_ahead": auc(gmax[m], y[m]),
            "auc_storm_reanalysis_same_hours": auc(P_re[m], y[m]),
            "hit_rate_storm_alarm": float(alarm[pos].mean()) if pos.any() else None,
            "false_alarm_hour_share": float(alarm[m & (y == 0)].mean())}


def run():
    cal = D.calibration()
    grid = E.LAGrid()
    a = _event_part(grid, cal)
    b = _year_part(grid, cal)
    l1 = a["by_lead"].get("lead_1d", {})
    ign = "; ".join(f"{x['fire']} top {x['model_top_pct']:.0f}%" for x in l1.get("ignitions", []))
    out = {"check": "V8", "event_test": a, "year_2024": b,
           "headline": (f"Day-ahead forecasts: 2024 storm index AUC {b['auc_storm_day_ahead']:.2f} vs forecast gust {b['auc_gust_day_ahead']:.2f}; "
                        f"E3 ignitions from the day-1 forecast: {ign}"),
           "baseline": "Same day-ahead forecast, regional max gust (6 h look-back); reanalysis-driven index shown as the perfect-weather bound",
           "caveat": "Forecast archive starts 2024-01-20, so only E3 and 2024 can be tested; one event and one year."}
    save("v8_forecast", out)
    return out


if __name__ == "__main__":
    r = run()
    import json

    print(r["headline"])
    print(json.dumps(r["year_2024"], indent=1, default=float))
    for k, v in r["event_test"]["by_lead"].items():
        print(k, {kk: vv for kk, vv in v.items() if kk != "ignitions"}, v["ignitions"])
