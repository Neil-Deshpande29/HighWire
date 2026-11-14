"""Source 3 (optional): HIFLD Transmission Lines, archived copy (HIFLD Open shut down Aug 2025).
Tries the ArcGIS Hub mirror and source.coop; on failure logs and exits 0 (build_grid then uses OSM only)."""
from __future__ import annotations

import json

from common import PROCESSED, get, log, region

L = log("hifld")
CANDIDATES = [
    # Esri/Hub mirrors of the last public HIFLD layer (may disappear; best effort)
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Electric_Power_Transmission_Lines/FeatureServer/0/query",
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/US_Electric_Power_Transmission_Lines/FeatureServer/0/query",
]


def main():
    b = region()["bbox"]
    params = {"where": "1=1", "geometry": f"{b['lon_min']},{b['lat_min']},{b['lon_max']},{b['lat_max']}",
              "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
              "outFields": "VOLTAGE,OWNER,SUB_1,SUB_2,TYPE", "returnGeometry": "true", "outSR": 4326, "f": "geojson",
              "resultRecordCount": 2000}
    for url in CANDIDATES:
        try:
            d = get(url, params, retries=1, timeout=90).json()
            if d.get("features"):
                (PROCESSED / f"hifld_lines_{region()['name']}.geojson").write_text(json.dumps(d))
                L.info("HIFLD archive: %d lines from %s", len(d["features"]), url)
                return
        except Exception as e:  # noqa: BLE001
            L.warning("HIFLD source failed (%s): %s", url, e)
    L.warning("No HIFLD archive reachable; continuing with OSM only (see DECISIONS.md).")


if __name__ == "__main__":
    main()
