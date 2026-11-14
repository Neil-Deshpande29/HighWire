"""Greenery near a line, measured from its aerial photos.

For each cached photo along a line, count the share of ground within CORRIDOR_M of the conductors that is covered by
vegetation (trees, brush, grass). A line over the fire alarm with vegetation close to the wires is the one to act on
first, so the share ranks lines for the operator and the planner.

Two ways to see vegetation, best first:
  * infrared: plants reflect near-infrared and absorb red, so (NIR - red) / (NIR + red) separates them from soil,
    roads and roofs, including the dry olive-brown brush that covers LA's hills. Used when the imagery provider has
    an infrared crop for the photo (USDA NAIP does).
  * colour: for RGB-only imagery. "Dark and at least as green as it is red and blue, or clearly green." Tuned on eight
    LA photos to agree with the infrared index: 82% of pixels match, but dark roofs and asphalt in built-up areas read
    as vegetation, so treat it as approximate.

What this is not: it ranks lines and never changes the calibrated fire index (the validation tests that index). Photos
cover a line's most fire-prone stretches (up to four, about 1.2 km each), not its whole length, and they show the day
they were flown, not today.
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np
from PIL import Image

from . import facts, imagery

CORRIDOR_M = 100.0  # look this far either side of the conductors
MEDIUM, HIGH = 0.30, 0.60  # share of the corridor that is vegetated; a judgement, set on LA wildland and suburban photos
NDVI_MIN = 0.10  # infrared index above which a pixel is vegetation (checked by eye on chaparral, roads and cleared lots)
# colour rule: excess green (2g - r - b, over brightness) at or above zero on dark ground, or clearly green anywhere
EXG_DARK, EXG_STRONG, DARK_BELOW = 0.0, 0.10, 380.0


def infrared_mask(nir_red: np.ndarray) -> np.ndarray:
    """Vegetation from an infrared crop whose first two channels are near-infrared and red (uint8, H x W x >=2)."""
    nir, red = nir_red[..., 0].astype(np.float32), nir_red[..., 1].astype(np.float32)
    return (nir - red) / np.maximum(nir + red, 1.0) > NDVI_MIN


def vegetation_mask(rgb: np.ndarray) -> np.ndarray:
    """Vegetation from colour alone (uint8, H x W x 3). Approximate; see the module docstring."""
    px = rgb[..., :3].astype(np.float32)
    total = px.sum(axis=-1)
    exg = (2.0 * px[..., 1] - px[..., 0] - px[..., 2]) / np.maximum(total, 1.0)
    return ((exg > EXG_DARK) & (total < DARK_BELOW)) | (exg > EXG_STRONG)


def corridor_mask(shape: tuple[int, int], bbox, path: list, half_width_m: float = CORRIDOR_M) -> np.ndarray:
    """Boolean H x W mask of pixels within half_width_m of the polyline `path` ([lat, lon] vertices).
    bbox is (min lon, min lat, max lon, max lat) of the image; row 0 is the north edge."""
    h, w = shape
    lon0, lat0, lon1, lat1 = bbox
    kx = 111_320.0 * np.cos(np.radians((lat0 + lat1) / 2.0))  # metres per degree of longitude here
    ky = 110_540.0
    span_x, span_y = (lon1 - lon0) * kx, (lat1 - lat0) * ky
    X, Y = np.meshgrid((np.arange(w) + 0.5) / w * span_x, (1.0 - (np.arange(h) + 0.5) / h) * span_y)
    pts = [((lon - lon0) * kx, (lat - lat0) * ky) for lat, lon in (p[:2] for p in path)]
    near = np.zeros(shape, dtype=bool)
    for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
        if max(ax, bx) < -half_width_m or min(ax, bx) > span_x + half_width_m or max(ay, by) < -half_width_m or min(ay, by) > span_y + half_width_m:
            continue  # this span is nowhere near the photo
        dx, dy = bx - ax, by - ay
        t = np.clip(((X - ax) * dx + (Y - ay) * dy) / (dx * dx + dy * dy or 1.0), 0.0, 1.0)
        near |= np.hypot(X - (ax + t * dx), Y - (ay + t * dy)) <= half_width_m
    return near


def level(share: float) -> str:
    return "high" if share >= HIGH else "medium" if share >= MEDIUM else "low"


def tile_greenery(tile: dict, path: list) -> dict | None:
    """Vegetated share of the corridor in one photo and how it was measured, or None if the line does not cross it."""
    if tile.get("nir"):
        veg, method = infrared_mask(np.asarray(Image.open(tile["nir"]).convert("RGB"))), "infrared"
    else:
        veg, method = vegetation_mask(np.asarray(Image.open(tile["img"]).convert("RGB"))), "colour"
    near = corridor_mask(veg.shape, tile["bbox"], path)
    n = int(near.sum())
    if n == 0:
        return None
    return {"share": float((veg & near).sum() / n), "pixels": n, "method": method}


@lru_cache(maxsize=2048)
def _measure(line_id: str, stamp: tuple) -> dict | None:
    path = facts.branch(line_id)["geometry"]
    parts = [(t, g) for t in imagery.cached_tiles(line_id) if (g := tile_greenery(t, path))]
    if not parts:
        return None
    pixels = sum(g["pixels"] for _, g in parts)
    share = round(sum(g["share"] * g["pixels"] for _, g in parts) / pixels, 2)  # two places: the level must match the figure shown
    methods = {g["method"] for _, g in parts}
    return {"share": share, "level": level(share), "photos": len(parts), "corridor_m": CORRIDOR_M,
            "method": methods.pop() if len(methods) == 1 else "mixed",
            "source": parts[0][0].get("source", "aerial imagery"), "photo_date": max((t.get("photo_date") or "") for t, _ in parts) or None}


def line_greenery(line_id: str) -> dict | None:
    """Greenery near a line from the photos already on disk, or None when it has no photos yet. Never fetches:
    it is called for every risky line on every screen refresh. Re-measures when a photo file changes or appears."""
    stamp = tuple((p, os.path.getmtime(p)) for t in imagery.cached_tiles(line_id) for p in (t["img"], t.get("nir")) if p)
    return _measure(line_id, stamp) if stamp else None
