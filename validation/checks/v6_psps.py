"""V6 (stretch, E3 only). De-energization comparison at county level. Our rule: the coordinator would de-energize
every overhead line whose risk reaches the calibrated line alarm during the PSPS window; we aggregate those lines to
counties (via their substations) and compare with the counties SCE actually de-energized (CPUC post-event report).
Circuit-level comparison is NOT possible: SCE's per-circuit list (Attachment C, Excel) was not reachable."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # validation/

import numpy as np  # noqa: E402

from common import D, E, event_hindcast, save  # noqa: E402

NAMES = {"06037": "Los Angeles", "06111": "Ventura", "06059": "Orange", "06071": "San Bernardino", "06065": "Riverside"}


def run():
    g = E.LAGrid()
    times, r = event_hindcast("E3", grid=g)
    alarm = D.calibration()["fire"]["line_alarm"]
    ps = D.psps()
    flagged = np.where((r["line_risk"] >= alarm).any(0) & ~g.cable)[0]
    first = {}
    for li in flagged:
        h = int(np.argmax(r["line_risk"][:, li] >= alarm))
        for b in (g.f[li], g.t[li]):
            c = NAMES.get(g.bus_county[b], g.bus_county[b])
            first[c] = min(first.get(c, times[h]), times[h])
    ours = set(first)
    in_bbox_counties = {NAMES[c] for c in set(g.bus_county) if c in NAMES}
    sce = set(ps["counties_deenergized_in_bbox"]) & in_bbox_counties
    inter, union = ours & sce, ours | sce
    km = float(g.length_km[flagged].sum())
    out = {"check": "V6", "our_counties": sorted(ours), "sce_counties_in_bbox": sorted(sce), "overlap": sorted(inter),
           "jaccard": len(inter) / len(union) if union else None, "lines_flagged": int(len(flagged)), "line_km_flagged": km,
           "first_flag_local": {c: str(t.tz_convert("America/Los_Angeles")) for c, t in first.items()},
           "sce_customers_deenergized": ps["customers_deenergized"], "sce_circuits_deenergized": ps["distribution_circuits_deenergized"],
           "source_url": ps["source_url"],
           "headline": f"County overlap with SCE's Jan 2025 PSPS: {len(inter)}/{len(union)} (Jaccard {len(inter) / len(union):.2f}); "
                       f"we would de-energize {len(flagged)} lines ({km:.0f} km)",
           "baseline": "None meaningful at county level (SCE de-energized circuits in every bbox county).",
           "caveat": "County-level only; SCE's per-circuit table (Attachment C) was not reachable, so this cannot test where or when within a county."}
    save("v6_psps", out)
    return out


if __name__ == "__main__":
    print(run())
