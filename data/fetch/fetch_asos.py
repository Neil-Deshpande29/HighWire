"""Source 5: IEM ASOS station observations (ground truth). -> data/processed/asos_<region>.csv
Columns: station, lat, lon, valid (UTC), tmpc, relh, wind_ms, drct, gust_ms. Missing stations are dropped and logged."""
from __future__ import annotations

import io

import pandas as pd

from common import PROCESSED, RAW, get, log, region

L = log("asos")
URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
KT = 0.514444


def main():
    reg = region()
    frames = []
    for y in range(2018, 2026):
        f = RAW / "asos" / f"{reg['name']}_{y}.csv"
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            params = [("station", s) for s in reg["asos_stations"]] + [
                ("data", "tmpf"), ("data", "relh"), ("data", "sknt"), ("data", "drct"), ("data", "gust"),
                ("year1", y), ("month1", 1), ("day1", 1), ("year2", y + 1 if y < 2025 else 2025), ("month2", 1 if y < 2025 else 2),
                ("day2", 1), ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "yes"), ("missing", "empty"), ("trace", "empty")]
            f.write_text(get(URL, params, timeout=600).text)
            L.info("fetched %s", f.name)
        frames.append(pd.read_csv(f))
    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={"lon": "lon", "lat": "lat"})
    out = pd.DataFrame({
        "station": "K" + df["station"].astype(str).str.lstrip("K"), "lat": df["lat"], "lon": df["lon"],
        "valid": pd.to_datetime(df["valid"]), "tmpc": (pd.to_numeric(df["tmpf"], errors="coerce") - 32) * 5 / 9,
        "relh": pd.to_numeric(df["relh"], errors="coerce"), "wind_ms": pd.to_numeric(df["sknt"], errors="coerce") * KT,
        "drct": pd.to_numeric(df["drct"], errors="coerce"), "gust_ms": pd.to_numeric(df["gust"], errors="coerce") * KT})
    have = sorted(out["station"].unique())
    L.info("stations with data: %s; missing: %s", have, sorted(set(reg["asos_stations"]) - set(have)))
    out.to_csv(PROCESSED / f"asos_{reg['name']}.csv.gz", index=False)
    L.info("%d observations", len(out))


if __name__ == "__main__":
    main()
