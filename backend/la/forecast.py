"""LIVE FORECAST mode: pull the current Open-Meteo forecast (keyless) for the LA grid, run the same calibrated
physics (power flow, IEEE 738, fire chain, storm damage, demand model) over the past 48 h + next 72 h, and save it
as validation/results/replay_FX.npz so the replay UI and the co-pilot can show "what is coming".
Run:  python -m backend.la.forecast      (or POST /la/forecast/refresh, or `make forecast`)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

from . import data as D
from . import engine as E
from . import load_model as LM
from . import storm as S

URL = "https://api.open-meteo.com/v1/forecast"
VARS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "shortwave_radiation", "surface_pressure", "precipitation"]
PAST_DAYS, FORECAST_DAYS, SHOW_PAST_H = 2, 4, 12
COUNTIES = ["06037", "06111", "06059", "06071"]
WX_FILE = D.P / "weather" / "FX.npz"


def fetch(chunk=50):
    import yaml

    reg = yaml.safe_load((D.ROOT / "config" / "region_la.yaml").read_text())
    b, step = reg["bbox"], reg["weather_grid_deg"]
    lats = np.round(np.arange(b["lat_min"], b["lat_max"] + 1e-9, step), 3)
    lons = np.round(np.arange(b["lon_min"], b["lon_max"] + 1e-9, step), 3)
    pts = [(la, lo) for la in lats for lo in lons]
    out = []
    for i in range(0, len(pts), chunk):
        part = pts[i:i + chunk]
        r = requests.get(URL, params={"latitude": ",".join(str(p[0]) for p in part), "longitude": ",".join(str(p[1]) for p in part),
                                      "hourly": ",".join(VARS), "past_days": PAST_DAYS, "forecast_days": FORECAST_DAYS,
                                      "timezone": "GMT", "wind_speed_unit": "ms"},
                         headers={"User-Agent": "highwire-hackathon-research/0.1"}, timeout=120)
        r.raise_for_status()
        d = r.json()
        out += d if isinstance(d, list) else [d]
        time.sleep(1)
    t = np.array([np.datetime64(x) for x in out[0]["hourly"]["time"]], dtype="datetime64[h]")
    arrs = {v: np.array([o["hourly"][v] for o in out], dtype=np.float32).T.reshape(len(t), len(lats), len(lons)) for v in VARS}
    fetched = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    np.savez_compressed(WX_FILE, time=t.astype("int64"), lats=lats, lons=lons, fetched_utc=str(fetched), **arrs)
    return fetched


def live_event() -> dict | None:
    """Event-like dict for the live forecast window (or None if no forecast was fetched)."""
    if not WX_FILE.exists():
        return None
    z = np.load(WX_FILE)
    fetched = datetime.fromisoformat(str(z["fetched_utc"]))
    t = z["time"].astype("datetime64[h]")
    end = pd.Timestamp(t[-1]).tz_localize("UTC").to_pydatetime() + timedelta(hours=1)
    start = fetched - timedelta(hours=SHOW_PAST_H)
    ahead = int((end - fetched).total_seconds() // 3600)
    return {"id": "FX", "name": f"Live forecast issued {fetched:%Y-%m-%d %H:00} UTC (next {ahead} h)", "kind": "forecast",
            "split": "none", "start": start.isoformat(), "end": end.isoformat(), "start_utc": start, "end_utc": end,
            "fetched_utc": fetched.isoformat(), "fires": []}


def build():
    """Run the calibrated models over the live forecast and save replay_FX.npz (same keys as build_replays)."""
    cal = D.calibration()
    grid = E.LAGrid()
    wx = D.weather("FX")
    ev = live_event()
    hrs = E.hours_for(wx, ev["start_utc"], ev["end_utc"])
    dem = LM.predict_from_weather(wx)
    ld, sc = dem["LDWP"][hrs], dem["SCE"][hrs]
    r = E.hindcast(grid, wx, hrs, ld, sc, cal=cal)
    st = S.event_storm(grid, "FX", cal, wx=wx, ev=ev)
    times = wx["time"][hrs]
    nan_out = np.full((len(hrs), len(COUNTIES)), np.nan, dtype=np.float32)
    np.savez_compressed(
        D.results_dir() / "replay_FX.npz",
        time=times.astype("datetime64[h]").astype("int64"),
        line_risk=r["line_risk"].astype(np.float32), loading=r["loading"].astype(np.float32),
        line_temp=r["line_temp"].astype(np.float32), line_clear=np.minimum(r["line_clear"], 99).astype(np.float32),
        line_sag=r["line_sag"].astype(np.float32), line_gust=r["line_gust"].astype(np.float32), line_rh=r["line_rh"].astype(np.float32),
        flow_mw=r["flow_mw"].astype(np.float32), region_index=r["region_index"].astype(np.float32), load_mw=r["load_mw"].astype(np.float32),
        demand_ldwp=ld.astype(np.float32), demand_sce=sc.astype(np.float32), outages=nan_out,
        wx_gust=wx["wind_gusts_10m"][hrs].astype(np.float32), wx_rh=wx["relative_humidity_2m"][hrs].astype(np.float32),
        wx_temp=wx["temperature_2m"][hrs].astype(np.float32), wx_dir=wx["wind_direction_10m"][hrs].astype(np.float32),
        wx_lats=wx["lats"], wx_lons=wx["lons"],
        storm_index=st["storm_index"].astype(np.float32), storm_hazard=st["storm_hazard"].astype(np.float32),
        rain_hazard=st["rain_hazard"].astype(np.float32), line_damage=st["line_damage"].astype(np.float32),
        wx_precip=np.nan_to_num(wx["precipitation"][hrs]).astype(np.float32))
    summary = {"fetched_utc": ev["fetched_utc"], "hours": int(len(hrs)), "max_storm_index": float(st["storm_index"].max()),
               "storm_alarm": cal["storm"]["alarm"], "max_fire_index": float(r["region_index"].max()),
               "fire_alarm": cal["fire"]["region_alarm"], "max_gust_ms": float(np.nanmax(wx["wind_gusts_10m"][hrs])),
               "min_rh_pct": float(np.nanmin(wx["relative_humidity_2m"][hrs]))}
    (D.results_dir() / "forecast_FX.json").write_text(json.dumps(summary, indent=1))
    return summary


def refresh():
    fetch()
    D.weather.cache_clear()
    return build()


if __name__ == "__main__":
    print(refresh())
