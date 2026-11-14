"""Source 11: 2020 Census tract population (PL 94-171 via the Census API; key optional) + tract centroids
(2020 Gazetteer). -> data/processed/population_<region>.csv (geoid, lat, lon, pop). Fallback if this fails:
build_grid allocates load by substation count per zone (logged)."""
from __future__ import annotations

import io
import os
import zipfile

import pandas as pd

from common import PROCESSED, RAW, get, log, region

L = log("census")
GAZ = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_gaz_tracts_06.txt"
COUNTIES = ["037", "059", "065", "071", "111"]  # LA, Orange, Riverside, San Bernardino, Ventura


PL_ZIP = "https://www2.census.gov/programs-surveys/decennial/2020/data/01-Redistricting_File--PL_94-171/California/ca2020.pl.zip"


def main():
    """The Census API now rejects keyless requests, so we read POP100 + internal point straight from the
    PL 94-171 geographic header (cageo2020.pl, pipe-delimited) for summary level 140 (tracts)."""
    reg, b = region(), region()["bbox"]
    zf = RAW / "census" / "ca2020.pl.zip"
    if not zf.exists():
        zf.parent.mkdir(parents=True, exist_ok=True)
        zf.write_bytes(get(PL_ZIP, timeout=600).content)
    z = zipfile.ZipFile(zf)
    geo = next(n for n in z.namelist() if n.lower().startswith("cageo"))
    rows = []
    for line in io.TextIOWrapper(z.open(geo), encoding="latin-1"):
        f = line.rstrip("\n").split("|")
        if f[2] != "140":
            continue
        # tail of the 2020 geoheader: ... POP100 | HU100 | INTPTLAT | INTPTLON | LSADC | PARTFLAG | UGA
        rows.append({"geoid": f[9], "county": f[14], "pop": int(f[-7]), "lat": float(f[-5]), "lon": float(f[-4])})
    pop = pd.DataFrame(rows)
    pop = pop[pop.county.isin(COUNTIES)]
    pop = pop[pop.lat.between(b["lat_min"], b["lat_max"]) & pop.lon.between(b["lon_min"], b["lon_max"])]
    pop.to_csv(PROCESSED / f"population_{reg['name']}.csv", index=False)
    L.info("%d tracts, %.2f M people in bbox", len(pop), pop["pop"].sum() / 1e6)


if __name__ == "__main__":
    main()
