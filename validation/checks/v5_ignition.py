"""V5. Ignition hindcast. For each utility-related ignition (CPUC/SCE reports, origin from WFIGS), take the model's
line risk ranking at the ignition hour and report the percentile rank of the line the incident report names (when
it is in the model; `facility_line` in fires.yaml), else the overhead line nearest the origin.
Baselines: random (expected 50th percentile) and ranking by gust alone. Tiny sample: every case is reported."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common import D, E, event_hindcast, save  # noqa: E402


def pct_rank(values, i):
    """Top-X%: share of lines ranked at or above line i (ties averaged). 0% = riskiest line."""
    r = pd.Series(values).rank(ascending=False, method="average").values
    return float(r[i] / len(values) * 100)


def run():
    g = E.LAGrid()
    cosl = np.cos(np.radians(34))
    overhead = ~g.cable
    cases = []
    cache = {}
    for fid, f in D.fires().items():
        eid = f["event"]
        if eid not in cache:
            cache[eid] = event_hindcast(eid, grid=g)
        times, r = cache[eid]
        t = pd.Timestamp(f["ignition_utc"]).floor("h")
        if t not in times:
            continue
        h = int(np.where(times == t)[0][0])
        dist = np.hypot(g.tlat - f["lat"], (g.tlon - f["lon"]) * cosl) * 111.0
        dist[~overhead[g.tb]] = np.inf
        ti = int(np.argmin(dist))
        near = int(g.tb[ti])
        # score the line the incident report names when it is in the model (Hurst: Eagle Rock-Sylmar 220 kV = L171),
        # else the nearest overhead line to the origin
        li = g.lix[f["facility_line"]] if f.get("facility_line") in g.lix else near
        idx_oh = np.where(overhead)[0]
        pos = int(np.where(idx_oh == li)[0][0])
        near_line = g.branches[near]
        risk = r["line_risk"][h][idx_oh]
        gust = r["line_gust"][h][idx_oh]
        line = g.branches[li]
        cases.append({
            "fire": f["name"], "event": eid, "split": D.events()[eid]["split"], "ignition_utc": str(t), "utility_related": f["utility_related"],
            "facility": f["facility"], "source_url": f["source_url"], "origin": [f["lat"], f["lon"]],
            "scored_line": line["id"], "scored_line_basis": "named in the incident report" if li != near else "nearest overhead line",
            "nearest_line": line["id"], "nearest_line_name": line.get("name"), "nearest_line_kv": line["kv"],
            "distance_km": float(dist[g.tb == li].min()),
            "nearest_overhead_line": near_line["id"], "nearest_overhead_line_km": float(dist[ti]),
            "line_risk": float(risk[pos]), "line_gust_ms": float(gust[pos]), "n_lines": int(len(idx_oh)),
            "model_top_pct": pct_rank(risk, pos), "gust_only_top_pct": pct_rank(gust, pos), "random_expected_top_pct": 50.0,
            "line_alarm": D.calibration()["fire"]["line_alarm"], "above_line_alarm": bool(risk[pos] >= D.calibration()["fire"]["line_alarm"]),
            "caveat": ("the igniting facility is a 16 kV distribution circuit, below the 66 kV model floor" if f["facility_voltage_kv"] and f["facility_voltage_kv"] < 66
                       else "the igniting tower carried an idle (de-energized) line; OSM may not map it" if "idle" in f["facility"] else ""),
        })
    test = [c for c in cases if c["split"] == "test"]
    med = float(np.median([c["model_top_pct"] for c in test])) if test else None
    med_g = float(np.median([c["gust_only_top_pct"] for c in test])) if test else None
    out = {"check": "V5", "cases": cases, "n_test_cases": len(test), "median_model_top_pct_test": med, "median_gust_top_pct_test": med_g,
           "headline": " ; ".join(f"{c['fire']}: line {c['nearest_line']} ({c['distance_km']:.1f} km) ranked top {c['model_top_pct']:.0f}% "
                                  f"(gust-only {c['gust_only_top_pct']:.0f}%)" for c in test),
           "baseline": "Random rank (50%) and ranking by gust alone",
           "caveat": f"Only {len(test)} test ignitions (plus {len(cases) - len(test)} training). Individual cases, not a statistic."}
    save("v5_ignition", out)
    return out


if __name__ == "__main__":
    r = run()
    for c in r["cases"]:
        print(c["fire"], c["split"], c["nearest_line"], c["nearest_line_kv"], round(c["distance_km"], 2), "km | model top", round(c["model_top_pct"], 1), "% | gust top", round(c["gust_only_top_pct"], 1), "% | risk", round(c["line_risk"], 2), "gust", round(c["line_gust_ms"], 1))
