"""Source 4: Open-Meteo historical hourly weather on a lat/lon grid.

  python fetch_weather_grid.py events   -> 0.1 deg grid for every event window (+pad) -> data/processed/weather/<EVENT>.npz
  python fetch_weather_grid.py series   -> 0.25 deg grid, 2019-2024, fewer variables -> data/processed/weather/series_2019_2024.npz
Units: m/s, C, %, W/m2, hPa, mm. Times are UTC hour starts.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

from common import PROCESSED, RAW, ROOT, cached_json, get, log, region

L = log("openmeteo")
URL = "https://archive-api.open-meteo.com/v1/archive"
EVENT_VARS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
              "shortwave_radiation", "surface_pressure", "precipitation"]
SERIES_VARS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "shortwave_radiation"]


def grid(bbox, step):
    lats = np.round(np.arange(bbox["lat_min"], bbox["lat_max"] + 1e-9, step), 3)
    lons = np.round(np.arange(bbox["lon_min"], bbox["lon_max"] + 1e-9, step), 3)
    return lats, lons


def fetch(lats, lons, start, end, variables, cache: Path, chunk=60):
    pts = [(la, lo) for la in lats for lo in lons]
    out = []
    for i in range(0, len(pts), chunk):
        part = pts[i:i + chunk]
        f = cache.with_name(f"{cache.stem}_{i}.json")
        data = cached_json(f, lambda part=part: get(URL, {
            "latitude": ",".join(str(p[0]) for p in part), "longitude": ",".join(str(p[1]) for p in part),
            "start_date": start, "end_date": end, "hourly": ",".join(variables), "timezone": "GMT",
            "wind_speed_unit": "ms"}, timeout=300).json())
        if not f.exists():
            pass
        out += data if isinstance(data, list) else [data]
        time.sleep(1)
    t = np.array([np.datetime64(x) for x in out[0]["hourly"]["time"]], dtype="datetime64[h]")
    arrs = {v: np.array([o["hourly"][v] for o in out], dtype=np.float32).T.reshape(len(t), len(lats), len(lons)) for v in variables}
    return t, arrs


def main(mode):
    reg = region()
    wdir = PROCESSED / "weather"
    wdir.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load((ROOT / "validation/config.yaml").read_text())
    if mode == "events":
        lats, lons = grid(reg["bbox"], reg["weather_grid_deg"])
        for ev in yaml.safe_load((ROOT / "validation/events.yaml").read_text())["events"]:
            out = wdir / f"{ev['id']}.npz"
            if out.exists():
                L.info("%s exists", out.name)
                continue
            s = (datetime.fromisoformat(ev["start"]) - timedelta(days=cfg["weather_pad_days"]["before"])).date()
            e = (datetime.fromisoformat(ev["end"]) + timedelta(days=cfg["weather_pad_days"]["after"])).date()
            L.info("%s: %s..%s, %d points", ev["id"], s, e, len(lats) * len(lons))
            t, arrs = fetch(lats, lons, str(s), str(e), EVENT_VARS, RAW / "openmeteo" / f"{ev['id']}.json")
            np.savez_compressed(out, time=t.astype("int64"), lats=lats, lons=lons, **arrs)
            time.sleep(20)  # stay under the per-minute weighted limit
    else:
        lats, lons = grid(reg["bbox"], reg["weather_grid_coarse_deg"])
        out = wdir / "series_2019_2024.npz"
        if out.exists():
            L.info("%s exists", out.name)
            return
        t, arrs = fetch(lats, lons, "2019-01-01", "2024-12-31", SERIES_VARS, RAW / "openmeteo" / "series.json", chunk=1)
        np.savez_compressed(out, time=t.astype("int64"), lats=lats, lons=lons, **arrs)
        L.info("series saved: %s hours x %s points", len(t), len(lats) * len(lons))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "events")
