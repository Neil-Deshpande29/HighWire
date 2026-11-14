"""Source 6: EIA-930 hourly demand. Uses EIA API v2 if EIA_API_KEY is set; otherwise (default here) the public
six-month bulk CSVs (no key). Keeps BA demand for LDWP and CISO and the CAISO sub-BA SCE.
-> data/processed/demand_<region>.csv  (utc_time, respondent, demand_mw)"""
from __future__ import annotations

import io
import os

import pandas as pd

from common import PROCESSED, RAW, get, log, region

L = log("eia930")
BULK = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/EIA930_{kind}_{y}_{h}.csv"
YEARS = range(2018, 2026)


def bulk(kind, keep_col, keep_vals, demand_col):
    rows = []
    for y in YEARS:
        for h in ("Jan_Jun", "Jul_Dec"):
            f = RAW / "eia930" / f"{kind}_{y}_{h}.csv"
            if y == 2025 and h == "Jul_Dec":
                continue  # not needed (E3 is January 2025)
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                txt = get(BULK.format(kind=kind, y=y, h=h), timeout=600).text
                try:
                    df = pd.read_csv(io.StringIO(txt), low_memory=False)
                    df[keep_col]
                except Exception:  # noqa: BLE001  (EIA-930 starts 2018-07; earlier files do not exist)
                    L.warning("no usable %s file for %s %s, skipping", kind, y, h)
                    continue
                df = df[df[keep_col].isin(keep_vals)]
                df.to_csv(f, index=False)
                L.info("fetched %s (%d rows kept)", f.name, len(df))
            df = pd.read_csv(f, low_memory=False)
            tcol = next(c for c in df.columns if c.startswith("UTC Time"))
            # prefer EIA's adjusted (cleaned/imputed) demand when the file has it
            dcol = demand_col + " (Adjusted)" if demand_col + " (Adjusted)" in df.columns else demand_col
            v = pd.to_numeric(df[dcol].astype(str).str.replace(",", ""), errors="coerce")
            t = pd.to_datetime(df[tcol], format="%m/%d/%Y %I:%M:%S %p")
            rows.append(pd.DataFrame({"utc_time": t, "respondent": df[keep_col], "demand_mw": v}))
    return pd.concat(rows)


def main():
    reg = region()
    if os.environ.get("EIA_API_KEY"):
        L.info("EIA_API_KEY set, but the bulk path is used for reproducibility (same data, no paging).")
    ba = bulk("BALANCE", "Balancing Authority", reg["balancing_authorities"], "Demand (MW)")
    sub = bulk("SUBREGION", "Sub-Region", [reg["sub_ba"]], "Demand (MW)")
    df = pd.concat([ba, sub]).dropna().sort_values(["respondent", "utc_time"])
    # EIA "UTC Time at End of Hour" -> hour-start convention used everywhere else
    df["utc_time"] = df["utc_time"] - pd.Timedelta(hours=1)
    df.to_csv(PROCESSED / f"demand_{reg['name']}.csv.gz", index=False)
    L.info("demand rows: %s", df.groupby("respondent").size().to_dict())


if __name__ == "__main__":
    main()
