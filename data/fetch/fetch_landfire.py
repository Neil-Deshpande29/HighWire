"""Source 14: LANDFIRE 2016 Remap (USGS) sampled under every tower: 40-class fire behavior fuel model (FBFM40),
existing vegetation height (EVH) and forest canopy cover (CC). 30 m pixels, keyless ArcGIS ImageServer getSamples.
The 2016 vintage predates every event we score (2018-2025), so no burn scar from a test fire leaks into the fuel map.
Each tower is sampled at the tower and 45 m either side of the line (the right-of-way edge and just beyond).
-> data/processed/landfire_towers.npz  (arrays aligned with LAGrid tower order)"""
from __future__ import annotations

import json
import sys
import time

import numpy as np

from common import PROCESSED, RAW, ROOT, cached_json, log

sys.path.insert(0, str(ROOT))
import requests  # noqa: E402

L = log("landfire")
BASE = "https://lfps.usgs.gov/arcgis/rest/services/Landfire_LF2016/LF2016_{}_CONUS/ImageServer/getSamples"
LAYERS = ["FBFM40", "EVH", "CC"]
OFFSET_M = 45.0
BATCH = 400


def sample(layer, pts, tag):
    out = []
    for i in range(0, len(pts), BATCH):
        part = pts[i:i + BATCH]
        f = RAW / "landfire" / f"{layer}_{tag}_{i}.json"

        def go(part=part):
            for attempt in range(5):
                try:
                    r = requests.post(BASE.format(layer), data={
                        "geometry": json.dumps({"points": [[round(float(x), 6), round(float(y), 6)] for x, y in part], "spatialReference": {"wkid": 4326}}),
                        "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true", "f": "json"},
                        headers={"User-Agent": "rewire-hackathon-research/0.1"}, timeout=120)
                    d = r.json()
                    if "samples" in d:
                        return d
                except Exception as e:  # noqa: BLE001
                    L.warning("%s batch %d attempt %d: %s", layer, i, attempt, e)
                time.sleep(3 * (attempt + 1))
            raise RuntimeError(f"LANDFIRE {layer} batch {i} failed")
        d = cached_json(f, go)
        v = np.full(len(part), -9999, dtype=np.int32)
        for s in d["samples"]:
            try:
                v[s["locationId"]] = int(float(s["value"]))
            except (ValueError, TypeError):
                pass
        out.append(v)
        if i % (BATCH * 10) == 0:
            L.info("%s %s %d/%d", layer, tag, i, len(pts))
    return np.concatenate(out)


def main():
    out = PROCESSED / "landfire_towers.npz"
    if out.exists():
        L.info("%s exists", out.name)
        return
    from backend.la import engine as E

    g = E.LAGrid()
    lat, lon, az = g.tlat, g.tlon, np.radians(g.taz)
    # unit vector perpendicular to the line (az = bearing of the line, degrees from north)
    dn, de = -np.sin(az), np.cos(az)
    dlat = OFFSET_M * dn / 110540.0
    dlon = OFFSET_M * de / (111320.0 * np.cos(np.radians(lat)))
    sets = {"c": (lon, lat), "l": (lon - dlon, lat - dlat), "r": (lon + dlon, lat + dlat)}
    res = {}
    for layer in LAYERS:
        for tag, (x, y) in sets.items():
            res[f"{layer}_{tag}"] = sample(layer, list(zip(x, y)), tag)
    np.savez_compressed(out, n_towers=len(lat), **res)
    L.info("saved %s (%d towers x 3 points x %d layers)", out.name, len(lat), len(LAYERS))


if __name__ == "__main__":
    main()
