"""V7. Red Flag consistency (independent NWS check). Over every event window: what fraction of high-risk line-hours
(risk >= line alarm) fall inside an active Red Flag Warning zone, and what fraction of active Red Flag zone-hours
(zones crossed by our lines) contain at least one high-risk line."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402

from common import D, E, event_hindcast, save  # noqa: E402


def polys(geom):
    parts = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [MPath(np.array(p[0])) for p in parts]


def run():
    g = E.LAGrid()
    rf = D.redflag()
    alarm = D.calibration()["fire"]["line_alarm"]
    pts = np.c_[g.tlon, g.tlat]
    zone_lines = {}
    for ugc, z in rf["zones"].items():
        inside = np.zeros(len(pts), bool)
        for p in polys(z["geometry"]):
            inside |= p.contains_points(pts)
        lines = np.unique(g.tb[inside])
        if len(lines):
            zone_lines[ugc] = lines
    warn = pd.DataFrame(rf["events"])
    warn["issue"] = pd.to_datetime(warn["issue"], utc=True)
    warn["expire"] = pd.to_datetime(warn["expire"], utc=True)
    tot_hi = hi_in = zh = zh_hit = 0
    per = {}
    for eid in D.events():
        times, r = event_hindcast(eid, grid=g)
        hi = (r["line_risk"] >= alarm) & ~g.cable[None]
        e_hi = e_in = e_zh = e_hit = 0
        for h, t in enumerate(times):
            act = warn[(warn.issue <= t) & (warn.expire > t)].ugc.unique()
            covered = np.zeros(g.nl, bool)
            for u in act:
                if u in zone_lines:
                    covered[zone_lines[u]] = True
                    e_zh += 1
                    e_hit += bool(hi[h, zone_lines[u]].any())
            e_hi += int(hi[h].sum())
            e_in += int((hi[h] & covered).sum())
        per[eid] = {"high_risk_line_hours": e_hi, "inside_red_flag": e_in, "red_flag_zone_hours": e_zh, "zone_hours_with_high_risk_line": e_hit}
        tot_hi, hi_in, zh, zh_hit = tot_hi + e_hi, hi_in + e_in, zh + e_zh, zh_hit + e_hit
    p = hi_in / tot_hi if tot_hi else None
    rcl = zh_hit / zh if zh else None
    out = {"check": "V7", "events": per, "frac_high_risk_inside_red_flag": p, "frac_red_flag_zone_hours_with_high_risk": rcl,
           "zones_crossing_lines": len(zone_lines),
           "headline": f"{p * 100:.0f}% of high-risk line-hours fall inside an active Red Flag Warning; "
                       f"{rcl * 100:.0f}% of Red Flag zone-hours contain a high-risk line" if p is not None and rcl is not None else "insufficient overlap",
           "baseline": "NWS Red Flag Warnings (independent forecaster judgement)"}
    save("v7_redflag", out)
    return out


if __name__ == "__main__":
    r = run()
    print(r["headline"], r["events"])
