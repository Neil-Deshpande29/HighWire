"""Source 8: NIFC WFIGS incident locations (point of origin + discovery time) and perimeters for the event fires.
-> data/processed/events/fires_wfigs.json. Each record keeps the exact query URL as source_url.

Known WFIGS data quirks handled here (see validation/DECISIONS.md):
 * several same-name fires exist (EATON 2022, HURST 2022-2024): we filter by discovery date within the event window;
 * WOOLSEY has two records; the CALAC one stores local time as if UTC (14:35Z = 06:35 PST, before the fire existed),
   so we take the record with the most plausible time (CAVNC, 22:30Z = 14:30 PST, matching CAL FIRE's 2:24 PM start).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

import yaml

from common import PROCESSED, ROOT, get, log, region

L = log("fires")
LOC = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations/FeatureServer/0/query"
PER = {
    2018: "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/InteragencyFirePerimeterHistory_2010s_Read_Only/FeatureServer/0/query",
    2019: "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/InteragencyFirePerimeterHistory_2010s_Read_Only/FeatureServer/0/query",
    2025: "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters/FeatureServer/0/query",
}
FIRES = {  # id: (WFIGS IncidentName, event id, preferred unique-id prefix or None)
    "woolsey_2018": ("WOOLSEY", "E1", "2018-CAVNC"),
    "saddleridge_2019": ("SADDLERIDGE", "E2", None),
    "eaton_2025": ("EATON", "E3", "2025-CALAC"),
    "hurst_2025": ("HURST", "E3", "2025-CALAC"),
}


def main():
    b = region()["bbox"]
    evs = {e["id"]: e for e in yaml.safe_load((ROOT / "validation/events.yaml").read_text())["events"]}
    out = {}
    for fid, (name, eid, pref) in FIRES.items():
        params = {"where": f"UPPER(IncidentName)='{name}'", "geometry": f"{b['lon_min']},{b['lat_min']},{b['lon_max']},{b['lat_max']}",
                  "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                  "outFields": "IncidentName,FireDiscoveryDateTime,InitialLatitude,InitialLongitude,FireCause,UniqueFireIdentifier,IncidentSize",
                  "returnGeometry": "true", "outSR": 4326, "f": "json"}
        url = LOC + "?" + urlencode(params)
        feats = get(LOC, params).json()["features"]
        s, e = datetime.fromisoformat(evs[eid]["start"]), datetime.fromisoformat(evs[eid]["end"])
        cands = []
        for f in feats:
            a = f["attributes"]
            if not a["FireDiscoveryDateTime"]:
                continue
            t = datetime.fromtimestamp(a["FireDiscoveryDateTime"] / 1000, tz=timezone.utc)
            if s <= t <= e:
                cands.append((0 if pref and a["UniqueFireIdentifier"].startswith(pref) else 1, t, f))
        if not cands:
            L.error("%s: no WFIGS record in window", fid)
            continue
        _, t, f = sorted(cands, key=lambda c: (c[0], c[1]))[0]
        a = f["attributes"]
        out[fid] = {"name": name.title(), "event": eid, "discovery_utc": t.isoformat(),
                    "lat": a["InitialLatitude"] or f["geometry"]["y"], "lon": a["InitialLongitude"] or f["geometry"]["x"],
                    "acres": a["IncidentSize"], "wfigs_id": a["UniqueFireIdentifier"], "wfigs_cause": a["FireCause"], "source_url": url}
        L.info("%s: %s at %.4f,%.4f (%s)", fid, t, out[fid]["lat"], out[fid]["lon"], a["UniqueFireIdentifier"])
        # perimeter (best effort)
        try:
            yr = t.year
            pq = {"where": f"UPPER(INCIDENT)='{name}'" if yr < 2020 else f"UPPER(poly_IncidentName)='{name}'",
                  "geometry": params["geometry"], "geometryType": "esriGeometryEnvelope", "inSR": 4326,
                  "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "true", "outSR": 4326,
                  "geometryPrecision": 4, "maxAllowableOffset": 0.002, "f": "geojson"}
            pj = get(PER[yr], pq).json()
            feats = [g for g in pj.get("features", []) if str(yr) in json.dumps(g["properties"])]
            if feats:
                out[fid]["perimeter"] = feats[0]["geometry"]
                out[fid]["perimeter_source_url"] = PER[yr] + "?" + urlencode(pq)
        except Exception as ex:  # noqa: BLE001
            L.warning("%s perimeter failed: %s", fid, ex)
    d = PROCESSED / "events"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fires_wfigs.json").write_text(json.dumps(out, indent=1))
    L.info("saved %d fires (%d with perimeters)", len(out), sum("perimeter" in v for v in out.values()))


if __name__ == "__main__":
    main()
