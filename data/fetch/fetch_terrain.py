"""Source 1: AWS Terrain Tiles (Terrarium). Mosaic -> regular lat/lon grid -> data/processed/terrain_<region>.npz.

Decode: elev = R*256 + G + B/256 - 32768 (meters). Fallback (not implemented, logged): USGS 3DEP via py3dep.
"""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

from common import PROCESSED, RAW, get, log, region

L = log("terrain")
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def tile_xy(lat, lon, z):
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def main():
    reg = region()
    b, z = reg["bbox"], reg["terrain_zoom"]
    x0, y0 = tile_xy(b["lat_max"], b["lon_min"], z)
    x1, y1 = tile_xy(b["lat_min"], b["lon_max"], z)
    xs, ys = range(int(x0), int(x1) + 1), range(int(y0), int(y1) + 1)
    tdir = RAW / "terrain"
    tdir.mkdir(parents=True, exist_ok=True)
    mosaic = np.zeros((len(ys) * 256, len(xs) * 256), dtype=np.float32)
    for j, ty in enumerate(ys):
        for i, tx in enumerate(xs):
            f = tdir / f"{z}_{tx}_{ty}.png"
            if not f.exists():
                f.write_bytes(get(URL.format(z=z, x=tx, y=ty)).content)
            a = np.asarray(Image.open(io.BytesIO(f.read_bytes())).convert("RGB")).astype(np.float32)
            mosaic[j * 256:(j + 1) * 256, i * 256:(i + 1) * 256] = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    L.info("mosaic %s from %d tiles", mosaic.shape, len(xs) * len(ys))

    # resample to a regular lat/lon grid (~0.0025 deg ~ 250 m)
    step = 0.0025
    lats = np.arange(b["lat_min"], b["lat_max"] + 1e-9, step)
    lons = np.arange(b["lon_min"], b["lon_max"] + 1e-9, step)
    LON, LAT = np.meshgrid(lons, lats)
    n = 2**z
    px = ((LON + 180.0) / 360.0 * n - xs[0]) * 256
    py = ((1.0 - np.arcsinh(np.tan(np.radians(LAT))) / np.pi) / 2.0 * n - ys[0]) * 256
    elev = map_coordinates(mosaic, [py, px], order=1).astype(np.float32)
    out = PROCESSED / f"terrain_{reg['name']}.npz"
    np.savez_compressed(out, elev=elev, lats=lats, lons=lons)
    L.info("saved %s grid %s, elev %.0f..%.0f m, %.0f%% sea", out.name, elev.shape, elev.min(), elev.max(), 100 * (elev <= 0).mean())


if __name__ == "__main__":
    main()
