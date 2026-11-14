"""Source 2: OpenStreetMap power network + critical sites via Overpass. Saves raw JSON to data/raw/osm/.

Towers (power=tower) are not downloaded separately: line geometry vertices already trace the tower positions,
and build_grid samples its own tower points every span. (See validation/DECISIONS.md.)
"""
from __future__ import annotations

import time

import requests

from common import RAW, UA, cached_json, log, region

L = log("osm")
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def overpass(q: str) -> dict:
    last = None
    for attempt in range(3):
        for ep in ENDPOINTS:
            try:
                r = requests.post(ep, data={"data": q}, headers=UA, timeout=300)
                if r.status_code == 200:
                    return r.json()
                last = f"{ep} HTTP {r.status_code} {r.text[:200]}"
            except requests.RequestException as e:
                last = f"{ep} {e}"
            L.warning("overpass retry: %s", last)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(last)


def main():
    b = region()["bbox"]
    bbox = f"{b['lat_min']},{b['lon_min']},{b['lat_max']},{b['lon_max']}"
    out = RAW / "osm"
    power = cached_json(out / "power.json", lambda: overpass(f"""
        [out:json][timeout:240];
        ( way["power"~"^(line|cable|minor_line)$"]({bbox});
          way["power"~"^(substation|plant)$"]({bbox});
          node["power"~"^(substation|plant)$"]({bbox});
          relation["power"~"^(substation|plant)$"]({bbox}); );
        out tags geom;"""))
    L.info("power elements: %d", len(power["elements"]))
    crit = cached_json(out / "critical.json", lambda: overpass(f"""
        [out:json][timeout:180];
        ( nwr["amenity"="hospital"]({bbox});
          nwr["man_made"~"^(wastewater_plant|water_works)$"]({bbox}); );
        out tags center;"""))
    L.info("critical-site elements: %d", len(crit["elements"]))


if __name__ == "__main__":
    main()
