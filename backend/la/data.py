"""Cached loaders for everything in data/processed (the app and validation read ONLY from there)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
P = ROOT / "data" / "processed"
REGION = "la"


@lru_cache
def grid() -> dict:
    return json.loads((P / f"grid_{REGION}.json").read_text())


@lru_cache
def terrain():
    z = np.load(P / f"terrain_{REGION}.npz")
    return {"elev": z["elev"], "lats": z["lats"], "lons": z["lons"]}


@lru_cache
def weather(name: str) -> dict:
    """Event grid (E1..C3) or 'series_2019_2024'. time = datetime64[h] (UTC)."""
    z = np.load(P / "weather" / f"{name}.npz")
    d = {k: z[k] for k in z.files}
    d["time"] = d["time"].astype("datetime64[h]")
    return d


@lru_cache
def demand() -> pd.DataFrame:
    d = pd.read_csv(P / f"demand_{REGION}.csv.gz", parse_dates=["utc_time"])
    p = d.pivot_table(index="utc_time", columns="respondent", values="demand_mw").sort_index()
    p.index = p.index.tz_localize("UTC")
    return p


@lru_cache
def outages() -> pd.DataFrame:
    """EAGLE-I customers out per county, hourly MEDIAN of the 15-min runs (UTC; robust to single-run glitches such
    as a 2,632 reading between 73,000s). QC: LA County readings above 1,000,000 are dropped (Jan 8 2025 has runs
    of ~1.7 M, about half of all LA County customers, far above every other report; see DECISIONS.md)."""
    cache = P / f"outages_{REGION}_hourly.csv.gz"
    if cache.exists():
        o = pd.read_csv(cache, index_col=0, parse_dates=True)
        o.index = o.index.tz_convert("UTC") if o.index.tz else o.index.tz_localize("UTC")
        return o
    frames = []
    for f in sorted((ROOT / "data" / "raw" / "eaglei").glob(f"eaglei_{REGION}_*.csv")):
        df = pd.read_csv(f, dtype={"fips_code": str})
        df["fips_code"] = df["fips_code"].str.zfill(5)
        frames.append(df)
    df = pd.concat(frames)
    df["t"] = pd.to_datetime(df["run_start_time"]).dt.floor("h")
    df = df[~((df.fips_code == "06037") & (df.customers_out > 1_000_000))]
    o = df.pivot_table(index="t", columns="fips_code", values="customers_out", aggfunc="median").sort_index()
    full = pd.date_range(o.index.min(), o.index.max(), freq="h")
    o = o.reindex(full)  # missing 15-min runs stay NaN (EAGLE-I gaps), never zero-filled
    o.index = o.index.tz_localize("UTC")
    o.to_csv(cache)
    return o


@lru_cache
def asos() -> pd.DataFrame:
    a = pd.read_csv(P / f"asos_{REGION}.csv.gz", parse_dates=["valid"])
    a["valid"] = a["valid"].dt.tz_localize("UTC")
    return a


@lru_cache
def events() -> dict:
    ev = yaml.safe_load((ROOT / "validation" / "events.yaml").read_text())["events"]
    cfg = yaml.safe_load((ROOT / "validation" / "config.yaml").read_text())
    out = {}
    for e in ev:
        e = dict(e)
        e["split"] = "train" if e["id"] in cfg["split"]["train"] else "test" if e["id"] in cfg["split"]["test"] else "none"
        e["start_utc"] = datetime.fromisoformat(e["start"]).astimezone(timezone.utc)
        e["end_utc"] = datetime.fromisoformat(e["end"]).astimezone(timezone.utc)
        out[e["id"]] = e
    return out


@lru_cache
def config() -> dict:
    return yaml.safe_load((ROOT / "validation" / "config.yaml").read_text())


@lru_cache
def fires() -> dict:
    facts = {f["id"]: f for f in yaml.safe_load((P / "events" / "fires.yaml").read_text())["fires"]}
    wf = json.loads((P / "events" / "fires_wfigs.json").read_text())
    for k, f in facts.items():
        w = wf.get(k, {})
        f["lat"], f["lon"] = w.get("lat"), w.get("lon")
        f["wfigs_source_url"] = w.get("source_url")
        f["perimeter"] = w.get("perimeter")
        f["ignition_utc"] = datetime.fromisoformat(f["ignition_local"]).astimezone(timezone.utc)
    return facts


@lru_cache
def psps() -> dict:
    return yaml.safe_load((P / "events" / "sce_psps_2025_01.yaml").read_text())


@lru_cache
def redflag() -> dict:
    return json.loads((P / f"redflag_{REGION}.json").read_text())


@lru_cache
def population() -> pd.DataFrame:
    return pd.read_csv(P / f"population_{REGION}.csv", dtype={"geoid": str, "county": str})


DEFAULT_CAL = {
    "gust": {"a": {"coast": 0.0, "basin": 0.0, "valley": 0.0, "mountain": 0.0}, "b": {"coast": 1.0, "basin": 1.0, "valley": 1.0, "mountain": 1.0}},
    "fire": {"g50": 15.0, "h50": 20.0, "c50": 9.0, "line_alarm": 0.5, "region_alarm": None},
    "load": None,
}


@lru_cache(maxsize=4)
def _calibration(mtime: float) -> dict:
    return json.loads((P / "calibration.json").read_text())


def calibration() -> dict:
    """Cached; re-read only when calibration.json changes on disk (audit: it was re-parsed several times per simulation).
    Callers must not mutate the returned dict."""
    f = P / "calibration.json"
    return _calibration(f.stat().st_mtime) if f.exists() else DEFAULT_CAL


def shown_risk(risk):
    """A line's fire index as the screen shows it: three decimals, rounded DOWN. Decisions (the alarm, the plans) are
    made on the unrounded index. Rounding to nearest let a line at 0.29996 show as 0.300 and be coloured and counted as
    over a 0.30 alarm that it had not reached; rounding down means a shown value reaches the alarm only when the index did.
    Works on a number or a numpy array."""
    return np.floor(np.asarray(risk, dtype=float) * 1000 + 1e-9) / 1000 if isinstance(risk, np.ndarray) else float(np.floor(float(risk) * 1000 + 1e-9) / 1000)


def results_dir() -> Path:
    d = ROOT / "validation" / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d
