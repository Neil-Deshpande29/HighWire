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

from common import (D, E, RES, apply_gust, auc, event_hindcast, local_hour_features, nudge_loso, outage_baseline,
                    pair_station_grid, pop_weighted_temp, save, series_storm)
from backend.la import storm as S

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
def load_features(idx_utc, T, T24, Tin):
    """Hour-of-day x weekday/weekend profile + temperature terms.
    v2 (chosen on an internal 2019-20 fit / summer-2021 holdout, never on H2): air-conditioning load depends on heat
    MORE in the afternoon than at night, so temperature hinges are interacted with time-of-day blocks, and an inland
    heat term (90th-percentile grid temperature) is added because SCE load sits inland where the population-weighted
    mean understates the heat. Internal holdout: SCE top-5 peak error -13.1% -> -4.8%, LDWP -9.1% -> -5.9%."""
    hod, wkend, _ = local_hour_features(idx_utc)
    cols = [np.ones(len(T)), np.maximum(T - 18, 0), np.maximum(T - 26, 0), np.maximum(14 - T, 0),
            np.maximum(T24 - 20, 0), np.maximum(T24 - 26, 0)]
    for h in range(1, 24):
        cols.append((hod == h).astype(float))
    for h in range(24):
        cols.append(((hod == h) & (wkend == 1)).astype(float))
    for a, b in [(0, 6), (6, 11), (11, 15), (15, 22), (22, 24)]:
        k = ((hod >= a) & (hod < b)).astype(float)
        cols += [k * np.maximum(T - 22, 0), k * np.maximum(T24 - 22, 0)]
    aft = ((hod >= 12) & (hod < 22)).astype(float)
    cols += [aft * np.maximum(Tin - 28, 0), aft * np.maximum(Tin - 34, 0), np.maximum(Tin - 28, 0)]
    return np.column_stack(cols)


def series_temps():
    s = D.weather("series_2019_2024")
    T = pop_weighted_temp(s)
    idx = pd.DatetimeIndex(s["time"].astype("datetime64[ns]")).tz_localize("UTC")
    T = pd.Series(T, idx)
    Tin = pd.Series(np.percentile(s["temperature_2m"].reshape(len(idx), -1), 90, axis=1), idx)
    return T, T.rolling(24, min_periods=1).mean(), Tin


def fit_load():
    T, T24, Tin = series_temps()
    dem = D.demand()
    out = {}
    for ba in ["LDWP", "SCE"]:
        y = dem[ba].reindex(T.index)
        fit = T.index.year.isin(CFG["load_model"]["fit_years"]) & y.notna().values
        X = load_features(T.index[fit], T.values[fit], T24.values[fit], Tin.values[fit])
        coef, *_ = np.linalg.lstsq(X, y.values[fit], rcond=None)
        pred = X @ coef
        out[ba] = {"coef": coef.tolist(), "train_mape": float(np.mean(np.abs(pred - y.values[fit]) / y.values[fit]) * 100)}
    return out


def predict_load(model, idx_utc):
    T, T24, Tin = series_temps()
    X = load_features(idx_utc, T.reindex(idx_utc).values, T24.reindex(idx_utc).values, Tin.reindex(idx_utc).values)
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


# ------------------------------------------------------------------ 4. storm-damage model (wind agent)
def fit_storm(cal):
    """Selection protocol, all on 2019-2021 (calibration years) with leave-one-year-out AUC against the top-1% LA
    County outage hours: (1) damage onset x restoration tau on the wind damage stock alone; (2) add the rain stock
    only if it raises the leave-one-year-out AUC; (3) refit logistic weights on all three years; alarm = 98th
    percentile of the index over calibration-year hours that were NOT high-outage hours (<= 2% false alarms)."""
    df = series_storm(cal)
    yrs = df.index.year.values
    cy = CFG["series"]["calibrate_years"]
    cm = np.isin(yrs, cy)
    o = D.outages()["06037"].reindex(df.index)
    y = ((o > o[cm].quantile(CFG["outage"]["high_hour_quantile"])).values & cm).astype(float)

    def loyo(make_score):
        return [auc(make_score(h)[yrs == h], y[yrs == h]) for h in cy]

    table = []
    for onset in S.ONSET_GRID:
        for tau in S.TAU_GRID:
            sw = S.stock(df[f"H{onset}"].values, tau)
            sc = loyo(lambda h, sw=sw: sw)
            table.append({"onset": onset, "tau": tau, "loyo_auc": [round(a, 4) for a in sc], "mean": float(np.mean(sc))})
    best = max(table, key=lambda r: r["mean"])
    sw = S.stock(df[f"H{best['onset']}"].values, best["tau"])
    rain_rows, chosen_rain = [], None
    for rt in S.RAIN_TAU_GRID:
        X = np.c_[sw, S.stock(df["Rn"].values, rt)]

        def mk(h, X=X):
            tr = cm & (yrs != h)
            return np.c_[np.ones(len(X)), X] @ S.logfit(X[tr], y[tr])
        sc = loyo(mk)
        rain_rows.append({"rain_tau": rt, "loyo_auc": [round(a, 4) for a in sc], "mean": float(np.mean(sc))})
    br = max(rain_rows, key=lambda r: r["mean"])
    if br["mean"] > best["mean"]:
        chosen_rain = br["rain_tau"]
    sr = S.stock(df["Rn"].values, chosen_rain or 6) * (1.0 if chosen_rain else 0.0)
    w = S.logfit(np.c_[sw, sr][cm], y[cm])
    if not chosen_rain:
        w[2] = 0.0
    P = S.score(sw, sr, w)
    alarm = float(np.quantile(P[cm & (y == 0)], 0.98))
    base = [auc(pd.Series(df.max_gust_raw.values).rolling(6, min_periods=1).max().values[yrs == h], y[yrs == h]) for h in cy]
    st = {"onset": best["onset"], "tau": best["tau"], "rain_tau": chosen_rain or 6, "w": [float(x) for x in w], "alarm": alarm}
    summary = {"chosen": st, "n_candidates": len(table) + len(rain_rows), "wind_table": sorted(table, key=lambda r: -r["mean"])[:8],
               "rain_table": rain_rows, "rain_accepted": chosen_rain is not None,
               "loyo_auc_chosen": br["mean"] if chosen_rain else best["mean"], "loyo_auc_gust_baseline_k6": float(np.mean(base)),
               "positives_calibration_years": int(y[cm].sum())}
    return st, summary


# ------------------------------------------------------------------ 5. station nudging (wind agent, live obs)
def fit_nudge():
    """Pick the residual-spreading length L and shrinkage w0 on TRAINING events, leave-one-station-out."""
    pairs = [pair_station_grid(e).dropna(subset=["gust_ms", "grid"]) for e in TRAIN]
    rows = []
    for L in (10, 20, 40, 80):
        for w0 in (0.05, 0.2, 0.5, 1.0):
            o = pd.concat([nudge_loso(m, L, w0) for m in pairs])
            windy = o.obs >= 8
            r_all = float(np.sqrt(np.mean((o.nudged - o.obs) ** 2)))
            r_w = float(np.sqrt(np.mean((o.nudged[windy] - o.obs[windy]) ** 2)))
            rows.append({"L_km": L, "w0": w0, "rmse_all": r_all, "rmse_windy8": r_w, "objective": r_all + r_w})
    best = min(rows, key=lambda r: r["objective"])
    return {"L_km": best["L_km"], "w0": best["w0"]}, {"grid": rows, "chosen": best}


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
    nudge, nudge_sum = fit_nudge()
    print("nudge:", nudge)
    storm, storm_sum = fit_storm({"gust": gust_cal, "fire": fire})
    print("storm:", storm, "LOYO", round(storm_sum["loyo_auc_chosen"], 3), "vs gust", round(storm_sum["loyo_auc_gust_baseline_k6"], 3))
    cal = {"gust": gust_cal, "fire": fire, "load": load_model, "storm": storm, "nudge": nudge, "trained_on": TRAIN,
           "load_fit_years": CFG["load_model"]["fit_years"], "storm_fit_years": CFG["series"]["calibrate_years"]}
    (D.P / "calibration.json").write_text(json.dumps(cal, indent=1))
    save("calibration_summary", {"gust": gust_sum, "fire": fire_sum, "storm": storm_sum, "nudge": nudge_sum,
                                  "load": {k: {"train_mape": v["train_mape"]} for k, v in load_model.items()}})


if __name__ == "__main__":
    main()
