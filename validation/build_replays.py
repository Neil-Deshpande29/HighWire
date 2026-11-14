"""Precompute hour-by-hour replays for the app (validation/results/replay_<EVENT>.npz).
The API serves replay numbers straight from these files, so every number in the UI traces back to validation/results."""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import D, E, RES, event_hindcast

COUNTIES = ["06037", "06111", "06059", "06071"]


def build(eid, grid):
    times, r = event_hindcast(eid, grid=grid)
    o = D.outages().reindex(times)
    ld, sc = E.event_demand(np.asarray(times.tz_localize(None), dtype="datetime64[h]"))
    wx = D.weather(eid)
    hi = E.hours_for(wx, D.events()[eid]["start_utc"], D.events()[eid]["end_utc"])
    np.savez_compressed(
        RES / f"replay_{eid}.npz",
        time=np.asarray(times.tz_localize(None), dtype="datetime64[h]").astype("int64"),
        line_risk=r["line_risk"].astype(np.float32), loading=r["loading"].astype(np.float32),
        line_temp=r["line_temp"].astype(np.float32), line_clear=np.minimum(r["line_clear"], 99).astype(np.float32),
        line_sag=r["line_sag"].astype(np.float32), line_gust=r["line_gust"].astype(np.float32), line_rh=r["line_rh"].astype(np.float32),
        flow_mw=r["flow_mw"].astype(np.float32), region_index=r["region_index"].astype(np.float32), load_mw=r["load_mw"].astype(np.float32),
        demand_ldwp=ld.astype(np.float32), demand_sce=sc.astype(np.float32),
        outages=o[COUNTIES].values.astype(np.float32),
        wx_gust=wx["wind_gusts_10m"][hi].astype(np.float32), wx_rh=wx["relative_humidity_2m"][hi].astype(np.float32),
        wx_temp=wx["temperature_2m"][hi].astype(np.float32), wx_dir=wx["wind_direction_10m"][hi].astype(np.float32),
        wx_lats=wx["lats"], wx_lons=wx["lons"])
    return len(times)


def main():
    grid = E.LAGrid()
    for eid in D.events():
        print(eid, build(eid, grid), "hours")


if __name__ == "__main__":
    main()
