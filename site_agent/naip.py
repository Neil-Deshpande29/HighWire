"""NAIP aerial photos for a line, from Microsoft Planetary Computer (keyless).

NAIP is the USDA's ~0.6 m aerial survey of the continental US. We search the public STAC catalogue for the newest
photo that covers a point, then ask Planetary Computer's data API for a JPEG crop of a small box around it.

Tiles are cached in data/processed/naip/<line>-<n>.jpg (+ .json with the photo id, date and box; n = 0..3, one per photo
point along the line, most fire-prone first), so the app and
the demo never wait on a download once `python -m site_agent.naip` has been run.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import requests

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
CROP = "https://planetarycomputer.microsoft.com/api/data/v1/item/crop/{minx},{miny},{maxx},{maxy}.jpg"
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "processed" / "naip"
RGB_BANDS = "image|1,2,3"
NIR_BANDS = "image|4,1,2"  # near-infrared, red, green: lets greenery.py tell vegetation from bare ground
HALF_KM = 0.6  # half-width of the box around the line's riskiest point: ~1.2 km across
SIZE_PX = 512
TIMEOUT_S = 60


def bbox(lat: float, lon: float, half_km: float = HALF_KM) -> tuple[float, float, float, float]:
    dlat = half_km / 110.54
    dlon = half_km / (111.32 * math.cos(math.radians(lat)))
    return (round(lon - dlon, 6), round(lat - dlat, 6), round(lon + dlon, 6), round(lat + dlat, 6))


def newest_item(lat: float, lon: float) -> dict | None:
    """Newest NAIP item covering the point, or None."""
    body = {"collections": ["naip"], "intersects": {"type": "Point", "coordinates": [lon, lat]}, "limit": 1,
            "sortby": [{"field": "datetime", "direction": "desc"}]}
    r = requests.post(STAC, json=body, timeout=TIMEOUT_S)
    r.raise_for_status()
    feats = r.json().get("features", [])
    if not feats:
        return None
    f = feats[0]
    return {"id": f["id"], "datetime": f["properties"].get("datetime"), "gsd_m": f["properties"].get("gsd")}


def crop_jpg(item_id: str, box: tuple[float, float, float, float], size: int = SIZE_PX, bands: str = RGB_BANDS) -> bytes:
    url = CROP.format(minx=box[0], miny=box[1], maxx=box[2], maxy=box[3])
    params = {"collection": "naip", "item": item_id, "assets": "image", "asset_bidx": bands, "max_size": size}
    r = requests.get(url, params=params, timeout=TIMEOUT_S)
    r.raise_for_status()
    if not r.headers.get("content-type", "").split(";")[0].strip() in ("image/jpeg", "image/jpg"):
        raise RuntimeError(f"NAIP crop returned {r.headers.get('content-type')}")
    return r.content


def _paths(line_id: str, n: int) -> tuple[Path, Path]:
    return CACHE / f"{line_id}-{n}.json", CACHE / f"{line_id}-{n}.jpg"


def _nir_path(line_id: str, n: int) -> Path:
    return CACHE / f"{line_id}-{n}.nir.jpg"


def _fetch_infrared(line_id: str, n: int, item_id: str, box) -> bool:
    """Save the infrared crop that matches photo n. Optional: without it greenery.py falls back to colour alone."""
    try:
        _nir_path(line_id, n).write_bytes(crop_jpg(item_id, tuple(box), bands=NIR_BANDS))
        return True
    except Exception:  # noqa: BLE001
        return False


def add_infrared(line_id: str) -> int:
    """Fetch the infrared crop for every cached photo of a line that lacks one. Returns how many were added."""
    added = 0
    for n in range(8):
        t = cached_tile(line_id, n)
        if t and "nir" not in t:
            added += _fetch_infrared(line_id, n, t["item"], t["bbox"])
    return added


def cached_tile(line_id: str, n: int, lat: float | None = None, lon: float | None = None) -> dict | None:
    """Cached photo n of a line, or None. With lat/lon, a tile centred elsewhere (the grid changed) counts as missing."""
    meta, img = _paths(line_id, n)
    if not (meta.exists() and img.exists()):
        return None
    m = json.loads(meta.read_text())
    if lat is not None and lon is not None and (abs(m["center"][0] - lat) > 1e-4 or abs(m["center"][1] - lon) > 1e-4):
        return None
    nir = _nir_path(line_id, n)
    return {**m, "img": str(img), **({"nir": str(nir)} if nir.exists() else {})}


def tile_for(line_id: str, n: int, lat: float, lon: float, refresh: bool = False) -> dict | None:
    """Photo n of a line, centred on (lat, lon); fetched on first use. Returns metadata incl. the image path, or None."""
    if not refresh and (c := cached_tile(line_id, n, lat, lon)):
        return c
    item = newest_item(lat, lon)
    if item is None:
        return None
    box = bbox(lat, lon)
    img = crop_jpg(item["id"], box)
    CACHE.mkdir(parents=True, exist_ok=True)
    meta_p, img_p = _paths(line_id, n)
    img_p.write_bytes(img)
    meta = {"line": line_id, "n": n, "item": item["id"], "photo_date": (item["datetime"] or "")[:10], "gsd_m": item["gsd_m"],
            "center": [lat, lon], "bbox": list(box), "source": "USDA NAIP via Microsoft Planetary Computer"}
    meta_p.write_text(json.dumps(meta, indent=1))
    has_nir = _fetch_infrared(line_id, n, item["id"], box)
    return {**meta, "img": str(img_p), **({"nir": str(_nir_path(line_id, n))} if has_nir else {})}


def tiles_for(line_id: str, points: list[tuple[float, float]], fetch: bool = True) -> tuple[list[dict | None], list[str]]:
    """One photo per point (cached, or fetched when `fetch`). Returns (tiles aligned with points, notes on failures)."""
    tiles, notes = [], []
    for n, (lat, lon) in enumerate(points):
        t = cached_tile(line_id, n, lat, lon)
        if t is None and fetch:
            try:
                t = tile_for(line_id, n, lat, lon)
            except Exception as e:  # noqa: BLE001  one missing photo is not fatal: facts still stand
                notes.append(f"photo {n + 1} unavailable: {str(e)[:100]}")
        tiles.append(t)
    return tiles, notes


def main(argv: list[str]) -> None:
    """Pre-fetch photos for every overhead line that crosses the fire alarm in any replay (or the ids given)."""
    sys.path.insert(0, str(ROOT))
    from site_agent.facts import alarm_lines, line_points

    ids = argv or alarm_lines()
    print(f"fetching NAIP photos for {len(ids)} lines (up to 4 each) -> {CACHE}")
    for k, lid in enumerate(ids, 1):
        tiles, notes = tiles_for(lid, line_points(lid))
        got = sum(t is not None for t in tiles)
        infrared = add_infrared(lid)
        print(f"[{k}/{len(ids)}] {lid}: {got}/{len(tiles)} photos" + (f", {infrared} infrared added" if infrared else "") + (f" ({notes[0]})" if notes else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
