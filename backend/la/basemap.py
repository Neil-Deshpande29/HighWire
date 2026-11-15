"""Aerial imagery under the map: one stitched picture of the region, drawn on the terrain when the layer is on.

Three sources, picked in this order, then `make imagery` builds the picture:
  1. REWIRE_BASEMAP_URL (+ REWIRE_BASEMAP_NAME) in .env: any XYZ tile address; it may contain a key.
  2. VEXCEL_API_KEY in .env: Vexcel aerial imagery through its API (backend/la/vexcel.py). Where the key has no
     coverage (the hackathon key stops near 34.32 N) the public USGS picture shows through, and the metadata says so.
  3. Neither: the USGS National Map imagery service (public domain, no key).
Addresses and keys are read from the environment only: never written to the cache, the metadata or a log.

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
from . import vexcel

USGS = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
DIR = D.P / "imagery"
IMAGE, META = DIR / "basemap.jpg", DIR / "basemap.json"
ZOOM = 12  # about 30 m per pixel at LA's latitude: sharp enough at the map's closest view, about 170 tiles
TILE = 256
SCRIM = (233, 238, 243)  # the map's own background grey
MUTE_COLOUR, MUTE_SCRIM = 0.6, 0.4  # keep 60% of the colour, then wash 40% toward the scrim: green lines stay readable
VEXCEL_GRID = (4, 3)  # requests across and down that cover the region
VEXCEL_CELL_PX = 1024  # width asked for per request: about 28 m per pixel, like the USGS picture
VEXCEL_SIZE = (3648, 2640)  # the finished picture
# The land and sea around the region, drawn flat and darker under the map so it does not float in empty space.
# Always the public USGS picture: it reaches past any provider's coverage, and nothing is measured from it.
SURROUND, SURROUND_META = DIR / "surround.jpg", DIR / "surround.json"
SURROUND_PAD_LAT, SURROUND_PAD_LON = 0.55, 0.9  # degrees added on each side of the region
SURROUND_ZOOM = 10
SURROUND_DARK = 0.3  # how far the surroundings are pushed toward black, so the region itself stands out


def source() -> dict:
    url = os.environ.get("REWIRE_BASEMAP_URL") or os.environ.get("HIGHWIRE_BASEMAP_URL")  # old name still read
    if url:
        return {"name": os.environ.get("REWIRE_BASEMAP_NAME") or os.environ.get("HIGHWIRE_BASEMAP_NAME") or "custom imagery", "url": url}
    if vexcel.key():
        return {"name": vexcel.NAME, "url": None}  # read through the Vexcel API, not a tile address
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


def _region() -> dict:
    ter = D.terrain()  # the picture must cover exactly the terrain mesh it is drawn on
    return {"lat_min": float(np.min(ter["lats"])), "lat_max": float(np.max(ter["lats"])),
            "lon_min": float(np.min(ter["lons"])), "lon_max": float(np.max(ter["lons"]))}


def _tiles_picture(url: str, zoom: int, fetch, box: dict) -> tuple[Image.Image, int]:
    """Download the XYZ tiles that cover the region, stitch them and resample to rows that are linear in latitude
    (the terrain mesh is). Returns the picture and how many tiles it took."""
    lat0, lat1, lon0, lon1 = box["lat_min"], box["lat_max"], box["lon_min"], box["lon_max"]
    xa, ya = _tile_xy(lat1, lon0, zoom)  # north-west corner
    xb, yb = _tile_xy(lat0, lon1, zoom)  # south-east corner
    xs, ys = range(int(xa), int(xb) + 1), range(int(ya), int(yb) + 1)
    jobs = [(x, y) for y in ys for x in xs]
    with ThreadPoolExecutor(max_workers=6) as pool:
        tiles = list(pool.map(lambda xy: fetch(url.format(z=zoom, x=xy[0], y=xy[1])), jobs))
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
    return Image.fromarray(px[rows][:, left:right]), len(jobs)


def _merc(lat) -> np.ndarray:
    return np.arcsinh(np.tan(np.radians(lat)))


def _vexcel_picture(box: dict, ortho) -> tuple[np.ndarray, np.ndarray, int]:
    """The region from Vexcel, asked for as a grid of areas and resampled to rows that are linear in latitude.
    Returns the picture, a mask of where Vexcel had imagery, and the number of requests answered."""
    from scipy import ndimage

    cols, rows = VEXCEL_GRID
    width, height = VEXCEL_SIZE
    dlon, dlat = (box["lon_max"] - box["lon_min"]) / cols, (box["lat_max"] - box["lat_min"]) / rows
    cells = [(r, c) for r in range(rows) for c in range(cols)]

    def one(rc):
        r, c = rc
        north, west = box["lat_max"] - r * dlat, box["lon_min"] + c * dlon
        south, east = north - dlat, west + dlon
        ask_h = round(VEXCEL_CELL_PX * float(_merc(north) - _merc(south)) / np.radians(dlon))  # the picture comes back in Web Mercator
        try:
            return Image.open(io.BytesIO(ortho(vexcel.rect_wkt(south, west, north, east), VEXCEL_CELL_PX, ask_h))).convert("RGB")
        except vexcel.NotCovered:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        got = list(pool.map(one, cells))
    out, have = np.zeros((height, width, 3), np.uint8), np.zeros((height, width), bool)
    for (r, c), img in zip(cells, got):
        if img is None:
            continue
        y0, y1, x0, x1 = round(r * height / rows), round((r + 1) * height / rows), round(c * width / cols), round((c + 1) * width / cols)
        north = box["lat_max"] - r * dlat
        px = np.asarray(img)
        lats = north - (np.arange(y1 - y0) + 0.5) / (y1 - y0) * dlat
        src = np.clip(((_merc(north) - _merc(lats)) / (_merc(north) - _merc(north - dlat)) * px.shape[0]).astype(int), 0, px.shape[0] - 1)
        cell = np.asarray(Image.fromarray(px[src]).resize((x1 - x0, y1 - y0), Image.BILINEAR))
        out[y0:y1, x0:x1] = cell
        # outside its coverage Vexcel returns solid black. Large black areas are "no imagery" (small ones are shadows),
        # grown by a few pixels so the dark edge of the coverage does not show as a line
        dark = cell.max(axis=2) < 10
        labels, n_areas = ndimage.label(dark)
        if n_areas:
            sizes = ndimage.sum(dark, labels, index=np.arange(1, n_areas + 1))
            missing = ndimage.binary_dilation(np.isin(labels, np.flatnonzero(sizes >= 4000) + 1), iterations=5)
        else:
            missing = np.zeros_like(dark)
        have[y0:y1, x0:x1] = ~missing
    return out, have, sum(g is not None for g in got)


def build(zoom: int = ZOOM, fetch=_fetch, ortho=vexcel.ortho) -> dict:
    """Build the region's picture from the configured source, mute it and cache it. Returns the metadata.
    `fetch` (one XYZ tile) and `ortho` (one Vexcel area) are replaceable for tests."""
    box = _region()
    src = source()
    extra = {}
    if src["url"]:
        img, n = _tiles_picture(src["url"], zoom, fetch, box)
    else:  # Vexcel, with the public picture wherever Vexcel has none
        px, have, n = _vexcel_picture(box, ortho)
        extra["coverage"] = round(float(have.mean()), 3)
        if not have.all():
            try:
                under = np.asarray(_tiles_picture(USGS, zoom, fetch, box)[0].resize(VEXCEL_SIZE, Image.BILINEAR))
                extra["fallback"] = "USGS National Map"
            except Exception:  # noqa: BLE001  offline: leave the uncovered part as plain map grey
                under = np.full_like(px, SCRIM)
                extra["fallback"] = None
            px = np.where(have[..., None], px, under)
        img = Image.fromarray(px)
    img = ImageEnhance.Color(img).enhance(MUTE_COLOUR)
    img = Image.blend(img, Image.new("RGB", img.size, SCRIM), MUTE_SCRIM)
    img = fill_gaps(img, INNER_SEA, _blank())  # open sea has no tiles: one sea colour, no flat grey stair-steps
    DIR.mkdir(parents=True, exist_ok=True)
    img.save(IMAGE, quality=86)
    meta = {"source": src["name"], "zoom": zoom, "tiles": n, "size": list(img.size), "bbox": box, **extra,
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    META.write_text(json.dumps(meta, indent=1))
    return meta


SEA = (15, 25, 36)  # what the surroundings show where there is no picture: open sea, in the tone of the darkened land
GAP_BELOW = 8  # a pixel darker than this on every channel has no tile behind it
GAP_GROW_PX = 18  # a gap is widened by this much, to swallow the smeared pixels along a missing tile's edge
GAP_FEATHER_PX = 9  # and its edge is blurred this far, so no stair-step of tile borders is left


INNER_SEA = (34, 50, 66)  # the same sea inside the region, in the lighter tone of the region's own picture


def fill_gaps(img: Image.Image, colour: tuple = SEA, blank: tuple | None = None) -> Image.Image:
    """The tile service has no pictures of open sea, and the odd hole on land. Left alone they show as hard flat
    stair-steps with smeared edges. Large gaps (the sea) become one sea colour with a soft edge; small ones (a hole on
    land) take the colour of the ground around them. A gap is near-black, or `blank` when the picture was muted after
    the tiles were laid (a missing tile is then that one flat colour)."""
    from scipy import ndimage

    a = np.asarray(img.convert("RGB")).astype(np.float32)
    gap = a.max(axis=2) < GAP_BELOW if blank is None else (np.abs(a - np.array(blank, np.float32)).max(axis=2) <= 3)
    if not gap.any():
        return img
    labels, n = ndimage.label(gap)
    sizes = ndimage.sum(gap, labels, range(1, n + 1))
    sea = np.zeros(gap.shape, bool)
    out = a.copy()
    holes = ndimage.find_objects(labels)
    for k, size in enumerate(sizes, 1):
        if size >= 0.01 * gap.size:
            sea |= labels == k
            continue
        if blank is not None or size < 400:
            continue  # a speck (or, in a muted picture, ground that happens to be that grey): leave it
        ys, xs = holes[k - 1]  # work in the hole's own neighbourhood, not over the whole picture
        box = (slice(max(ys.start - 8, 0), ys.stop + 8), slice(max(xs.start - 8, 0), xs.stop + 8))
        hole = labels[box] == k
        ring = ndimage.binary_dilation(hole, iterations=6) & ~gap[box]
        if ring.any():
            out[box][hole] = np.median(a[box][ring], axis=0)
    if sea.any():
        grown = ndimage.binary_dilation(sea, iterations=GAP_GROW_PX)
        w = ndimage.gaussian_filter(grown.astype(np.float32), GAP_FEATHER_PX)[..., None]
        out = out * (1 - w) + np.array(colour, np.float32) * w
    return Image.fromarray(out.clip(0, 255).astype(np.uint8))


def _blank() -> tuple:
    """What a missing tile looks like in the region's picture: black, after the muting scrim is laid over it."""
    return Image.blend(Image.new("RGB", (1, 1), (0, 0, 0)), Image.new("RGB", (1, 1), SCRIM), MUTE_SCRIM).getpixel((0, 0))


def repair_picture() -> dict | None:
    """Fill the sea in the region's own picture that is already on disk (no download)."""
    if not IMAGE.exists() or not META.exists():
        return None
    fill_gaps(Image.open(IMAGE), INNER_SEA, _blank()).save(IMAGE, quality=86)
    m = json.loads(META.read_text())
    m["built_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    m["gaps_filled"] = True
    META.write_text(json.dumps(m, indent=1))
    return m


def repair_surround() -> dict | None:
    """Fill the gaps in the surroundings picture that is already on disk (no download), and mark it as rebuilt so the
    screen fetches it again."""
    if not SURROUND.exists() or not SURROUND_META.exists():
        return None
    fill_gaps(Image.open(SURROUND)).save(SURROUND, quality=86)
    m = json.loads(SURROUND_META.read_text())
    m["built_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    m["gaps_filled"] = True
    SURROUND_META.write_text(json.dumps(m, indent=1))
    return m


def build_surround(zoom: int = SURROUND_ZOOM, fetch=_fetch) -> dict:
    """Build the wider picture around the region and cache it. Returns its metadata (the box it covers)."""
    box = _region()
    wide = {"lat_min": box["lat_min"] - SURROUND_PAD_LAT, "lat_max": box["lat_max"] + SURROUND_PAD_LAT,
            "lon_min": box["lon_min"] - SURROUND_PAD_LON, "lon_max": box["lon_max"] + SURROUND_PAD_LON}
    img, n = _tiles_picture(USGS, zoom, fetch, wide)
    img = ImageEnhance.Color(img).enhance(MUTE_COLOUR)
    img = Image.blend(img, Image.new("RGB", img.size, (10, 13, 17)), SURROUND_DARK)
    img = fill_gaps(img)  # open sea has no tiles: no black stair-steps
    DIR.mkdir(parents=True, exist_ok=True)
    img.save(SURROUND, quality=82)
    out = {"source": "USGS National Map", "bbox": wide, "size": list(img.size), "tiles": n,
           "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    SURROUND_META.write_text(json.dumps(out, indent=1))
    return out


def surround() -> dict | None:
    """Metadata of the cached surroundings, or None when they have not been built."""
    if not (SURROUND.exists() and SURROUND_META.exists()):
        return None
    return json.loads(SURROUND_META.read_text())


def meta() -> dict | None:
    """Metadata of the cached picture, or None when it has not been built."""
    if not (IMAGE.exists() and META.exists()):
        return None
    return json.loads(META.read_text())


def path() -> Path | None:
    return IMAGE if IMAGE.exists() else None


if __name__ == "__main__":
    m = build(int(sys.argv[1]) if len(sys.argv) > 1 else ZOOM)
    print(f"built {IMAGE} from {m['source']}: {m['tiles']} requests, {m['size'][0]}x{m['size'][1]} px"
          + (f", {m['coverage']:.0%} covered" + (f", {m['fallback']} elsewhere" if m.get("fallback") else "") if "coverage" in m else ""))
    w = build_surround()
    print(f"built {SURROUND}: the surroundings, {w['tiles']} tiles, {w['size'][0]}x{w['size'][1]} px")
