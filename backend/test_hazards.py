"""Hazards for the map: what the hour's numbers show, grouped into places, and what the public warning services say.
Offline: the warning services are a fake fetcher here. Also the fill for gaps in the map pictures."""
import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import routes_la as R  # noqa: E402
from backend.la import basemap as BM  # noqa: E402
from backend.la import data as D  # noqa: E402
from backend.la import hazards as H  # noqa: E402
from backend.la import replay as RP  # noqa: E402

CALM, STORM = "2018-11-08T08:00:00+00:00", "2018-11-08T19:00:00+00:00"  # Woolsey day, UTC


def test_trouble_is_grouped_into_places_worst_first():
    hz = H.detected("E1", STORM)
    assert 1 <= len(hz) <= H.MAX_HAZARDS and {z["kind"] for z in hz} >= {"fire", "storm"}
    assert [z["level"] != "act" for z in hz] == sorted(z["level"] != "act" for z in hz)  # act before watch
    fire = [z for z in hz if z["kind"] == "fire" and z["level"] == "act"]
    box = D.grid()["bbox"]
    for z in hz:
        assert box["lat_min"] <= z["lat"] <= box["lat_max"] and box["lon_min"] <= z["lon"] <= box["lon_max"]
        assert 3.0 <= z["radius_km"] <= 14.0 and z["n_lines"] >= len(z["lines"]) >= 1 and 0 <= z["wind_from_deg"] <= 360
        assert z["title"] and z["detail"].endswith(".")
    # every line in a fire hazard really is over its alarm this hour, and no line is in two fire hazards
    h = RP.hour("E1", STORM)
    on_map = lambda b: box["lat_min"] <= H._mid(b)[0] <= box["lat_max"] and box["lon_min"] <= H._mid(b)[1] <= box["lon_max"]  # noqa: E731
    over = {b["id"] for b, r in zip(D.grid()["branches"], h["lines"]["risk"]) if r >= h["line_alarm"] and not b["cable"] and on_map(b)}
    named = [l for z in fire for l in z["lines"]]
    assert named and set(named) <= over and len(named) == len(set(named))
    # the map marks the worst few places, so the smallest groups may be left to the lines' own colour
    assert len(over) / 2 < sum(z["n_lines"] for z in fire) <= len(over)
    assert [z["n_lines"] for z in fire] == sorted((z["n_lines"] for z in fire), reverse=True)
    assert "from the" in fire[0]["detail"] and "over the fire alarm" in fire[0]["detail"]


def test_a_calm_hour_shows_no_fire_and_a_later_hour_is_never_read():
    assert not [z for z in H.detected("E1", CALM) if z["kind"] == "fire"]
    d = RP.load("E1")
    h = RP._hour_index(d, STORM, None)
    wiped = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in d.items()}
    for k in ("line_risk", "loading", "line_damage", "wx_gust", "wx_dir"):
        wiped[k][h + 1:] = 0
    import unittest.mock as mock

    with mock.patch.object(RP, "load", lambda eid: wiped):
        blind = H.detected("E1", STORM)
    assert blind == H.detected("E1", STORM)  # the hours after this one change nothing


def test_the_hour_the_screen_fetches_carries_its_hazards(monkeypatch):
    monkeypatch.setitem(R.LA, "state", None)
    assert R.replay("E1", i=11)["hazards"] == H.detected("E1", STORM)


def _nws(event, severity="Severe", geometry=None, ugc=(), area="Los Angeles County Mountains", status="Actual"):
    return {"id": event, "geometry": geometry, "properties": {"id": event, "event": event, "severity": severity, "status": status, "messageType": "Alert",
                                                                 "headline": f"{event} in effect", "areaDesc": area, "ends": None, "geocode": {"UGC": list(ugc)}}}


def test_live_warnings_keep_what_touches_the_region_and_name_what_cannot_be_reached():
    box = D.grid()["bbox"]
    lat, lon = (box["lat_min"] + box["lat_max"]) / 2, (box["lon_min"] + box["lon_max"]) / 2
    inside = {"type": "Polygon", "coordinates": [[[lon - 0.1, lat - 0.1], [lon + 0.1, lat - 0.1], [lon + 0.1, lat + 0.1], [lon - 0.1, lat - 0.1]]]}
    far = {"type": "Polygon", "coordinates": [[[-122.5, 37.7], [-122.3, 37.7], [-122.3, 37.9], [-122.5, 37.7]]]}
    zone = next(iter(D.redflag()["zones"]))
    alerts = {"features": [_nws("Wind Advisory", "Moderate", inside), _nws("Red Flag Warning", "Severe", None, [zone]), _nws("Tornado Warning", "Extreme", inside),
                           _nws("Dense Fog Advisory", "Minor", far, area="San Francisco"), _nws("Flood Watch", "Severe", inside, status="Test")]}
    quakes = {"features": [{"id": "q1", "properties": {"mag": 3.6, "place": "4 km N of Pasadena, CA", "time": 1_800_000_000_000}, "geometry": {"coordinates": [lon, lat, 9.0]}},
                           {"id": "q2", "properties": {"mag": 5.1, "place": "Alaska", "time": 1_800_000_000_000}, "geometry": {"coordinates": [-150.0, 61.0, 30.0]}}]}
    out = H.live(fetch=lambda url: alerts if "weather.gov" in url else quakes, now=1_800_000_100.0)
    assert [a["event"] for a in out["alerts"]] == ["Tornado Warning", "Red Flag Warning", "Wind Advisory"]  # worst first; far away and tests dropped
    assert [a["kind"] for a in out["alerts"]] == ["tornado", "fire", "wind"] and all(a["rings"] for a in out["alerts"])
    assert [(q["id"], q["mag"]) for q in out["quakes"]] == [("q1", 3.6)] and out["unreachable"] == []

    def down(url):
        raise OSError("no network")

    offline = H.live(fetch=down, now=1_800_000_100.0)
    assert offline["alerts"] == [] and offline["quakes"] == [] and offline["unreachable"] == ["National Weather Service", "USGS"]


def test_gaps_in_a_map_picture_become_sea_with_a_soft_edge_and_small_holes_take_the_ground_around_them():
    land = (120, 118, 110)
    a = np.full((600, 800, 3), land, np.uint8)
    a[:, :275] = 0  # no tiles over open sea: a hard black block
    a[300:330, 500:530] = 0  # and one hole on land
    out = np.asarray(BM.fill_gaps(Image.fromarray(a))).astype(int)
    assert (out.max(axis=2) < BM.GAP_BELOW).mean() == 0  # no black left
    assert np.abs(out[300, 40] - np.array(BM.SEA)).max() <= 2 and np.abs(out[300, 760] - np.array(land)).max() <= 2
    assert np.abs(out[315, 515] - np.array(land)).max() <= 2  # the hole on land is ground, not sea
    edge = out[300, 265:330, 0]
    assert len(set(edge.tolist())) > 6 and edge[0] < edge[-1]  # a gradual shore, not a step
    muted = np.full((120, 160, 3), land, np.uint8)
    muted[:, :60] = BM._blank()  # in the region's own picture a missing tile is one flat grey
    inner = np.asarray(BM.fill_gaps(Image.fromarray(muted), BM.INNER_SEA, BM._blank())).astype(int)
    assert np.abs(inner[60, 10] - np.array(BM.INNER_SEA)).max() <= 2 and np.abs(inner[60, 150] - np.array(land)).max() <= 2
    untouched = Image.fromarray(np.full((40, 40, 3), land, np.uint8))
    assert BM.fill_gaps(untouched) is untouched
