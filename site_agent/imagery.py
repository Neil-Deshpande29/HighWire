"""Which aerial imagery the site agent and the greenery measure read.

One place to switch provider. Every provider module offers the interface of naip.py: tile_for, tiles_for, cached_tile,
and tile dicts with img, bbox, center, photo_date, gsd_m, source. Set REWIRE_IMAGERY to pick one; the default is the
keyless USDA NAIP archive. A Vexcel module registers here once its API key and docs are available.
"""
from __future__ import annotations

import os

from . import naip

PROVIDERS = {"naip": naip}
MAX_PHOTOS = 8


def provider():
    name = (os.environ.get("REWIRE_IMAGERY") or os.environ.get("HIGHWIRE_IMAGERY") or "naip").lower()  # old name still read
    if name not in PROVIDERS:
        raise KeyError(f"unknown imagery provider {name!r}; configured: {', '.join(sorted(PROVIDERS))}")
    return PROVIDERS[name]


def cached_tiles(line_id: str) -> list[dict]:
    """The photos of a line that are already on disk, in order. Never touches the network."""
    p = provider()
    return [t for n in range(MAX_PHOTOS) if (t := p.cached_tile(line_id, n))]
