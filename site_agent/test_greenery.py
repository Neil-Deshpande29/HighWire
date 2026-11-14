"""Greenery near a line, measured from aerial photos. Offline: synthetic images in a temp folder, no network, no keys."""
import json
import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from site_agent import facts, greenery as G, imagery, naip  # noqa: E402

GREEN, DARK_BRUSH, GREY, SOIL, WATER, LAWN = (40, 150, 50), (70, 78, 55), (120, 120, 120), (170, 140, 105), (40, 70, 160), (120, 205, 110)


def solid(color, h=40, w=40):
    return np.full((h, w, 3), color, dtype=np.uint8)


def test_colour_rule_sees_plants_and_ignores_ground():
    seen = {name: bool(G.vegetation_mask(solid(c)).all()) for name, c in
            [("green", GREEN), ("brush", DARK_BRUSH), ("lawn", LAWN), ("road", GREY), ("soil", SOIL), ("water", WATER)]}
    assert seen == {"green": True, "brush": True, "lawn": True, "road": False, "soil": False, "water": False}


def test_infrared_rule_uses_nir_against_red():
    plants = np.dstack([np.full((8, 8), 180, np.uint8), np.full((8, 8), 70, np.uint8), np.full((8, 8), 60, np.uint8)])
    asphalt = np.dstack([np.full((8, 8), 90, np.uint8), np.full((8, 8), 95, np.uint8), np.full((8, 8), 90, np.uint8)])
    assert G.infrared_mask(plants).all() and not G.infrared_mask(asphalt).any()


def test_corridor_is_a_band_along_the_line():
    """A photo about 1.2 km across with the line running west to east through its middle: only a 200 m band counts."""
    lat0, lon0 = 34.10, -118.86
    box = (lon0, lat0, lon0 + 0.013, lat0 + 0.0108)
    mid = lat0 + 0.0054
    near = G.corridor_mask((120, 120), box, [[mid, lon0 - 0.01], [mid, lon0 + 0.03]])
    rows = np.where(near.any(axis=1))[0]
    assert near[:, 0].sum() == near[:, -1].sum() and 18 <= len(rows) <= 22  # 200 m of a 1.19 km tall photo is about a sixth
    assert abs((rows[0] + rows[-1]) / 2 - 59.5) <= 1  # centred on the line
    assert not G.corridor_mask((120, 120), box, [[lat0 + 0.5, lon0], [lat0 + 0.5, lon0 + 0.01]]).any()  # a line elsewhere


def test_levels():
    assert [G.level(x) for x in (0.0, 0.29, 0.30, 0.59, 0.60, 1.0)] == ["low", "low", "medium", "medium", "high", "high"]


@pytest.fixture
def photo_dir(tmp_path, monkeypatch):
    """A temp photo cache for line L220, laid out exactly as naip.py writes it."""
    monkeypatch.setattr(naip, "CACHE", tmp_path)
    monkeypatch.delenv("HIGHWIRE_IMAGERY", raising=False)
    G._measure.cache_clear()
    lat, lon = facts.branch("L220")["geometry"][len(facts.branch("L220")["geometry"]) // 2][:2]

    def add(n, rgb, nir=None):
        Image.fromarray(rgb).save(tmp_path / f"L220-{n}.jpg", quality=95)
        if nir is not None:
            Image.fromarray(nir).save(tmp_path / f"L220-{n}.nir.jpg", quality=95)
        (tmp_path / f"L220-{n}.json").write_text(json.dumps({"line": "L220", "n": n, "item": f"synthetic_{n}", "photo_date": f"2024-0{n + 1}-01",
                                                               "gsd_m": 0.6, "center": [lat, lon], "bbox": list(naip.bbox(lat, lon)), "source": "synthetic"}))
    yield add
    G._measure.cache_clear()


def test_a_line_with_no_photos_has_no_measure(photo_dir):
    assert G.line_greenery("L220") is None


def test_line_greenery_from_colour_photos(photo_dir):
    photo_dir(0, solid(GREEN, 100, 100))
    photo_dir(1, solid(GREY, 100, 100))
    g = G.line_greenery("L220")
    assert g["photos"] == 2 and g["method"] == "colour" and g["source"] == "synthetic" and g["photo_date"] == "2024-02-01"
    assert 0.3 < g["share"] < 0.7 and g["level"] == "medium" and g["corridor_m"] == 100.0  # one photo all plants, one all road


def test_infrared_wins_when_the_provider_has_it(photo_dir):
    """Brown brush looks like bare soil in colour. The infrared crop shows it is alive."""
    brush_nir = np.dstack([np.full((100, 100), 170, np.uint8), np.full((100, 100), 110, np.uint8), np.full((100, 100), 90, np.uint8)])
    photo_dir(0, solid(SOIL, 100, 100))
    assert G.line_greenery("L220")["level"] == "low"
    photo_dir(0, solid(SOIL, 100, 100), nir=brush_nir)
    g = G.line_greenery("L220")  # the new file changes the cache stamp, so it is measured again
    assert g["method"] == "infrared" and g["share"] > 0.95 and g["level"] == "high"


def test_imagery_switch_names_its_providers(monkeypatch):
    assert imagery.provider() is naip
    monkeypatch.setenv("HIGHWIRE_IMAGERY", "vexcel")
    with pytest.raises(KeyError, match="vexcel"):
        imagery.provider()  # not registered until its key and docs exist; fail loudly, never fall back silently


def test_an_unreadable_photo_is_skipped_not_fatal(photo_dir, tmp_path):
    photo_dir(0, solid(GREEN, 100, 100))
    (tmp_path / "L220-0.jpg").write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    assert G.line_greenery("L220") is None
