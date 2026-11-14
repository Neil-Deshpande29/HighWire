"""Shared situation brief: one deterministic page of facts, built once per co-pilot run, that every agent starts from.

The "scouting" layer is code, not an LLM: physics (state.sim), the replay hour (fire alarm, storm-damage model, Red Flag
zones), calibration alarms, the downwind consequence layer, cached site-agent context and the historical validation.
The brief is handed to the specialist agents (fire, wind, heat, brownout) and to the coordinator, so the higher-level
decisions are made from the same page instead of each agent re-reading tools on its own.

Rules:
  * Every number in the brief is copied from those sources; nothing is computed by an LLM.
  * No hindsight: the brief never includes what actually happened later (real outages, real ignitions), only what an
    operator could know at that hour. Hindsight stays in get_event_context, labelled as such.
  * Small: it goes into several prompts, so lists are capped (MAX_LINES).

Schema (stable; the UI and tests read it):
  {"version", "event", "t_utc", "scenario",
   "fire":  {"line_alarm", "region_index", "region_alarm", "n_over_alarm",
             "shut_off_candidates": [line], "patrol_corridors": [line]},          line = {id, name, kv, zone, risk, gust_ms, rh_pct,
                                                                                       people_downwind, hospitals_downwind}
   "storm": {"index", "alarm", "above_alarm", "lines_damage_likely": [{id, name, kv, zone, p_damage, gust_ms}]} | None,
   "grid":  {"load_mw", "unserved_mw", "n_violations", "overloaded": [{id, loading_pct}], "critical_on_backup": [name]},
   "weather": {"max_gust_ms", "min_rh_pct", "max_temp_c", "redflag_zones_active": [zone]},
   "site":  [{line, line_summary, worst_photo, disagreements, source}],
   "trust": [{check, status, headline}],
   "headlines": [str]}                                                              3-5 one-line takeaways, numbers copied
"""
from __future__ import annotations

import json

MAX_LINES = 8
SUBTRANSMISSION_MAX_KV = 180.0  # same tier split as backend/la/state.py
# What history says about trusting the fire / storm signals. Only checks whose headlines name no real event: V5 and V8
# name the ignitions (Eaton, Hurst, ...), which would tell agents replaying those storms that a fire is coming.
TRUST_CHECKS = ("V4b", "V7")


def _r(x, nd=1):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _site_cache_stamp() -> float:
    """Newest site-agent description on disk, so a description made after the brief was cached refreshes it."""
    try:
        from site_agent import naip

        return max((p.stat().st_mtime for p in naip.CACHE.glob("*.look.json")), default=0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def build_brief(state) -> dict:
    """The situation brief for an LA replay state (cached on the state). Synthetic states get a minimal brief."""
    cache = state.__dict__.get("_brief")
    key = (getattr(state, "eid", None), str(getattr(state, "t_utc", "")), tuple(getattr(state, "operator_opened", [])),
           _site_cache_stamp())
    if cache and cache[0] == key:
        return cache[1]
    brief = _build_la(state) if hasattr(state, "grid") else _build_synthetic(state)
    state.__dict__["_brief"] = (key, brief)
    return brief


def _build_la(state) -> dict:
    from ..la import data as D
    from ..la import replay as RP

    sim = state.sim
    h = RP.hour(state.eid, t=state.t_utc.isoformat())
    fc = D.calibration()["fire"]
    over = h.get("over_alarm") or []

    def line_row(x):
        dw = x.get("downwind") or {}
        return {"id": x["id"], "name": x.get("name"), "kv": _r(x["kv"], 0), "zone": x.get("zone"), "risk": _r(x["risk"], 2),
                "gust_ms": _r(x.get("gust")), "rh_pct": _r(x.get("rh"), 0), "people_downwind": dw.get("people"),
                "hospitals_downwind": dw.get("hospitals")}

    shut = [line_row(x) for x in over if x["kv"] < SUBTRANSMISSION_MAX_KV][:MAX_LINES]
    patrol = [line_row(x) for x in over if x["kv"] >= SUBTRANSMISSION_MAX_KV][:MAX_LINES]

    storm = None
    s = h.get("storm")
    if s:
        storm = {"index": _r(s.get("index"), 3), "alarm": _r(s.get("alarm"), 3),
                 "above_alarm": bool(s.get("index") is not None and s.get("alarm") is not None and s["index"] >= s["alarm"]),
                 "lines_damage_likely": [{"id": x["id"], "name": x.get("name"), "kv": _r(x["kv"], 0), "zone": x.get("zone"),
                                          "p_damage": _r(x.get("p_damage"), 2), "gust_ms": _r(x.get("gust"))}
                                         for x in (s.get("top_lines") or [])[:MAX_LINES]]}

    L = sim["lines"]
    overloaded = sorted(((lid, v["loading_pct"]) for lid, v in L.items() if v.get("energized") and v["loading_pct"] > 100),
                        key=lambda kv: -kv[1])[:MAX_LINES]
    tot = sim["totals"]
    crit = [state.buses[b].get("critical_name") or b for b in tot.get("critical_on_backup", [])][:MAX_LINES]
    wx = h.get("weather") or {}

    site = []
    try:
        from site_agent import agent as SA

        for x in over[:3]:
            c = SA.compact(SA.describe(x["id"], state.eid, t=state.t_utc.isoformat(), use_llm=False, fetch=False))
            site.append({"line": x["id"], "line_summary": c.get("line_summary"), "worst_photo": c.get("worst_photo"),
                         "disagreements": c.get("disagreements", [])[:2], "source": c.get("source")})
    except Exception:  # noqa: BLE001  site context is optional
        site = []

    trust = []
    try:
        for c in RP.validation_summary().get("cards", []):
            if c.get("id") in TRUST_CHECKS:
                trust.append({"check": c["id"], "status": c.get("status"), "headline": c.get("headline")})
    except Exception:  # noqa: BLE001
        trust = []

    brief = {
        "version": 1, "event": state.eid, "t_utc": state.t_utc.isoformat(), "scenario": state.scenario,
        "fire": {"line_alarm": _r(fc["line_alarm"], 2), "region_index": _r(h.get("region_index"), 0),
                 "region_alarm": _r(h.get("region_alarm"), 0), "n_over_alarm": int(h.get("n_over_alarm") or 0),
                 "shut_off_candidates": shut, "patrol_corridors": patrol},
        "storm": storm,
        "grid": {"load_mw": _r(tot.get("load_mw"), 0), "unserved_mw": _r(tot.get("unserved_mw"), 0),
                 "n_violations": int(tot.get("violation_count") or 0),
                 "overloaded": [{"id": lid, "loading_pct": _r(p, 0)} for lid, p in overloaded], "critical_on_backup": crit},
        "weather": {"max_gust_ms": _r(wx.get("max_gust")), "min_rh_pct": _r(wx.get("min_rh"), 0),
                    "max_temp_c": _r(wx.get("max_temp")), "redflag_zones_active": list(h.get("redflag_active") or [])[:12]},
        "site": site,
        "trust": trust,
    }
    brief["headlines"] = _headlines(brief)
    return brief


def _build_synthetic(state) -> dict:
    sim = state.sim
    tot = sim["totals"]
    brief = {"version": 1, "event": None, "t_utc": None, "scenario": getattr(state, "scenario", ""),
             "fire": {"line_alarm": None, "region_index": None, "region_alarm": None,
                      "n_over_alarm": sum(1 for v in sim["lines"].values() if v.get("fire_risk", 0) >= 0.5),
                      "shut_off_candidates": [], "patrol_corridors": []},
             "storm": None,
             "grid": {"load_mw": _r(tot.get("load_mw"), 0), "unserved_mw": _r(tot.get("unserved_mw"), 0),
                      "n_violations": int(tot.get("violation_count") or len(sim.get("violations", []))),
                      "overloaded": [], "critical_on_backup": list(tot.get("critical_on_backup", []))},
             "weather": {}, "site": [], "trust": []}
    brief["headlines"] = [f"{brief['scenario']}: {brief['grid']['n_violations']} physics violations."]
    return brief


def _headlines(b: dict) -> list[str]:
    out = []
    f = b["fire"]
    if f["n_over_alarm"]:
        out.append(f"{f['n_over_alarm']} line(s) above the fire alarm ({len(f['shut_off_candidates'])} sub-transmission shut-off "
                   f"candidates, {len(f['patrol_corridors'])} bulk corridors to patrol); region index {f['region_index']} vs alarm "
                   f"{f['region_alarm']}.")
    else:
        out.append(f"No line above the fire alarm; region index {f['region_index']} vs alarm {f['region_alarm']}.")
    s = b["storm"]
    if s:
        out.append(f"Storm-damage index {s['index']} vs alarm {s['alarm']}" + (" (ABOVE: pre-stage crews)." if s["above_alarm"] else "."))
    g = b["grid"]
    if g["overloaded"] or g["unserved_mw"]:
        out.append(f"{len(g['overloaded'])} overloaded line(s); {g['unserved_mw']} MW unserved.")
    w = b["weather"]
    if w.get("max_gust_ms") is not None:
        out.append(f"Worst weather: gust {w['max_gust_ms']} m/s, humidity {w['min_rh_pct']}%; "
                   f"{len(w['redflag_zones_active'])} Red Flag zone(s) active.")
    worst = max((x for x in f["shut_off_candidates"] + f["patrol_corridors"] if x.get("people_downwind")),
                key=lambda x: x["people_downwind"], default=None)
    if worst:
        out.append(f"Most people downwind of a flagged line: {worst['id']} (~{worst['people_downwind']:,} within 10 km).")
    return out[:5]


def brief_text(brief: dict) -> str:
    """Compact JSON for prompts (no indentation, None fields dropped from line rows)."""
    def clean(o):
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items() if v is not None and v != [] and v != ""}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o

    return json.dumps(clean(brief), separators=(",", ":"), default=str)
