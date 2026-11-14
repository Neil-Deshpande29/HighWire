"""Calibration: fit ONLY the genuinely unknown parameters, ONLY on training data (validation/config.yaml).
  1. gust correction  (per zone linear map gridded -> station gust; mountain zone has no station, uses valley)
  2. load model       (per BA: piecewise-linear temperature terms + hour-of-day x weekday/weekend, 2019-2021)
  3. fire thresholds  (g50, h50, c50 + region alarm level; maximize hits in training wind-event outage spikes
                       with a cap on control-week false alarms)
Writes data/processed/calibration.json and validation/results/calibration_summary.json."""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from common import D, E, RES, apply_gust, event_hindcast, local_hour_features, outage_baseline, pair_station_grid, pop_weighted_temp, save

CFG = D.config()
TRAIN = CFG["split"]["train"]
ZONES = ["coast", "basin", "valley", "mountain"]


# ------------------------------------------------------------------ 1. gust correction
def fit_gust():
    m = pd.concat([pair_station_grid(e) for e in TRAIN]).dropna(subset=["gust_ms", "grid"])
    # Fit on windy hours only (station or grid gust >= 8 m/s): an all-hours fit is dominated by calm hours and
    # flattens the slope, which under-predicts exactly the Santa Ana gusts we care about.
    mw = m[(m.gust_ms >= 8) | (m.grid >= 8)]
    a, b, stats = {}, {}, {}
    for z in ZONES:
        sub = mw[mw.zone == z]
        src = z
        if len(sub) < 30:  # no station in this zone (mountain): borrow the valley fit
            sub, src = mw[mw.zone == "valley"], "valley"
        X = np.c_[np.ones(len(sub)), sub.grid.values]
        coef, *_ = np.linalg.lstsq(X, sub.gust_ms.values, rcond=None)
        # acceptance rule: keep the correction only if it lowers training RMSE on windy station hours (>= 12 m/s)
        zw = m[(m.zone == (z if src == z else src)) & (m.gust_ms >= 12)]
        rm = lambda p: float(np.sqrt(np.mean((p - zw.gust_ms.values) ** 2))) if len(zw) else np.inf  # noqa: E731
        fitted = coef[0] + coef[1] * zw.grid.values
        accept = len(zw) > 0 and rm(fitted) < rm(zw.grid.values)
        a[z], b[z] = (float(coef[0]), float(coef[1])) if accept else (0.0, 1.0)
        stats[z] = {"n": int(len(sub)), "fit_from": src, "candidate": [float(coef[0]), float(coef[1])], "accepted": bool(accept),
                    "windy_rmse_identity": rm(zw.grid.values), "windy_rmse_candidate": rm(fitted)}
    cal = {"a": a, "b": b}
    corr = apply_gust({"gust": cal}, m.zone.values, m.grid.values)
    windy = m.gust_ms >= 12
    def err(pred, obs):
        return {"bias": float(np.mean(pred - obs)), "rmse": float(np.sqrt(np.mean((pred - obs) ** 2)))}
    summary = {"n_pairs": int(len(m)), "zones": stats, "before_all": err(m.grid.values, m.gust_ms.values),
               "after_all": err(corr, m.gust_ms.values), "before_windy": err(m.grid.values[windy], m.gust_ms.values[windy]),
               "after_windy": err(corr[windy], m.gust_ms.values[windy]), "n_windy": int(windy.sum())}
    return cal, summary


# ------------------------------------------------------------------ 2. load model
def load_features(idx_utc, T, T24):
    hod, wkend, _ = local_hour_features(idx_utc)
    cols = [np.ones(len(T)), np.maximum(T - 18, 0), np.maximum(T - 26, 0), np.maximum(14 - T, 0),
            np.maximum(T24 - 20, 0), np.maximum(T24 - 26, 0)]
    for h in range(1, 24):
        cols.append((hod == h).astype(float))
    for h in range(24):
        cols.append(((hod == h) & (wkend == 1)).astype(float))
    return np.column_stack(cols)


def series_temps():
    s = D.weather("series_2019_2024")
    T = pop_weighted_temp(s)
    idx = pd.DatetimeIndex(s["time"].astype("datetime64[ns]")).tz_localize("UTC")
    T = pd.Series(T, idx)
    return T, T.rolling(24, min_periods=1).mean()


def fit_load():
    T, T24 = series_temps()
    dem = D.demand()
    out = {}
    for ba in ["LDWP", "SCE"]:
        y = dem[ba].reindex(T.index)
        fit = T.index.year.isin(CFG["load_model"]["fit_years"]) & y.notna().values
        X = load_features(T.index[fit], T.values[fit], T24.values[fit])
        coef, *_ = np.linalg.lstsq(X, y.values[fit], rcond=None)
        pred = X @ coef
        out[ba] = {"coef": coef.tolist(), "train_mape": float(np.mean(np.abs(pred - y.values[fit]) / y.values[fit]) * 100)}
    return out


def predict_load(model, idx_utc):
    T, T24 = series_temps()
    X = load_features(idx_utc, T.reindex(idx_utc).values, T24.reindex(idx_utc).values)
    return {ba: X @ np.array(m["coef"]) for ba, m in model.items()}


# ------------------------------------------------------------------ 3. fire thresholds
def risk_from(r, grid, g50, h50, c50):
    return (E.sigmoid((r["tower_gust"] - g50) / 2.0) * E.sigmoid((h50 - r["tower_rh"]) / 5.0)
            * (0.5 + 0.5 * E.sigmoid((c50 - r["_clear"]) / 1.0)) * grid.tfuel[None] * r["energized"][grid.tb][None])


def fit_fire(gust_cal):
    grid = E.LAGrid()
    cal = {"gust": gust_cal, "fire": D.DEFAULT_CAL["fire"]}
    runs = {}
    for eid in TRAIN:
        if D.events()[eid]["kind"] == "heat":
            continue
        times, r = event_hindcast(eid, cal=cal, grid=grid)
        # recover tower clearance (independent of fire params) from the hindcast internals
        r["_clear"] = _tower_clearance(grid, eid, cal)
        runs[eid] = (times, r)
    wind_ev = [e for e in runs if D.events()[e]["kind"] == "wind"]
    ctrl = [e for e in runs if D.events()[e]["kind"] == "control"]
    o = D.outages()["06037"]
    spikes = {}
    for e in wind_ev:
        base = outage_baseline(e)
        times = runs[e][0]
        oo = o.reindex(times)
        spikes[e] = (oo > CFG["outage"]["spike_multiple"] * base).values
    best = None
    grid_search = []
    for g50, h50, c50 in itertools.product([6, 8, 10, 12, 14, 16, 18, 20], [10, 15, 20, 25, 30, 35, 40], [5, 7, 9, 11, 13]):
        ri = {e: (risk_from(runs[e][1], grid, g50, h50, c50) * grid.tspan_km[None]).sum(1) for e in runs}
        ctrl_ri = np.concatenate([ri[e] for e in ctrl])
        alarm = float(np.quantile(ctrl_ri, 0.98))  # <= 2% false-alarm hours in training control weeks by construction
        alarm = max(alarm, 1.0)
        hits = np.concatenate([ri[e][spikes[e]] >= alarm for e in wind_ev])
        hit_rate = float(hits.mean()) if hits.size else 0.0
        peak = max(float(ri[e].max()) for e in wind_ev)
        margin = peak / alarm
        grid_search.append({"g50": g50, "h50": h50, "c50": c50, "alarm": alarm, "hit_rate": hit_rate, "margin": margin})
        pos = np.concatenate([ri[e][spikes[e]] for e in wind_ev])
        auc = float((pos[:, None] > ctrl_ri[None, :]).mean() + 0.5 * (pos[:, None] == ctrl_ri[None, :]).mean()) if pos.size else 0.5
        grid_search[-1]["auc_spike_vs_control"] = auc
        key = (round(hit_rate, 6), round(auc, 6), margin)
        if best is None or key > best[0]:
            best = (key, {"g50": g50, "h50": h50, "c50": c50, "region_alarm": alarm})
    params = best[1]
    # line-level alarm: 99.5th pct of line-hour risk in training control weeks (min 0.3)
    lr = []
    for e in ctrl:
        rk = risk_from(runs[e][1], grid, params["g50"], params["h50"], params["c50"])
        line = np.zeros((rk.shape[0], grid.nl))
        np.maximum.at(line, (slice(None), grid.tb), rk)
        lr.append(line.ravel())
    params["line_alarm"] = float(max(0.3, np.quantile(np.concatenate(lr), 0.995)))
    summary = {"chosen": params, "objective": "max hit rate on training wind-event outage-spike hours (alarm = 98th pct of control-week RI); tie-break: AUC spike-hours vs control-hours, then peak/alarm margin",
               "spike_hours": {e: int(spikes[e].sum()) for e in wind_ev}, "best_hit_rate": best[0][0], "best_auc": best[0][1], "best_margin": best[0][2],
               "n_configs": len(grid_search),
               "top10": sorted(grid_search, key=lambda d: (-d["hit_rate"], -d["auc_spike_vs_control"], -d["margin"]))[:10]}
    return params, summary


def _tower_clearance(grid, eid, cal):
    """Re-run the physics chain up to clearance (fire-parameter independent)."""
    from backend import thermal as TH

    ev = D.events()[eid]
    wx = D.weather(eid)
    hrs = E.hours_for(wx, ev["start_utc"], ev["end_utc"])
    W = E.tower_weather(grid, wx, hrs, cal)
    ld, sc = E.event_demand(wx["time"][hrs])
    top = grid.base_topology()
    P, _ = grid.injections(ld, sc, top)
    F, _ = grid.flows(P, top)
    amps = np.abs(F) * 1e6 / (np.sqrt(3) * grid.kv[None] * 1e3)
    I_t = (amps / (grid.circuits * grid.thermal_scale)[None])[:, grid.tb]
    d = np.abs((W["wdir"] - grid.taz[None]) % 180.0)
    phi = np.radians(np.minimum(d, 180 - d))
    Tc = TH.conductor_temperature(grid.tcond, I_t, W["temp"], np.maximum(W["wind"], 0.5), phi, W["ghi"], grid.telev[None], iters=30)
    sag = TH.sag(Tc, grid.tspan_m[None], grid.tsagref[None])
    theta = TH.blowout_angle(W["gust"] * np.sin(phi), grid.tcond["D"][None], grid.tcond["w_N_m"][None])
    return TH.clearance(sag, theta, grid.tattach[None], grid.trow[None], grid.tcanopy[None])


def main():
    gust_cal, gust_sum = fit_gust()
    print("gust:", json.dumps(gust_sum["before_windy"]), "->", json.dumps(gust_sum["after_windy"]))
    load_model = fit_load()
    print("load train MAPE:", {k: round(v["train_mape"], 2) for k, v in load_model.items()})
    fire, fire_sum = fit_fire(gust_cal)
    print("gust zones:", {z: (v["accepted"], round(v["windy_rmse_identity"], 2), round(v["windy_rmse_candidate"], 2)) for z, v in gust_sum["zones"].items()})
    print("fire:", fire, "hit rate", fire_sum["best_hit_rate"], "auc", round(fire_sum["best_auc"], 3), "margin", round(fire_sum["best_margin"], 2))
    for row in fire_sum["top10"][:6]:
        print("   ", row)
    cal = {"gust": gust_cal, "fire": fire, "load": load_model, "trained_on": TRAIN,
           "load_fit_years": CFG["load_model"]["fit_years"]}
    (D.P / "calibration.json").write_text(json.dumps(cal, indent=1))
    save("calibration_summary", {"gust": gust_sum, "fire": fire_sum,
                                  "load": {k: {"train_mape": v["train_mape"]} for k, v in load_model.items()}})


if __name__ == "__main__":
    main()
