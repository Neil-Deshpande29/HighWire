"""V2. Gridded weather (Open-Meteo) vs ASOS ground stations, every event. Bias/RMSE per station for temperature,
sustained wind and gust, before and after the (calibrated) gust correction. Plots gusts over time for KBUR and the
station nearest each fire origin."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from common import D, RES, apply_gust, nudge_loso, pair_station_grid, save  # noqa: E402


def err(p, o):
    m = np.isfinite(p) & np.isfinite(o)
    return {"n": int(m.sum()), "bias": float(np.mean(p[m] - o[m])) if m.any() else None,
            "rmse": float(np.sqrt(np.mean((p[m] - o[m]) ** 2))) if m.any() else None}


def run():
    cal = D.calibration()
    out, frames = {}, []
    for eid, ev in D.events().items():
        g = pair_station_grid(eid)
        t = pair_station_grid(eid, var_obs="tmpc", var_grid="temperature_2m")[["hour", "station", "grid"]].rename(columns={"grid": "grid_t"})
        w = pair_station_grid(eid, var_obs="wind_ms", var_grid="wind_speed_10m")[["hour", "station", "grid"]].rename(columns={"grid": "grid_w"})
        m = g.merge(t, on=["hour", "station"]).merge(w, on=["hour", "station"])
        m["grid_corr"] = apply_gust(cal, m.zone.values, m.grid.values)
        frames.append(m)
        per = {}
        for st, s in m.groupby("station"):
            windy = s.gust_ms >= 12
            per[st] = {"zone": s.zone.iloc[0], "temp": err(s.grid_t.values, s.tmpc.values), "wind": err(s.grid_w.values, s.wind_ms.values),
                       "gust_before": err(s.grid.values, s.gust_ms.values), "gust_after": err(s.grid_corr.values, s.gust_ms.values),
                       "gust_windy_before": err(s.grid.values[windy], s.gust_ms.values[windy]),
                       "gust_windy_after": err(s.grid_corr.values[windy], s.gust_ms.values[windy])}
        out[eid] = {"split": ev["split"], "stations": per,
                    "all": {"gust_before": err(m.grid.values, m.gust_ms.values), "gust_after": err(m.grid_corr.values, m.gust_ms.values),
                            "temp": err(m.grid_t.values, m.tmpc.values), "wind": err(m.grid_w.values, m.wind_ms.values),
                            "gust_windy_before": err(m.grid.values[m.gust_ms >= 12], m.gust_ms.values[m.gust_ms >= 12]),
                            "gust_peak_obs": float(m.gust_ms.max()), "gust_peak_grid": float(m.grid.max())}}
    allm = pd.concat(frames)
    # plots: KBUR + nearest station to each fire origin
    fires = D.fires()
    meta = allm.groupby("station")[["lat", "lon"]].first()
    panels = []
    for fid, f in fires.items():
        d = np.hypot(meta.lat - f["lat"], (meta.lon - f["lon"]) * np.cos(np.radians(34)))
        panels.append((f["event"], d.idxmin(), f"{f['name']} -> nearest station {d.idxmin()}"))
    panels = [(e, "KBUR", f"{e}: KBUR") for e in ("E1", "E2", "E3")] + panels
    fig, axes = plt.subplots(len(panels), 1, figsize=(10, 2.2 * len(panels)))
    for ax, (eid, st, title) in zip(axes, panels):
        s = allm[(allm.event == eid) & (allm.station == st)].sort_values("hour")
        ax.plot(s.hour, s.gust_ms, "k.", ms=3, label="ASOS gust (obs)")
        ax.plot(s.hour, s.grid, "-", lw=1, label="Open-Meteo gust")
        ax.plot(s.hour, s.grid_corr, "--", lw=1, label="after correction")
        ax.set_title(title, fontsize=9)
        ax.set_ylabel("m/s")
    axes[0].legend(fontsize=7, ncol=3)
    fig.tight_layout()
    fig.savefig(RES / "v2_weather.png", dpi=100)
    plt.close(fig)
    test = [e for e, v in out.items() if v["split"] == "test"]
    tw = pd.concat([f for f in frames if f.event.iloc[0] in test])
    windy = tw.gust_ms >= 12
    res = {"check": "V2", "events": out,
           "test_summary": {"gust_all_before": err(tw.grid.values, tw.gust_ms.values), "gust_all_after": err(tw.grid_corr.values, tw.gust_ms.values),
                            "gust_windy_before": err(tw.grid.values[windy], tw.gust_ms.values[windy]),
                            "gust_windy_after": err(tw.grid_corr.values[windy], tw.gust_ms.values[windy]),
                            "temp": err(tw.grid_t.values, tw.tmpc.values), "wind": err(tw.grid_w.values, tw.wind_ms.values)},
           "gust_correction_applied": cal["gust"], "plot": "v2_weather.png"}
    ts = res["test_summary"]
    # wind agent: live station nudging, leave-one-station-out (params fit on training events in calibrate.py)
    nd = cal.get("nudge")
    if nd:
        no = pd.concat([nudge_loso(f.dropna(subset=["gust_ms", "grid"]), nd["L_km"], nd["w0"]).assign(event=f.event.iloc[0])
                        for f in frames if f.event.iloc[0] in test])
        w12 = no.obs >= 12
        pk = no.groupby(["event", "station"]).agg(o=("obs", "max"), g=("grid", "max"), n=("nudged", "max"))
        res["nudged_test"] = {"params": nd, "gust_all": err(no.nudged.values, no.obs.values), "gust_all_raw": err(no.grid.values, no.obs.values),
                              "gust_windy": err(no.nudged.values[w12], no.obs.values[w12]), "gust_windy_raw": err(no.grid.values[w12], no.obs.values[w12]),
                              "event_peak": err(pk.n.values, pk.o.values), "event_peak_raw": err(pk.g.values, pk.o.values),
                              "method": "leave-one-station-out: each station is predicted from the other stations' live residuals"}
    res["headline"] = (f"Test events: gridded gust bias {ts['gust_all_before']['bias']:+.1f} m/s (RMSE {ts['gust_all_before']['rmse']:.1f}); "
                       f"on windy hours (obs >= 12 m/s) bias {ts['gust_windy_before']['bias']:+.1f} m/s")
    res["baseline"] = "Raw Open-Meteo reanalysis (before correction)"
    save("v2_weather", res)
    return res


if __name__ == "__main__":
    r = run()
    print(r["headline"], r["test_summary"])
