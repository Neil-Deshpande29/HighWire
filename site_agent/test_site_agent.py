"""Site agent tests: no network, no API key. A fake chat model stands in for Gemini."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from site_agent import agent as A  # noqa: E402
from site_agent import facts as F  # noqa: E402
from site_agent import naip  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32


class FakeReply:
    def __init__(self, content):
        self.content = content


class FakeChat:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return FakeReply(self.payload if isinstance(self.payload, str) else json.dumps(self.payload))


def photo(**kw):
    base = {"land_cover": ["shrub/chaparral", "homes"], "structures": "neighborhood", "vegetation_near_tower": "heavy",
            "wildland_urban_interface": True, "access": "limited", "summary": "Homes back onto dense brush."}
    return {**base, **kw}


# ------------------------------------------------------------------ photo points and facts
def test_bbox_is_about_1_2_km_square():
    minx, miny, maxx, maxy = naip.bbox(34.2, -118.1)
    assert abs((maxy - miny) * 110.54 - 1.2) < 0.01
    assert abs((maxx - minx) * 111.32 * 0.8271 - 1.2) < 0.02  # cos(34.2 deg) = 0.8271


def test_photo_points_are_spaced_along_the_line():
    pts = F.line_points("L29")  # 27 km line
    assert 2 <= len(pts) <= F.MAX_POINTS
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            d = np.hypot((pts[a][0] - pts[b][0]) * 110.54, (pts[a][1] - pts[b][1]) * 111.32 * np.cos(np.radians(34)))
            assert d >= F.MIN_SPACING_KM - 1e-6
    assert pts[0] == F.line_point("L29")


def test_facts_for_a_real_line():
    f = F.line_facts("L29")
    assert f["line"] == "L29" and f["n_towers"] > 0 and f["point"] == f["points"][0]
    assert all(-119 < p["lon"] < -117.6 and 33.6 < p["lat"] < 34.5 for p in f["points"])


def test_facts_with_replay_hour_add_weather_and_downwind_per_photo():
    f = F.line_facts("L29", "E3", 10)
    for p in f["points"]:
        assert set(p["weather"]) == {"gust_ms", "rh_pct", "wind_from_deg"} and p["downwind"]["people"] >= 0
    assert f["downwind"]["people"] == max(p["downwind"]["people"] for p in f["points"])


# ------------------------------------------------------------------ sanitizing and cross-checks
def test_sanitize_unknown_values_numbers_and_string_booleans():
    look, notes = A.sanitize({"photos": [photo(land_cover=["grass", "volcano"], structures="skyscrapers",
                                               summary="About 1,200 homes.", wildland_urban_interface="false"),
                                         photo(summary="Dozens of homes back onto the brush.")],
                              "worst_photo": "2", "line_summary": "Brush runs right up to the homes.", "confidence": "certain"}, 3)
    p1, p2, p3 = look["photos"]
    assert p1["land_cover"] == ["grass"] and p1["structures"] == "unknown" and p1["summary"] == ""
    assert p1["wildland_urban_interface"] is False
    assert p2["summary"] == ""  # spelled-out number withheld too
    assert p3["structures"] == "unknown" and p3["land_cover"] == []  # a photo Gemini skipped
    assert look["worst_photo"] == 2 and look["confidence"] == "unknown"
    assert look["line_summary"] == "Brush runs right up to the homes."
    assert len(notes) == 3  # two withheld summaries + "described 2 of 3"


def test_cross_check_flags_landfire_urban_vs_brush():
    p = {"landfire_fuel": "urban / developed", "spark_receptivity": 0.05, "downwind": {"people": 50000}}
    assert [c["kind"] for c in A.cross_check(p, photo())] == ["fuel"]


def test_cross_check_flags_landfire_brush_vs_built_ground():
    p = {"landfire_fuel": "grass", "spark_receptivity": 1.0}
    assert [c["kind"] for c in A.cross_check(p, photo(land_cover=["roads/paved"], structures="dense urban"))] == ["fuel"]


def test_cross_check_flags_homes_census_misses_and_ignores_unknown():
    p = {"landfire_fuel": "shrub", "spark_receptivity": 0.8, "downwind": {"people": 120}}
    assert [c["kind"] for c in A.cross_check(p, photo())] == ["people"]
    p["downwind"]["people"] = 50000
    assert A.cross_check(p, photo(structures="unknown")) == []


def _key(line):
    """Cache key exactly as describe() computes it for the fake tiles (photo ids naip_<n>)."""
    f = F.line_facts(line)
    have = list(range(len(f["points"])))
    return A.cache_key([{"item": f"naip_{n}"} for n in have], have, f)


def test_number_words_are_withheld():
    for s in ["Roughly sixty homes border the tower.", "Homes sit thirteen streets away.", "Half the slope is brush.",
              "A couple of houses back onto brush.", "The first ridge is bare."]:
        look, notes = A.sanitize({"photos": [photo(summary=s)], "line_summary": s}, 1)
        assert look["photos"][0]["summary"] == "" and look["line_summary"] == "" and notes, s


def test_photo_points_rank_fuel_before_elevation():
    t = F._towers()
    m = np.where(t["tb"] == F._index()["L29"])[0]
    fuel = np.round((t["recept"][m] if "recept" in t else 1) * np.array([F.ZONE_FUEL[z] for z in t["zone"][m]]), 6)
    first = F.fire_points("L29", k=1)[0]
    assert fuel[list(m).index(first)] == fuel.max()


def test_cache_key_changes_with_the_prompt(monkeypatch):
    k1 = _key("L29")
    monkeypatch.setattr(A, "PROMPT", A.PROMPT + " ")
    assert _key("L29") != k1


# ------------------------------------------------------------------ describe(): photos, Gemini, cache
@pytest.fixture
def tiles(tmp_path, monkeypatch):
    """Fake cached photos for every photo point of L29 (no network)."""
    monkeypatch.setattr(naip, "CACHE", tmp_path)
    for n, (lat, lon) in enumerate(F.line_points("L29")):
        (tmp_path / f"L29-{n}.jpg").write_bytes(JPEG)
        (tmp_path / f"L29-{n}.json").write_text(json.dumps({"line": "L29", "n": n, "item": f"naip_{n}", "photo_date": "2022-05-05",
                                                             "center": [lat, lon], "bbox": [], "source": "test"}))
    return tmp_path


def test_describe_one_call_for_all_photos(tiles):
    n = len(F.line_points("L29"))
    chat = FakeChat({"photos": [photo() for _ in range(n)], "worst_photo": 1, "line_summary": "Homes back onto dense brush.",
                     "confidence": "medium"})
    out = A.describe("L29", "E3", 10, chat=chat, fetch=False)
    assert len(chat.calls) == 1
    sent = chat.calls[0][0].content
    assert sum(c["type"] == "image_url" for c in sent) == n
    assert all(c["image_url"]["url"].startswith("data:image/jpeg;base64,") for c in sent if c["type"] == "image_url")
    assert [p["look"]["structures"] for p in out["photos"]] == ["neighborhood"] * n
    assert out["look"]["worst_photo"] == 1 and out["look_source"].startswith("gemini")
    assert all("img" not in p["tile"] for p in out["photos"])
    assert not (tiles / "L29.look.json").exists()  # fake output is never cached


def test_cached_description_is_reused_without_a_new_call(tiles):
    from backend import llm as LLM

    n = len(F.line_points("L29"))
    look = {"photos": [A.sanitize_photo(photo(), []) for _ in range(n)], "worst_photo": 2, "line_summary": "Brush to the homes.",
            "confidence": "high"}
    (tiles / "L29.look.json").write_text(json.dumps({"photos_key": _key("L29"), "model": LLM.model_name(), "look": look, "notes": []}))
    chat = FakeChat("should not be called")
    out = A.describe("L29", "E3", 10, chat=chat, fetch=False)
    assert chat.calls == [] and "cached" in out["look_source"] and out["look"]["worst_photo"] == 2


def test_without_key_returns_facts_and_photos(tiles, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    out = A.describe("L29", fetch=False)
    assert out["look"] is None and out["look_source"] == "none: no GEMINI_API_KEY"
    assert all(p["tile"] for p in out["photos"])


def test_agent_tool_mode_never_calls_gemini(tiles):
    out = A.describe("L29", use_llm=False, fetch=False)
    assert out["look"] is None and out["look_source"].startswith("none: not described yet")


def test_bad_gemini_reply_is_an_error_not_a_crash(tiles):
    out = A.describe("L29", chat=FakeChat("sorry, I can't help with that"), fetch=False)
    assert out["look"] is None and out["look_source"].startswith("error")


def test_compact_context_for_agents(tiles):
    n = len(F.line_points("L29"))
    chat = FakeChat({"photos": [photo() for _ in range(n)], "worst_photo": 1, "line_summary": "Homes back onto dense brush.",
                     "confidence": "medium"})
    c = A.compact(A.describe("L29", "E3", 10, chat=chat, fetch=False))
    assert c["line_summary"] == "Homes back onto dense brush." and len(c["photos"]) == n
    assert {"seen", "buildings", "access", "people_downwind_10km"} <= set(c["photos"][0])


# ------------------------------------------------------------------ wiring: API, agent tool, rule-based planner
def test_site_api_routes(tiles, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import app

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    c = TestClient(app)
    r = c.get("/la/site/L29?event=E3&i=10")
    assert r.status_code == 200 and len(r.json()["photos"]) >= 2 and "downwind" in r.json()["photos"][0]["point"]
    assert c.get("/la/site/L29/photo/0.jpg").headers["content-type"] == "image/jpeg"
    assert c.get("/la/site/L29/photo/7.jpg").status_code == 404
    assert c.get("/la/site/..%5Cetc/photo/0.jpg").status_code == 404
    assert c.get("/la/site/L99999").status_code == 404
    assert c.get("/la/site/L29?event=NOPE").status_code == 404


def test_agent_tool_and_rule_planner_use_cached_context(tiles):
    from backend import llm as LLM
    from backend.agents import mock_planner as MP
    from backend.agents.tools import JobLog, make_tools
    from backend.la.state import LAState

    n = len(F.line_points("L29"))
    look = {"photos": [A.sanitize_photo(photo(), []) for _ in range(n)], "worst_photo": 1,
            "line_summary": "Homes back onto dense brush.", "confidence": "high"}
    (tiles / "L29.look.json").write_text(json.dumps({"photos_key": _key("L29"), "model": LLM.model_name(),
                                                      "look": look, "notes": []}))
    st = LAState("E3", "2025-01-08T02:00:00+00:00")
    log = JobLog()
    tool = make_tools(st, log, "wind_agent", ["get_site_context"])[0]
    ctx = json.loads(tool("L29"))
    assert ctx["line_summary"] == "Homes back onto dense brush." and ctx["photos"][0]["access"] == "limited"
    assert MP._site_phrase(MP._site(st, "L29")).startswith(" Site agent (aerial photos): Homes back onto dense brush.")
