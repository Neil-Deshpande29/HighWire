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
    monkeypatch.setattr(BM, "SURROUND", tmp_path / "surround.jpg")
    monkeypatch.setattr(BM, "SURROUND_META", tmp_path / "surround.json")


def test_default_source_is_public_and_another_provider_is_one_setting(monkeypatch):
    monkeypatch.delenv("REWIRE_BASEMAP_URL", raising=False)
    monkeypatch.delenv("VEXCEL_API_KEY", raising=False)
    assert BM.source() == {"name": "USGS National Map", "url": BM.USGS}
    monkeypatch.setenv("VEXCEL_API_KEY", "k")
    assert BM.source() == {"name": "Vexcel", "url": None}  # a Vexcel key alone is enough
    monkeypatch.setenv("REWIRE_BASEMAP_URL", "https://tiles.example/{z}/{x}/{y}?key=SECRET")
    monkeypatch.setenv("REWIRE_BASEMAP_NAME", "Vexcel")
    assert BM.source()["name"] == "Vexcel" and "{z}" in BM.source()["url"]


def test_build_stitches_the_region_and_saves_no_address_or_key(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    monkeypatch.setenv("REWIRE_BASEMAP_URL", "https://tiles.example/{z}/{x}/{y}?key=SECRET")
    monkeypatch.setenv("REWIRE_BASEMAP_NAME", "Vexcel")
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


def test_vexcel_picture_falls_back_where_the_key_has_no_coverage(monkeypatch, tmp_path):
    """Vexcel answers the southern areas, refuses the northern ones, and returns black for a strip: the public
    picture shows through in both, and neither the key nor an address is saved."""
    _use_tmp(monkeypatch, tmp_path)
    monkeypatch.delenv("REWIRE_BASEMAP_URL", raising=False)
    monkeypatch.setenv("VEXCEL_API_KEY", "SECRET-KEY")
    asked = []

    def ortho(wkt, width, height):
        import io

        from backend.la import vexcel as V

        south = min(float(p.split()[1]) for p in wkt.split("((")[1].split("))")[0].split(","))
        asked.append((width, height))
        if south > 34.15:
            raise V.NotCovered("outside")
        img = Image.new("RGB", (width, height), (200, 40, 40))
        img.paste((0, 0, 0), (0, 0, width, height // 4))  # the top quarter has no imagery
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        return buf.getvalue()

    meta = BM.build(zoom=9, fetch=lambda url: Image.new("RGB", (256, 256), (40, 40, 200)), ortho=ortho)
    assert meta["source"] == "Vexcel" and meta["fallback"] == "USGS National Map" and 0.3 < meta["coverage"] < 0.6
    assert meta["tiles"] == 8 and len(asked) == 12 and all(0.9 < h / w < 1.02 for w, h in asked)  # sized for Web Mercator at 34 N
    assert "SECRET-KEY" not in (tmp_path / "basemap.json").read_text()
    img = np.asarray(Image.open(BM.path())).astype(int)
    red_minus_blue = img[..., 0] - img[..., 2]
    assert red_minus_blue[-20].mean() > 20 and red_minus_blue[20].mean() < -20  # Vexcel in the south, the public picture in the north


def test_surroundings_reach_past_the_region_and_are_darker(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert BM.surround() is None
    meta = BM.build_surround(zoom=8, fetch=lambda url: Image.new("RGB", (256, 256), (200, 200, 200)))
    ter = D.terrain()
    assert meta["bbox"]["lat_min"] < float(np.min(ter["lats"])) and meta["bbox"]["lon_max"] > float(np.max(ter["lons"]))
    assert BM.surround() == meta and meta["source"] == "USGS National Map"
    assert np.asarray(Image.open(tmp_path / "surround.jpg")).mean() < 150  # pushed toward black so the region stands out
