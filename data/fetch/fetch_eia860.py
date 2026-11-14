"""Source 13: EIA-860 (2023) plant locations + generator nameplate -> data/processed/plants_<region>.csv
Keeps operating generators at plants inside the bbox; capacity summed per plant."""
from __future__ import annotations

import io
import zipfile

import pandas as pd

from common import PROCESSED, RAW, get, log, region

L = log("eia860")
URL = "https://www.eia.gov/electricity/data/eia860/archive/xls/eia8602023.zip"


def main():
    reg, b = region(), region()["bbox"]
    f = RAW / "eia860" / "eia8602023.zip"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(get(URL, timeout=600).content)
    z = zipfile.ZipFile(f)
    pname = next(n for n in z.namelist() if n.startswith("2___Plant"))
    gname = next(n for n in z.namelist() if n.startswith("3_1_Generator"))
    plants = pd.read_excel(io.BytesIO(z.read(pname)), header=1)
    gens = pd.read_excel(io.BytesIO(z.read(gname)), sheet_name="Operable", header=1)
    plants["Latitude"] = pd.to_numeric(plants["Latitude"], errors="coerce")
    plants["Longitude"] = pd.to_numeric(plants["Longitude"], errors="coerce")
    gens["Nameplate Capacity (MW)"] = pd.to_numeric(gens["Nameplate Capacity (MW)"], errors="coerce")
    plants = plants[(plants.Latitude.between(b["lat_min"], b["lat_max"])) & (plants.Longitude.between(b["lon_min"], b["lon_max"]))]
    gens = gens[gens["Status"].astype(str).str.startswith("OP")]
    cap = gens.groupby("Plant Code").agg(nameplate_mw=("Nameplate Capacity (MW)", "sum"),
                                         tech=("Technology", lambda s: s.value_counts().index[0]))
    df = plants.merge(cap, left_on="Plant Code", right_index=True)
    out = df[["Plant Code", "Plant Name", "Utility Name", "Latitude", "Longitude", "nameplate_mw", "tech"]]
    out.columns = ["plant_code", "name", "utility", "lat", "lon", "nameplate_mw", "tech"]
    out.to_csv(PROCESSED / f"plants_{reg['name']}.csv", index=False)
    L.info("%d operating plants, %.0f MW nameplate in bbox", len(out), out.nameplate_mw.sum())


if __name__ == "__main__":
    main()
