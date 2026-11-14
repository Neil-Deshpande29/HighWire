"""V3. Demand model vs actual hourly load in the H2 heat wave (test). Weather-only prediction for LDWP and SCE.
Metrics: peak error %, peak-hour error (h), MAPE. Baseline: same hour last week (persistence)."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from calibrate import predict_load  # noqa: E402
from common import D, RES, save  # noqa: E402


def metrics(pred, act, idx):
    m = np.isfinite(pred) & np.isfinite(act)
    p, a, t = pred[m], act[m], idx[m]
    ip, ia = int(np.argmax(p)), int(np.argmax(a))
    return {"peak_actual_mw": float(a[ia]), "peak_pred_mw": float(p[ip]), "peak_error_pct": float((p[ip] - a[ia]) / a[ia] * 100),
            "peak_hour_error_h": float((t[ip] - t[ia]).total_seconds() / 3600), "mape_pct": float(np.mean(np.abs(p - a) / a) * 100),
            "actual_peak_time_local": str(t[ia].tz_convert("America/Los_Angeles"))}


def run(eid="H2"):
    cal = D.calibration()
    ev = D.events()[eid]
    idx = pd.date_range(ev["start_utc"], ev["end_utc"], freq="h", inclusive="left")
    dem = D.demand()
    pred = predict_load(cal["load"], idx)
    out = {"check": "V3", "event": eid, "split": ev["split"], "by_ba": {}}
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    for ax, ba in zip(axes, ["LDWP", "SCE"]):
        act = dem[ba].reindex(idx).values
        base = dem[ba].reindex(idx - pd.Timedelta(days=7)).values
        out["by_ba"][ba] = {"model": metrics(pred[ba], act, idx), "baseline_last_week": metrics(base, act, idx)}
        loc = idx.tz_convert("America/Los_Angeles").tz_localize(None)
        ax.plot(loc, act, "k-", lw=1.5, label="actual (EIA-930)")
        ax.plot(loc, pred[ba], "-", lw=1.2, label=f"Highwire weather model (MAPE {out['by_ba'][ba]['model']['mape_pct']:.1f}%)")
        ax.plot(loc, base, ":", lw=1, label=f"same hour last week (MAPE {out['by_ba'][ba]['baseline_last_week']['mape_pct']:.1f}%)")
        ax.set_title(f"{ba} hourly demand, {ev['name']}", fontsize=9)
        ax.set_ylabel("MW")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(RES / "v3_demand.png", dpi=110)
    plt.close(fig)
    s = out["by_ba"]["SCE"]["model"]
    l = out["by_ba"]["LDWP"]["model"]
    out["headline"] = (f"{eid}: predicted the heat-wave peak within {abs(s['peak_error_pct']):.1f}% (SCE) and "
                       f"{abs(l['peak_error_pct']):.1f}% (LDWP) from weather alone")
    out["baseline"] = "Same hour last week"
    out["plot"] = "v3_demand.png"
    save("v3_demand", out)
    return out


if __name__ == "__main__":
    r = run()
    print(r["headline"])
    for ba, v in r["by_ba"].items():
        print(ba, {k: round(x, 2) if isinstance(x, float) else x for k, x in v["model"].items()}, "| baseline", {k: round(x, 2) if isinstance(x, float) else x for k, x in v["baseline_last_week"].items()})
