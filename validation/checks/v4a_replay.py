"""V4a. Event replay: hourly region risk index (risk-weighted line-km) vs EAGLE-I customers out.
For each wind event (test: E1, E3; train E2 shown for reference) and the test control week C3:
Spearman correlation, lead time = first hour RI >= calibrated alarm  ->  first hour LA County outages > 2x baseline.
v2: the same metrics for the STORM index (wind agent, backend/la/storm.py; alarm fit on 2019-2021 only)."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import matplotlib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from common import D, E, RES, event_hindcast, first_spike, outage_baseline, save  # noqa: E402
from backend.la import storm as S  # noqa: E402

COUNTIES = {"06037": "Los Angeles", "06111": "Ventura", "06059": "Orange", "06071": "San Bernardino"}


def replay_series(eid, grid=None):
    cal = D.calibration()
    times, r = event_hindcast(eid, cal=cal, grid=grid)
    o = D.outages().reindex(times)
    return times, r, o


def run():
    cal = D.calibration()
    alarm = cal["fire"]["region_alarm"]
    mult = D.config()["outage"]["spike_multiple"]
    grid = E.LAGrid()
    out = {"check": "V4a", "alarm": alarm, "events": {}}
    evs = [e for e, v in D.events().items() if v["kind"] in ("wind", "control")]
    fig, axes = plt.subplots(len(evs), 1, figsize=(11, 2.6 * len(evs)))
    for ax, eid in zip(axes, evs):
        ev = D.events()[eid]
        times, r, o = replay_series(eid, grid)
        ri = r["region_index"]
        la = o["06037"].values
        base = outage_baseline(eid)
        both = np.isfinite(la)
        rho, p = spearmanr(ri[both], la[both]) if both.sum() > 5 else (np.nan, np.nan)
        allc = o[list(COUNTIES)].sum(1, min_count=1).values
        rho_all, _ = spearmanr(ri[np.isfinite(allc)], allc[np.isfinite(allc)])
        a_idx = np.where(ri >= alarm)[0]
        s_idx = np.where(la > mult * base)[0]
        first_alarm = times[a_idx[0]] if len(a_idx) else None
        # primary: first of >= 3 consecutive hours above 2x baseline (changed after first run, see DECISIONS.md)
        fs = first_spike(np.nan_to_num(la), mult * base, sustain=3)
        spike_t = times[fs] if fs is not None else None
        spike_t1 = times[s_idx[0]] if len(s_idx) else None  # original single-hour definition, reported too
        lead = (spike_t - first_alarm).total_seconds() / 3600 if first_alarm is not None and spike_t is not None else None
        lead1 = (spike_t1 - first_alarm).total_seconds() / 3600 if first_alarm is not None and spike_t1 is not None else None
        first_spike_t = spike_t
        # exploratory (post-hoc, labeled as such): lead to the main surge = first hour >= 50% of the event's peak
        surge_i = int(np.argmax(np.nan_to_num(la) >= 0.5 * np.nanmax(la))) if np.isfinite(la).any() else None
        surge_t = times[surge_i] if surge_i is not None else None
        lead_surge = (surge_t - first_alarm).total_seconds() / 3600 if first_alarm is not None and surge_t is not None else None
        res = {"split": ev["split"], "kind": ev["kind"], "hours": len(times), "spearman_la": float(rho), "spearman_p": float(p),
               "spearman_4county": float(rho_all), "outage_baseline_la": base, "outage_peak_la": float(np.nanmax(la)),
               "outage_peak_time": str(times[int(np.nanargmax(la))]), "ri_peak": float(ri.max()), "ri_peak_time": str(times[int(ri.argmax())]),
               "first_alarm_utc": str(first_alarm) if first_alarm is not None else None,
               "first_spike_utc": str(first_spike_t) if first_spike_t is not None else None, "lead_time_h": lead,
               "lead_time_h_single_hour_definition": lead1,
               "exploratory_lead_to_main_surge_h": lead_surge, "main_surge_utc": str(surge_t) if surge_t is not None else None, "first_spike_single_hour_utc": str(spike_t1) if spike_t1 is not None else None,
               "alarm_hours": int(len(a_idx)), "alarm_hour_fraction": float(len(a_idx) / len(times)),
               "spike_hours": int(len(s_idx)), "hits_on_spike_hours": float((ri[s_idx] >= alarm).mean()) if len(s_idx) else None}
        # storm index (wind agent)
        sti = S.event_storm(grid, eid, cal)["storm_index"]
        st_alarm = cal["storm"]["alarm"]
        srho, sp = spearmanr(sti[both], la[both]) if both.sum() > 5 else (np.nan, np.nan)
        sa_idx = np.where(sti >= st_alarm)[0]
        s_first = times[sa_idx[0]] if len(sa_idx) else None
        s_lead = (spike_t - s_first).total_seconds() / 3600 if s_first is not None and spike_t is not None else None
        res["storm"] = {"spearman_la": float(srho), "spearman_p": float(sp), "alarm": st_alarm,
                        "first_alarm_utc": str(s_first) if s_first is not None else None, "lead_time_h": s_lead,
                        "alarm_hour_fraction": float(len(sa_idx) / len(times)),
                        "hits_on_spike_hours": float((sti[s_idx] >= st_alarm).mean()) if len(s_idx) else None,
                        "peak": float(sti.max())}
        out["events"][eid] = res
        loc = times.tz_convert("America/Los_Angeles").tz_localize(None)
        ax.plot(loc, ri, color="tab:red", lw=1.5, label="Fire risk index (risk-weighted line-km)")
        axs = ax.twinx()
        axs.spines["right"].set_position(("axes", 1.07))
        axs.plot(loc, sti, color="tab:blue", lw=1.3, label="Storm index (wind agent)")
        axs.axhline(st_alarm, color="tab:blue", ls=":", lw=0.8)
        axs.set_ylim(0, 1)
        axs.set_ylabel("storm index", color="tab:blue", fontsize=8)
        ax.axhline(alarm, color="tab:red", ls=":", lw=1)
        ax.set_ylabel("risk index", color="tab:red", fontsize=8)
        ax2 = ax.twinx()
        ax2.plot(loc, la, color="k", lw=1.2, label="LA County customers out (EAGLE-I)")
        ax2.axhline(mult * base, color="k", ls=":", lw=0.8)
        ax2.set_ylabel("customers out", fontsize=8)
        for f in D.fires().values():
            if f["event"] == eid:
                ax.axvline(pd.Timestamp(f["ignition_utc"]).tz_convert("America/Los_Angeles").tz_localize(None), color="orange", lw=1)
                ax.text(pd.Timestamp(f["ignition_utc"]).tz_convert("America/Los_Angeles").tz_localize(None), ax.get_ylim()[1] * 0.9, f["name"], fontsize=7, color="darkorange")
        if lead is not None:
            ax.annotate("", xy=(first_spike_t.tz_convert("America/Los_Angeles").tz_localize(None), alarm),
                        xytext=(first_alarm.tz_convert("America/Los_Angeles").tz_localize(None), alarm),
                        arrowprops=dict(arrowstyle="<->", color="tab:blue"))
        title = f"{eid} ({ev['split']}): {ev['name']}  |  storm rho {srho:.2f}" + (f", lead {s_lead:+.0f} h" if s_lead is not None else "") + f"  |  fire rho {rho:.2f}"
        title += f"  |  lead {lead:+.0f} h" if lead is not None else ("  |  alarm never fired" if not len(a_idx) else "  |  no outage spike")
        ax.set_title(title, fontsize=9)
        if eid == evs[0]:
            ax.legend(loc="upper left", fontsize=7)
            axs.legend(loc="upper center", fontsize=7)
            ax2.legend(loc="upper right", fontsize=7)
    fig.tight_layout()
    fig.savefig(RES / "v4a_replay.png", dpi=100)
    plt.close(fig)
    # per-event model-vs-reality series for the UI chart
    for eid in evs:
        times, r, o = replay_series(eid, grid)
        pd.DataFrame({"t": times.astype(str), "risk_index": r["region_index"], "storm_index": S.event_storm(grid, eid)["storm_index"], "la_out": o["06037"].values,
                      "four_county_out": o[list(COUNTIES)].sum(1, min_count=1).values}).to_csv(RES / f"series_{eid}.csv", index=False)
    out["storm_test"] = {k: v["storm"] for k, v in out["events"].items() if v["split"] == "test"}
    e1 = out["events"].get("E1", {})
    out["headline"] = (f"E1 (Woolsey, test): risk index first crossed its alarm {abs(e1['lead_time_h']):.0f} h "
                       f"{'before' if e1['lead_time_h'] >= 0 else 'after'} LA County outages crossed 2x baseline; Spearman {e1['spearman_la']:.2f}"
                       if e1.get("lead_time_h") is not None else "E1: no lead time (alarm or spike missing)")
    out["baseline"] = "Lead time / correlation are compared to the gust-only baseline in V4b; here, raw co-movement with outages"
    out["plot"] = "v4a_replay.png"
    save("v4a_replay", out)
    return out


if __name__ == "__main__":
    r = run()
    print(r["headline"])
    for e, v in r["events"].items():
        print(e, "STORM rho", round(v["storm"]["spearman_la"], 2), "lead", v["storm"]["lead_time_h"], "alarm_frac", round(v["storm"]["alarm_hour_fraction"], 3))
        print(e, v["split"], "rho", round(v["spearman_la"], 2), "lead", v["lead_time_h"], "alarm_frac", round(v["alarm_hour_fraction"], 3), "hits", v["hits_on_spike_hours"], "peak", v["outage_peak_la"], v["first_alarm_utc"], v["first_spike_utc"])
