"""Source 10: NWS Red Flag Warnings (FW.W) from the IEM VTEC archive for our WFO, plus the fire-zone polygons
as they were at warning time (IEM geojson per event; api.weather.gov 404s on retired zone codes). Output: data/processed/redflag_<region>.json  {events:[{issue,expire,ugc}], zones:{ugc: geojson}}
"""
from __future__ import annotations

import json

from common import PROCESSED, RAW, cached_json, get, log, region

L = log("redflag")
YEARS = range(2018, 2026)


def main():
    reg = region()
    wfo = reg["nws_wfo"]
    rows = []
    for y in YEARS:
        d = cached_json(RAW / "redflag" / f"{wfo}_{y}.json", lambda y=y: get(
            "https://mesonet.agron.iastate.edu/json/vtec_events_bywfo.py",
            {"wfo": wfo, "year": y, "phenomena": "FW", "significance": "W"}).json())
        for e in d.get("events", []):
            if e["phenomena"] == "FW" and e["significance"] == "W":
                rows.append({"issue": e["issue"], "expire": e["expire"], "ugc": e["ugc"], "eventid": e["eventid"], "year": y,
                             "source_url": e["url"]})
    zones = {}
    for y, etn in sorted({(r["year"], r["eventid"]) for r in rows}):
        try:
            g = cached_json(RAW / "redflag" / f"geo_{wfo}_{y}_{etn}.json", lambda y=y, etn=etn: get(
                "https://mesonet.agron.iastate.edu/geojson/vtec_event.py",
                {"wfo": wfo, "year": y, "phenomena": "FW", "significance": "W", "etn": etn}, retries=2, timeout=60).json())
            for f in g.get("features", []):
                zones.setdefault(f["id"], {"geometry": f["geometry"]})
        except Exception as e:  # noqa: BLE001
            L.error("event %s/%s geometry failed: %s", y, etn, e)
    out = PROCESSED / f"redflag_{reg['name']}.json"
    out.write_text(json.dumps({"events": rows, "zones": zones}))
    L.info("%d zone-warnings over %d zones -> %s", len(rows), len(zones), out.name)


if __name__ == "__main__":
    main()
