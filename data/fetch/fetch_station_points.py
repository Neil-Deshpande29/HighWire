"""Source 4b: Open-Meteo hourly gust / wind at the exact ASOS station coordinates, 2018-2025.
Used only to fit the gridded->station gust correction on the calibration years (2019-2021), which contain far more
Santa Ana hours than the training events. -> data/processed/weather/station_points.npz"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from common import PROCESSED, RAW, cached_json, get, log

L = log("openmeteo_points")
URL = "https://archive-api.open-meteo.com/v1/archive"
VARS = ["wind_gusts_10m", "wind_speed_10m"]


def main():
    out = PROCESSED / "weather" / "station_points.npz"
    if out.exists():
        L.info("%s exists", out.name)
        return
    a = pd.read_csv(PROCESSED / "asos_la.csv.gz", usecols=["station", "lat", "lon"]).groupby("station").first()
    arrs, t = {v: [] for v in VARS}, None
    for st, r in a.iterrows():
        f = RAW / "openmeteo" / f"station_{st}.json"
        d = cached_json(f, lambda r=r: get(URL, {"latitude": r.lat, "longitude": r.lon, "start_date": "2018-01-01",
                                                "end_date": "2025-01-31", "hourly": ",".join(VARS), "timezone": "GMT",
                                                "wind_speed_unit": "ms"}, timeout=300).json())
        t = np.array([np.datetime64(x) for x in d["hourly"]["time"]], dtype="datetime64[h]")
        for v in VARS:
            arrs[v].append(np.array(d["hourly"][v], dtype=np.float32))
        L.info("%s ok (%d h)", st, len(t))
        time.sleep(2)
    np.savez_compressed(out, time=t.astype("int64"), stations=np.array(a.index), lats=a.lat.values, lons=a.lon.values,
                        **{v: np.stack(x, 1) for v, x in arrs.items()})


if __name__ == "__main__":
    main()
