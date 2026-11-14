"""Vexcel API 2.0 client: aerial imagery and object detection for the hackathon's Los Angeles coverage.

Only the calls the app uses, with the request shapes from Vexcel's own hackathon quickstart (vexcel_quickstart.py):
    POST ortho/extract     an aerial picture of any WKT area, as JPEG bytes
    POST elements/detect   open-vocabulary detection (for example "tree") on that picture, as boxes in map coordinates
The key is read from VEXCEL_API_KEY (in .env, which is git-ignored) and sent in the X-API-KEY header. It is never
written to a cache, a log or a response. Imagery and anything derived from it is licensed: cache it only under
data/processed/vexcel/ or data/processed/imagery/, both git-ignored.

A 403 means the area is outside what the key covers (the hackathon key stops near 34.32 N) and a 404 that there is
no imagery for it; both are reported as NotCovered.
"""
from __future__ import annotations

import math
import os
import random
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:  # python-dotenv is in requirements; keep the module importable without it
    pass

API = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"  # let the API pick the best imagery available at each place
MAX_RETRIES = 4
NAME = "Vexcel"


class NotCovered(Exception):
    """The area is outside the region this key may read."""


def key() -> str | None:
    return os.environ.get("VEXCEL_API_KEY") or None


def rect_wkt(south: float, west: float, north: float, east: float) -> str:
    """WKT polygon for a lat/lon box. WKT is (longitude latitude)."""
    return f"POLYGON(({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))"


def square_wkt(lat: float, lon: float, half_m: float) -> str:
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * math.cos(math.radians(lat)))
    return rect_wkt(lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def _post(path: str, body: dict, timeout: int = 300):
    import requests

    k = key()
    if not k:
        raise RuntimeError("VEXCEL_API_KEY is not set (put it in .env)")
    for attempt in range(MAX_RETRIES + 1):
        r = requests.post(f"{API}/{path}", headers={"X-API-KEY": k}, json=body, timeout=timeout)
        if r.status_code != 429 or attempt == MAX_RETRIES:
            break
        time.sleep(min(2 ** attempt, 30) + random.random())  # rate limited: back off as the quickstart does
    if r.status_code in (403, 404):  # 403: outside the key's area; 404: no imagery for this area
        raise NotCovered("Vexcel has no imagery here for this key")
    if not r.ok:
        raise RuntimeError(f"Vexcel {path} failed ({r.status_code}): {r.text[:200]}")
    return r


def ortho(wkt: str, width: int | None = None, height: int | None = None) -> bytes:
    """JPEG of the area's bounding box, north up, in Web Mercator. With a size, the picture is fitted to it."""
    body = {"wkt": wkt, "srid": 4326, "layer": LAYERS, "crop": "bounding_box", "image-format": "jpeg"}
    if width and height:
        body["destination-dimensions"] = f"{width},{height}"
    return _post("ortho/extract", body).content


def detect(wkt: str, label: str, threshold: float = 0.5) -> list[dict]:
    """Things matching `label` inside the area, found on its aerial picture: [{confidence, lat, lon}] at each box's centre."""
    body = {
        "wkt": wkt, "srid": 4326, "buffer-meter": 0,
        "images": [{"endpoint": "ortho/extract", "request": {"layer": LAYERS, "srid": 4326, "wkt": wkt, "crop": "bounding_box"}}],
        "model-input": {"elements": [{"label": label, "model": "sam3", "detection-threshold": threshold}], "include": ["bbox", "confidence"]},
    }
    out = []
    for feat in _post("elements/detect", body).json().get("features", []):
        for d in feat.get("properties", {}).get("detections", []):
            pts = [tuple(map(float, p.split())) for p in d["bbox"].split("((")[1].split("))")[0].split(",")][:4]
            out.append({"confidence": round(float(d["confidence"]), 2), "lon": sum(p[0] for p in pts) / len(pts), "lat": sum(p[1] for p in pts) / len(pts)})
    return out
