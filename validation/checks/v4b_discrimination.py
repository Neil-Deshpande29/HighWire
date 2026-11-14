"""V4b. Discrimination over years. Hourly region risk index from the coarse 2019-2024 weather series (thermal sag
approximated as air temp + 10 C, i.e. no power flow per hour). Positive class: hours in the top 1% of LA County
customers out (EAGLE-I) within the report years. ROC AUC on 2022-2024 vs baseline = regional max gust alone.
The only thing fit on 2019-2021 is the look-back window k (rolling max over the previous k hours), chosen
separately and identically for every score.
v2: adds the STORM index (wind agent: wind + rain damage stock, backend/la/storm.py), whose parameters were fit on
2019-2021 only (calibrate.py fit_storm). The fire index answers a different question (ignition) and is kept for reference."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import matplotlib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from common import D, E, RES, save, series_storm  # noqa: E402  (adds repo root to sys.path)
from backend.la import storm as S  # noqa: E402
from backend import thermal as TH  # noqa: E402


def series_index():
    fc0 = D.calibration()["fire"]
    cache = RES / f"series_risk_2019_2024_{fc0.get('model', 'legacy')}.csv"
    if cache.exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)
    cal = D.calibration()
    fc = cal["fire"]
    g = E.LAGrid()
    s = D.weather("series_2019_2024")
    T = len(s["time"])
    ri, gmax = np.zeros(T), np.zeros(T)
    for a in range(0, T, 744):
        idx = np.arange(a, min(a + 744, T))
        W = E.tower_weather(g, s, idx, cal)
        d = np.abs((W["wdir"] - g.taz[None]) % 180.0)
        phi = np.radians(np.minimum(d, 180 - d))
        sag = TH.sag(W["temp"] + 10.0, g.tspan_m[None], g.tsagref[None])
        theta = TH.blowout_angle(W["gust"] * np.sin(phi), g.tcond["D"][None], g.tcond["w_N_m"][None])
        clr = TH.clearance(sag, theta, g.tattach[None], g.trow[None], g.tcanopy[None])
        if fc.get("model") == "two_step":
            st = cal["storm"]
            risk = (E.damage_prob(W["gust"], clr, st["onset"], st.get("c50", 5.0)) * E.sigmoid((fc["h50"] - W["rh"]) / 5)
                    * g.tfuel_ign[None])
        else:
            clr_z = TH.clearance(sag, theta, g.tattach[None], g.trow[None], g.tcanopy_zone[None])
            risk = (E.sigmoid((W["gust"] - fc["g50"]) / 2) * E.sigmoid((fc["h50"] - W["rh"]) / 5)
                    * (0.5 + 0.5 * E.sigmoid((fc["c50"] - clr_z) / 1)) * g.tfuel[None])
        ri[idx] = (risk * g.tspan_km[None]).sum(1)
        gmax[idx] = s["wind_gusts_10m"][idx].reshape(len(idx), -1).max(1)
    times = pd.DatetimeIndex(s["time"].astype("datetime64[ns]")).tz_localize("UTC")
    df = pd.DataFrame({"risk_index": ri, "max_gust_raw": gmax}, index=times)
    df.to_csv(cache)
    return df


def auc(score, label):
    m = np.isfinite(score) & np.isfinite(label)
    s, y = score[m], label[m].astype(bool)
    if y.sum() == 0 or (~y).sum() == 0:
        return np.nan
    r = pd.Series(s).rank().values
    return float((r[y].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (~y).sum()))


def roc(score, label):
    m = np.isfinite(score)
    s, y = score[m], label[m].astype(bool)
    thr = np.unique(np.quantile(s, np.linspace(0, 1, 200)))[::-1]
    return [(float(((s >= t) & ~y).sum() / max((~y).sum(), 1)), float(((s >= t) & y).sum() / max(y.sum(), 1))) for t in thr]


def run():
    cfg = D.config()
    df = series_index()
    o = D.outages()["06037"].reindex(df.index)
    out = {"check": "V4b"}
    st = D.calibration()["storm"]
    ss = series_storm().reindex(df.index)
    storm = pd.Series(S.index_from_hazards(ss[f"H{st['onset']}"].values, ss["Rn"].values, st), index=df.index)
    feats = {"storm_index": storm, "risk_index": df["risk_index"], "baseline_max_gust": df["max_gust_raw"]}
    lab = {}
    for name, years in (("calibrate", cfg["series"]["calibrate_years"]), ("report", cfg["series"]["report_years"])):
        m = df.index.year.isin(years)
        q = o[m].quantile(cfg["outage"]["high_hour_quantile"])
        lab[name] = (m, (o > q).values & m, float(q))
    chosen = {}
    for fname, f in feats.items():
        best = None
        for k in (1, 3, 6, 12, 24):
            v = f.rolling(k, min_periods=1).max().values
            m, y, _ = lab["calibrate"]
            a = auc(v[m], y[m])
            if best is None or a > best[1]:
                best = (k, a)
        chosen[fname] = {"k_hours": best[0], "auc_calibrate": best[1]}
    m, y, q = lab["report"]
    fig, ax = plt.subplots(figsize=(5, 5))
    for fname, f in feats.items():
        v = f.rolling(chosen[fname]["k_hours"], min_periods=1).max().values
        chosen[fname]["auc_report"] = auc(v[m], y[m])
        pts = roc(v[m], y[m])
        ax.plot([p[0] for p in pts], [p[1] for p in pts], label=f"{fname} (AUC {chosen[fname]['auc_report']:.2f})")
    ax.plot([0, 1], [0, 1], "k:", lw=0.8)
    ax.set(xlabel="false positive rate", ylabel="true positive rate", title="2022-2024: top-1% LA County outage hours")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(RES / "v4b_discrimination.png", dpi=110)
    plt.close(fig)
    ri, bl = chosen["risk_index"]["auc_report"], chosen["baseline_max_gust"]["auc_report"]
    sa = chosen["storm_index"]["auc_report"]
    # per-year AUC (2023 may have no positives under the pooled threshold)
    per_year = {}
    for yy in D.config()["series"]["report_years"]:
        my = m & (df.index.year == yy)
        per_year[int(yy)] = {k: auc(f.rolling(chosen[k]["k_hours"], min_periods=1).max().values[my], y[my]) for k, f in feats.items()}
        per_year[int(yy)]["n_positive"] = int(y[my].sum())
    verdict = "beats" if sa > bl + 0.02 else ("matches" if sa >= bl - 0.02 else "does NOT beat")
    out.update({"features": chosen, "positive_threshold_customers": q, "n_positive_hours": int(y[m].sum()), "n_hours": int(m.sum()),
                "beats_baseline": bool(sa > bl), "storm_verdict": verdict, "per_year": per_year,
                "headline": f"2022-2024 high-outage hours: storm index AUC {sa:.2f} (fire index {ri:.2f}) vs gust-only baseline {bl:.2f}: {verdict} the baseline",
                "baseline": "Regional maximum gridded gust, same look-back window selection",
                "caveat": "Positives include every cause of high outages (heat, rain, equipment); non-weather causes are not separable in EAGLE-I.",
                "plot": "v4b_discrimination.png"})
    save("v4b_discrimination", out)
    return out


if __name__ == "__main__":
    r = run()
    print(r["headline"], r["features"], r["n_positive_hours"], r["positive_threshold_customers"])
