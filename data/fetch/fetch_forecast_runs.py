"""Source 15: archived weather FORECASTS (Open-Meteo Previous Runs API, keyless). `<var>_previous_dayN` is the value
that the forecast issued N days earlier predicted for that hour, i.e. what an operator could have known N*24 h ahead.
Archive starts around 2024-01-20, so it covers E3 (Jan 2025) and most of 2024, but not E1/E2/H1/H2.

  python fetch_forecast_runs.py events  -> data/processed/weather/E3_fc1.npz, E3_fc2.npz  (same grid/keys as E3.npz)
  python fetch_forecast_runs.py series  -> data/processed/weather/series_fc1_2024.npz     (0.25 deg grid, day-ahead)
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

import numpy as np
import yaml

from common import PROCESSED, RAW, ROOT, cached_json, get, log, region
from fetch_weather_grid import grid

L = log("openmeteo_runs")
URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "shortwave_radiation", "precipitation"]
ARCHIVE_START = "2024-01-20"


def fetch(lats, lons, start, end, lead_days, cache, chunk=20):
    pts = [(la, lo) for la in lats for lo in lons]
    names = [f"{v}_previous_day{lead_days}" for v in VARS]
    out = []
    for i in range(0, len(pts), chunk):
        part = pts[i:i + chunk]
        f = cache.with_name(f"{cache.stem}_{i}.json")
        data = cached_json(f, lambda part=part: get(URL, {
            "latitude": ",".join(str(p[0]) for p in part), "longitude": ",".join(str(p[1]) for p in part),
            "start_date": start, "end_date": end, "hourly": ",".join(names), "timezone": "GMT",
            "wind_speed_unit": "ms"}, timeout=300).json())
        out += data if isinstance(data, list) else [data]
        time.sleep(2)
    t = np.array([np.datetime64(x) for x in out[0]["hourly"]["time"]], dtype="datetime64[h]")
    arrs = {v: np.array([o["hourly"][n] for o in out], dtype=np.float32).T.reshape(len(t), len(lats), len(lons))
            for v, n in zip(VARS, names)}
    return t, arrs


def main(mode):
    reg = region()
    wdir = PROCESSED / "weather"
    cfg = yaml.safe_load((ROOT / "validation/config.yaml").read_text())
    if mode == "events":
        lats, lons = grid(reg["bbox"], reg["weather_grid_deg"])
        for ev in yaml.safe_load((ROOT / "validation/events.yaml").read_text())["events"]:
            s = (datetime.fromisoformat(ev["start"]) - timedelta(days=cfg["weather_pad_days"]["before"])).date()
            e = (datetime.fromisoformat(ev["end"]) + timedelta(days=cfg["weather_pad_days"]["after"])).date()
            if str(s) < ARCHIVE_START:
                continue
            for lead in (1, 2):
                out = wdir / f"{ev['id']}_fc{lead}.npz"
                if out.exists():
                    L.info("%s exists", out.name)
                    continue
                t, arrs = fetch(lats, lons, str(s), str(e), lead, RAW / "openmeteo_runs" / f"{ev['id']}_fc{lead}.json")
                np.savez_compressed(out, time=t.astype("int64"), lats=lats, lons=lons, **arrs)
                L.info("%s: %d h, NaN share %.3f", out.name, len(t), float(np.isnan(arrs["wind_gusts_10m"]).mean()))
                time.sleep(10)
    else:
        lats, lons = grid(reg["bbox"], reg["weather_grid_coarse_deg"])
        out = wdir / "series_fc1_2024.npz"
        if out.exists():
            L.info("%s exists", out.name)
            return
        t, arrs = fetch(lats, lons, ARCHIVE_START, "2024-12-31", 1, RAW / "openmeteo_runs" / "series_fc1.json", chunk=4)
        np.savez_compressed(out, time=t.astype("int64"), lats=lats, lons=lons, **arrs)
        L.info("%s: %d h, NaN share %.3f", out.name, len(t), float(np.isnan(arrs["wind_gusts_10m"]).mean()))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "events")
