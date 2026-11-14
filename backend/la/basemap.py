"""Aerial imagery under the map: one stitched picture of the region, drawn on the terrain when the layer is on.

Any XYZ tile service works. The default is the USGS National Map imagery service (public domain, no key). To use
another provider, for example Vexcel, set in .env:

    HIGHWIRE_BASEMAP_URL=https://.../{z}/{x}/{y}...     the provider's tile address; it may contain a key
    HIGHWIRE_BASEMAP_NAME=Vexcel

and rebuild with `make imagery`. The address is read from the environment only: it is never written to the cache,
the metadata or a log, because it can carry a key.

The picture is cached under data/processed/imagery/ (git-ignored: a provider's tiles may be licensed). It is muted
on purpose (less colour, a light scrim) so the lines, labels and risk colours drawn on top stay legible.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

from . import data as D

USGS = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
DIR = D.P / "imagery"
IMAGE, META = DIR / "basemap.jpg", DIR / "basemap.json"
ZOOM = 12  # about 30 m per pixel at LA's latitude: sharp enough at the map's closest view, about 170 tiles
TILE = 256
SCRIM = (233, 238, 243)  # the map's own background grey
MUTE_COLOUR, MUTE_SCRIM = 0.6, 0.4  # keep 60% of the colour, then wash 40% toward the scrim: green lines stay readable


def source() -> dict:
    url = os.environ.get("HIGHWIRE_BASEMAP_URL")
    if url:
        return {"name": os.environ.get("HIGHWIRE_BASEMAP_NAME", "custom imagery"), "url": url}
    return {"name": "USGS National Map", "url": USGS}


def _tile_xy(lat: float, lon: float, z: int) -> tuple[float, float]:
    """Fractional Web-Mercator tile coordinates of a point."""
    n = 2 ** z
    r = math.radians(lat)
    return (lon + 180.0) / 360.0 * n, (1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n


def _fetch(url: str) -> Image.Image:
    import requests

    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def build(zoom: int = ZOOM, fetch=_fetch) -> dict:
    """Download the tiles that cover the region, stitch them, resample to rows that are linear in latitude (the
    terrain mesh is), mute the result and cache it. Returns the metadata. `fetch` is replaceable for tests."""
    ter = D.terrain()  # the picture must cover exactly the terrain mesh it is drawn on
    box = {"lat_min": float(np.min(ter["lats"])), "lat_max": float(np.max(ter["lats"])),
           "lon_min": float(np.min(ter["lons"])), "lon_max": float(np.max(ter["lons"]))}
    lat0, lat1, lon0, lon1 = box["lat_min"], box["lat_max"], box["lon_min"], box["lon_max"]
    src = source()
    xa, ya = _tile_xy(lat1, lon0, zoom)  # north-west corner
    xb, yb = _tile_xy(lat0, lon1, zoom)  # south-east corner
    xs, ys = range(int(xa), int(xb) + 1), range(int(ya), int(yb) + 1)
    jobs = [(x, y) for y in ys for x in xs]
    with ThreadPoolExecutor(max_workers=6) as pool:
        tiles = list(pool.map(lambda xy: fetch(src["url"].format(z=zoom, x=xy[0], y=xy[1])), jobs))
    mosaic = Image.new("RGB", (len(xs) * TILE, len(ys) * TILE))
    for (x, y), tile in zip(jobs, tiles):
        mosaic.paste(tile.resize((TILE, TILE)) if tile.size != (TILE, TILE) else tile, ((x - xs[0]) * TILE, (y - ys[0]) * TILE))
    px = np.asarray(mosaic)
    left, right = round((xa - xs[0]) * TILE), round((xb - xs[0]) * TILE)
    top, bottom = (ya - ys[0]) * TILE, (yb - ys[0]) * TILE
    height = round(bottom - top)
    # Mercator rows stretch with latitude; the terrain is evenly spaced in latitude, so pick each output row's source row
    lats = lat1 - (np.arange(height) + 0.5) / height * (lat1 - lat0)
    rows = np.clip(np.round([(_tile_xy(float(la), lon0, zoom)[1] - ys[0]) * TILE for la in lats]).astype(int), 0, px.shape[0] - 1)
    img = Image.fromarray(px[rows][:, left:right])
    img = ImageEnhance.Color(img).enhance(MUTE_COLOUR)
    img = Image.blend(img, Image.new("RGB", img.size, SCRIM), MUTE_SCRIM)
    DIR.mkdir(parents=True, exist_ok=True)
    img.save(IMAGE, quality=86)
    meta = {"source": src["name"], "zoom": zoom, "tiles": len(jobs), "size": list(img.size), "bbox": box,
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    META.write_text(json.dumps(meta, indent=1))
    return meta


def meta() -> dict | None:
    """Metadata of the cached picture, or None when it has not been built."""
    if not (IMAGE.exists() and META.exists()):
        return None
    return json.loads(META.read_text())


def path() -> Path | None:
    return IMAGE if IMAGE.exists() else None


if __name__ == "__main__":
    m = build(int(sys.argv[1]) if len(sys.argv) > 1 else ZOOM)
    print(f"built {IMAGE} from {m['source']}: {m['tiles']} tiles at zoom {m['zoom']}, {m['size'][0]}x{m['size'][1]} px")
