"""Aerial picture under the map: stitching, and keeping a provider's key out of everything saved. Offline (fake tiles)."""
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.la import basemap as BM  # noqa: E402
from backend.la import data as D  # noqa: E402


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(BM, "DIR", tmp_path)
    monkeypatch.setattr(BM, "IMAGE", tmp_path / "basemap.jpg")
    monkeypatch.setattr(BM, "META", tmp_path / "basemap.json")


def test_default_source_is_public_and_another_provider_is_one_setting(monkeypatch):
    monkeypatch.delenv("HIGHWIRE_BASEMAP_URL", raising=False)
    assert BM.source() == {"name": "USGS National Map", "url": BM.USGS}
    monkeypatch.setenv("HIGHWIRE_BASEMAP_URL", "https://tiles.example/{z}/{x}/{y}?key=SECRET")
    monkeypatch.setenv("HIGHWIRE_BASEMAP_NAME", "Vexcel")
    assert BM.source()["name"] == "Vexcel" and "{z}" in BM.source()["url"]


def test_build_stitches_the_region_and_saves_no_address_or_key(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    monkeypatch.setenv("HIGHWIRE_BASEMAP_URL", "https://tiles.example/{z}/{x}/{y}?key=SECRET")
    monkeypatch.setenv("HIGHWIRE_BASEMAP_NAME", "Vexcel")
    asked = []

    def fake(url):  # each tile is one flat colour that encodes its column and row
        asked.append(url)
        z, x, y = (int(v) for v in url.split("?")[0].split("/")[-3:])
        return Image.new("RGB", (256, 256), (x % 256, y % 256, 128))

    meta = BM.build(zoom=9, fetch=fake)
    assert BM.meta() == meta and meta["source"] == "Vexcel" and meta["tiles"] == len(asked) > 0
    saved = (tmp_path / "basemap.json").read_text()
    assert "SECRET" not in saved and "tiles.example" not in saved  # the address can carry a key: never stored

    ter = D.terrain()
    assert meta["bbox"]["lat_min"] == float(np.min(ter["lats"])) and meta["bbox"]["lon_max"] == float(np.max(ter["lons"]))
    img = np.asarray(Image.open(BM.path()))
    assert list(img.shape[1::-1]) == meta["size"]
    # the region is 1.25 degrees wide and 0.75 tall at about 34 N, so about 1.38 times wider than tall on the ground
    assert 1.3 < img.shape[1] / img.shape[0] < 1.46 and img.std() > 1


def test_no_picture_until_built(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert BM.meta() is None and BM.path() is None
    (tmp_path / "basemap.json").write_text(json.dumps({"source": "x"}))
    assert BM.meta() is None  # metadata without the picture does not count
