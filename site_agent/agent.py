"""Gemini site agent: looks at aerial photos along a line (up to 4, its most fire-prone stretches) and says, in words,
what is on the ground at each, plus one line-level summary.

Rules (same as the rest of Highwire):
  * Gemini describes; it never produces a number the app shows or the physics uses.
  * Its words are cross-checked against the data (Census, LANDFIRE, OSM), photo by photo. Disagreements are shown to
    the operator, not resolved silently.
  * Without a GEMINI_API_KEY the agent still returns the photos and the facts; only the descriptions are missing.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from site_agent import facts as F  # noqa: E402
from site_agent import naip  # noqa: E402

LAND_COVER = ["grass", "shrub/chaparral", "trees", "homes", "commercial/industrial", "roads/paved", "bare/rock", "water",
              "agriculture"]
STRUCTURES = ["none", "scattered", "neighborhood", "dense urban"]
VEGETATION = ["none", "low", "moderate", "heavy"]
ACCESS = ["good", "limited", "none visible"]
CONFIDENCE = ["low", "medium", "high"]
DISCLAIMER = "Gemini's description of aerial photos; context for the operator and the agents only, not used by the physics."

PROMPT = """You are the site agent on a power-grid wildfire co-pilot. The {n} images are USDA NAIP aerial photos, each
about 1.2 km across, centred on a different transmission tower of the same line (the line itself may not be visible).
Photo 1 is the most fire-prone stretch by the fuel data. Describe what is on the ground around each tower, for an
operator deciding whether to switch the line off during a windstorm and where to send patrols.

Data already known about each photo's tower (do not repeat it, and never invent numbers):
{facts}

Reply with ONLY this JSON, no prose, no code fences:
{{"photos": [one object per photo, in order:
   {{"land_cover": [up to 4 of {land_cover}],
    "structures": one of {structures},
    "vegetation_near_tower": one of {vegetation},
    "wildland_urban_interface": true or false,
    "access": one of {access},
    "summary": "one plain sentence, NO numbers"}}],
 "worst_photo": the photo number where a spark would do the most harm,
 "line_summary": "at most 2 plain sentences, NO numbers, about what would burn along this line and who is close",
 "confidence": one of {confidence}}}"""

NUMBER_WORDS = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                          r"(thir|four|fif|six|seven|eigh|nine)teen|(twen|thir|for|fif|six|seven|eigh|nine)ty|"
                          r"hundreds?|thousands?|dozens?|millions?|percent|half|halves|quarter|couple|several|"
                          r"single|double|triple|first|second|third|fourth|fifth)\b", re.I)


def _facts_for_prompt(f: dict, idx: list[int]) -> str:
    """Only facts that do not change by the hour, so one cached description is valid at every replay hour."""
    rows = []
    for k, n in enumerate(idx, 1):
        p = f["points"][n]
        rows.append({"photo": k, "zone": p.get("zone"), "landfire_fuel_at_tower": p.get("landfire_fuel")})
    return json.dumps({"kv": f.get("kv"), "line_fuel_mix": f.get("fuel_mix"), "photos": rows})


def _parse(text: str) -> dict:
    text = re.sub(r"```(json)?", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON in Gemini reply")
    return json.loads(text[a: b + 1])


def _as_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return bool(v)


def _clean_text(s, limit: int, notes: list[str], what: str) -> str:
    s = str(s or "").strip()
    if re.search(r"\d", s) or NUMBER_WORDS.search(s):
        notes.append(f"Gemini's {what} stated numbers, so it was withheld (numbers come from data only).")
        return ""
    return s[:limit]


def _pick(v, allowed):
    return v if v in allowed else "unknown"


def sanitize_photo(raw: dict, notes: list[str], label: str = "summary") -> dict:
    """One photo's description: only allowed values ("unknown" otherwise), no numbers in the words."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "land_cover": [x for x in (raw.get("land_cover") or []) if x in LAND_COVER][:4],
        "structures": _pick(raw.get("structures"), STRUCTURES),
        "vegetation_near_tower": _pick(raw.get("vegetation_near_tower"), VEGETATION),
        "wildland_urban_interface": _as_bool(raw.get("wildland_urban_interface", False)),
        "access": _pick(raw.get("access"), ACCESS),
        "summary": _clean_text(raw.get("summary"), 240, notes, label),
    }


def sanitize(raw: dict, n_photos: int) -> tuple[dict, list[str]]:
    """Whole reply -> {"photos": [...n_photos], "worst_photo", "line_summary", "confidence"}, plus notes."""
    notes: list[str] = []
    photos = raw.get("photos") if isinstance(raw.get("photos"), list) else []
    looks = [sanitize_photo(photos[k] if k < len(photos) else {}, notes, f"photo {k + 1} summary") for k in range(n_photos)]
    if len(photos) != n_photos:
        notes.append(f"Gemini described {len(photos)} of {n_photos} photos.")
    try:
        worst = int(raw.get("worst_photo"))
    except (TypeError, ValueError):
        worst = None
    return {"photos": looks, "worst_photo": worst if worst and 1 <= worst <= n_photos else None,
            "line_summary": _clean_text(raw.get("line_summary"), 400, notes, "line summary"),
            "confidence": _pick(raw.get("confidence"), CONFIDENCE)}, notes


def cross_check(point: dict, look: dict) -> list[dict]:
    """Where Gemini's reading of ONE photo and that tower's data disagree. Each item: {"kind", "message"}."""
    out = []
    lc = set(look.get("land_cover") or [])
    burnable_seen = bool(lc & {"grass", "shrub/chaparral", "trees"})
    fuel = point.get("landfire_fuel")
    rec = point.get("spark_receptivity")
    if fuel in ("urban / developed", "barren", "agriculture", "water") and burnable_seen:
        out.append({"kind": "fuel", "message": f"LANDFIRE marks this tower as {fuel}, but the photo shows burnable "
                    "vegetation. The fire model may understate risk here."})
    if rec is not None and rec >= 0.8 and not burnable_seen and lc:
        out.append({"kind": "fuel", "message": "LANDFIRE says grass or brush at this tower, but the photo shows mostly "
                    "built or bare ground. The fire model may overstate risk here."})
    people = (point.get("downwind") or {}).get("people")
    if people is not None:
        if look.get("structures") in ("neighborhood", "dense urban") and people < 1000:
            out.append({"kind": "people", "message": "The photo shows homes near the tower, but Census counts few people "
                        "in the downwind cone. Tract centroids can miss homes on the wildland edge."})
        if look.get("structures") == "none" and people >= 20000:
            out.append({"kind": "people", "message": "Census puts many people downwind, but none are visible in the photo. "
                        "They are likely beyond the photo's edge."})
    return out


def cache_key(tiles: list, have: list[int], f: dict) -> list[str]:
    """Identity of a description: the photos (id + position) and the exact prompt with its facts. Changing the photos,
    the prompt wording or the LANDFIRE facts it quotes makes a fresh Gemini call."""
    import hashlib

    prompt_hash = hashlib.sha1((PROMPT + _facts_for_prompt(f, have)).encode()).hexdigest()[:12]
    return [tiles[n]["item"] + f"@{n}" for n in have] + [f"prompt:{prompt_hash}"]


def _look_cache(line_id: str) -> Path:
    return naip.CACHE / f"{line_id}.look.json"


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _line_lock(line_id: str) -> threading.Lock:
    """One Gemini call per line at a time: scrubbing replay hours must not start parallel calls for the same photos."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(line_id, threading.Lock())


def look_with_gemini(tiles: list[dict], f: dict, idx: list[int], chat=None) -> tuple[dict, list[str]]:
    """One multimodal Gemini call over all of a line's photos. `idx` maps photo order -> facts["points"] index.
    `chat` lets tests pass a fake model."""
    from langchain_core.messages import HumanMessage

    from backend import llm as LLM

    chat = chat or LLM.chat_model(timeout=40, max_retries=1, max_tokens=1200)
    prompt = PROMPT.format(n=len(tiles), facts=_facts_for_prompt(f, idx), land_cover=LAND_COVER, structures=STRUCTURES,
                           vegetation=VEGETATION, access=ACCESS, confidence=CONFIDENCE)
    content = [{"type": "text", "text": prompt}]
    for k, t in enumerate(tiles, 1):
        img = base64.b64encode(Path(t["img"]).read_bytes()).decode()
        content += [{"type": "text", "text": f"Photo {k}:"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]
    reply = chat.invoke([HumanMessage(content=content)])
    text = reply.content if isinstance(reply.content, str) else "".join(
        c.get("text", "") if isinstance(c, dict) else str(c) for c in reply.content)
    return sanitize(_parse(text), len(tiles))


def describe(line_id: str, event_id: str | None = None, i: int | None = None, use_llm: bool = True, chat=None,
             fetch: bool = True, t: str | None = None) -> dict:
    """Facts + photos + Gemini's descriptions + per-photo cross-checks for one line (optionally at a replay hour).

    Returns {"line", "facts", "photos": [{"n", "point", "tile", "look", "checks"}], "look": {"line_summary",
    "worst_photo", "confidence"} | None, "look_source", "checks" (all photos, each tagged with its photo number),
    "notes", "disclaimer"}."""
    from backend import llm as LLM

    f = F.line_facts(line_id, event_id, i, t)
    pts = [(p["lat"], p["lon"]) for p in f["points"]]
    tiles, notes = naip.tiles_for(line_id, pts, fetch=fetch)
    out = {"line": line_id, "facts": f, "photos": [], "look": None, "look_source": None, "checks": [], "notes": notes,
           "disclaimer": DISCLAIMER}
    for n, (p, tile) in enumerate(zip(f["points"], tiles)):
        out["photos"].append({"n": n, "point": p, "tile": {k: v for k, v in tile.items() if k != "img"} if tile else None,
                              "look": None, "checks": []})
    have = [n for n, tile in enumerate(tiles) if tile]
    if not have:
        out["look_source"] = "none: no photo"
        return out
    key = cache_key(tiles, have, f)
    cache = _look_cache(line_id)
    model = LLM.model_name()
    look: dict | None = None

    def read_cache():
        if cache.exists():
            try:
                c = json.loads(cache.read_text())
            except (OSError, ValueError):
                return None
            if c.get("photos_key") == key and c.get("model") == model:
                return c
        return None

    c = read_cache()
    if c:
        look, out["look_source"] = c["look"], f"gemini ({model}, cached)"
        out["notes"] += c.get("notes", [])
    else:
        if not use_llm:
            out["look_source"] = "none: not described yet (open the line in the Site agent panel, or run make site)"
            return out
        if chat is None and not LLM.enabled():
            out["look_source"] = "none: no GEMINI_API_KEY"
            return out
        with _line_lock(line_id):
            c = None if chat is not None else read_cache()  # another request may have described it while we waited
            if c:
                look, out["look_source"] = c["look"], f"gemini ({model}, cached)"
                out["notes"] += c.get("notes", [])
            else:
                try:
                    look, new_notes = look_with_gemini([tiles[n] for n in have], f, have, chat=chat)
                except Exception as e:  # noqa: BLE001
                    out["look_source"] = f"error: {str(e)[:160]}"
                    return out
                out["notes"] += new_notes
                out["look_source"] = f"gemini ({model})"
                if chat is None:  # only cache real model output; a failed write must not discard a good answer
                    try:
                        cache.write_text(json.dumps({"photos_key": key, "model": model, "look": look, "notes": new_notes}, indent=1))
                    except OSError as e:
                        out["notes"].append(f"description not cached: {str(e)[:80]}")
    for k, n in enumerate(have):
        ph = out["photos"][n]
        ph["look"] = look["photos"][k]
        ph["checks"] = cross_check(ph["point"], ph["look"])
        out["checks"] += [{**chk, "photo": n + 1} for chk in ph["checks"]]
    worst = look.get("worst_photo")
    out["look"] = {"line_summary": look.get("line_summary", ""), "confidence": look.get("confidence", "unknown"),
                   "worst_photo": have[worst - 1] + 1 if worst and worst <= len(have) else None}
    return out


def compact(out: dict) -> dict:
    """Small, number-light version for the co-pilot agents' prompts."""
    photos = []
    for ph in out["photos"]:
        p = ph["point"]
        row = {"photo": ph["n"] + 1, "zone": p.get("zone"), "landfire_fuel": p.get("landfire_fuel")}
        if p.get("downwind"):
            row["people_downwind_10km"] = p["downwind"]["people"]
            row["hospitals_downwind"] = p["downwind"]["hospitals"]
        if ph["look"]:
            lk = ph["look"]
            row.update({"seen": lk["land_cover"], "buildings": lk["structures"], "vegetation_at_tower": lk["vegetation_near_tower"],
                        "wildland_urban_edge": lk["wildland_urban_interface"], "access": lk["access"], "note": lk["summary"]})
        photos.append(row)
    f = out["facts"]
    return {"line": out["line"], "kv": f["kv"], "length_km": f["length_km"], "fuel_mix": f.get("fuel_mix"), "photos": photos,
            "line_summary": (out["look"] or {}).get("line_summary"), "worst_photo": (out["look"] or {}).get("worst_photo"),
            "disagreements": [f"photo {c['photo']}: {c['message']}" for c in out["checks"]], "source": out["look_source"],
            "disclaimer": out["disclaimer"]}


def batch(line_ids: list[str] | None = None) -> None:
    """Describe every alarmed line once (photos + one Gemini call each) so the co-pilot agents can read cached context."""
    from backend import llm as LLM

    ids = line_ids or F.alarm_lines()
    print(f"site agent: {len(ids)} lines, model {LLM.model_name()}, key {'set' if LLM.enabled() else 'NOT set (photos only)'}")
    for k, lid in enumerate(ids, 1):
        out = describe(lid)
        print(f"[{k}/{len(ids)}] {lid}: {sum(p['tile'] is not None for p in out['photos'])} photos, {out['look_source']}, "
              f"{len(out['checks'])} disagreement(s)")


if __name__ == "__main__":
    import pprint

    args = sys.argv[1:]
    if not args or args[0] == "--batch":
        batch(args[1:] or None)
    else:
        pprint.pprint(describe(args[0], args[1] if len(args) > 1 else None, int(args[2]) if len(args) > 2 else None))
