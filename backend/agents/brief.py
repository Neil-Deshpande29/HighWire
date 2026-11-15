"""Shared situation brief: one deterministic page of facts, built once per co-pilot run, that every agent starts from.

The "scouting" layer is code, not an LLM: physics (state.sim), the replay hour (fire alarm, storm-damage model, Red Flag
zones), calibration alarms, the downwind consequence layer, cached site-agent context and the historical validation.
The brief is handed to the specialist agents (fire, wind, heat, brownout) and to the coordinator, so the higher-level
decisions are made from the same page instead of each agent re-reading tools on its own.

Rules:
  * Every number in the brief is copied from those sources; nothing is computed by an LLM.
  * No hindsight: the brief never includes what actually happened later (real outages, real ignitions), only what an
    operator could know at that hour. Hindsight stays in get_event_context and is kept out of the brief.
  * Small: it goes into several prompts, so lists are capped (MAX_LINES, MAX_ZONES). A capped list never stands in for
    a count: every count is its own n_* field, taken from the full data.

Schema (stable; the UI and tests read it):
  {"version", "event", "t_utc", "scenario",
   "fire":  {"line_alarm", "region_index", "region_alarm", "n_over_alarm", "n_shut_off", "n_patrol",
             "shut_off_candidates": [line], "patrol_corridors": [line]},          line = {id, name, kv, zone, risk, gust_ms, rh_pct,
                                                                                       people_downwind, hospitals_downwind}
   "storm": {"index", "alarm", "above_alarm", "recorded",
             "lines_damage_likely": [{id, name, kv, zone, p_damage, gust_ms}]} | None,
   "grid":  {"load_mw", "unserved_mw", "n_violations", "n_overloaded", "overloaded": [{id, loading_pct}],
             "critical_on_backup": [name]},
   "weather": {"max_gust_ms", "min_rh_pct", "max_temp_c", "n_redflag_active", "redflag_zones_active": [zone],
               "redflag_recorded"},
   "site":  [{line, line_summary, worst_photo, disagreements, source}],
   "trust": [{check, status, headline}],
   "headlines": [str]}                                                              3-5 one-line takeaways, numbers copied

What the fields measure:
  * n_shut_off / n_patrol: every energized overhead line at or above the fire alarm in the state's own simulation, split
    where the state splits them (below / at or above SUBTRANSMISSION_MAX_KV). Together they are n_over_alarm. The lists
    under them hold only the riskiest MAX_LINES per tier out of the 25 riskiest lines overall.
  * n_overloaded and n_redflag_active count every overloaded line and every Red Flag zone in force; their lists are capped.
  * people_downwind / hospitals_downwind: 2020 Census population (tract centres) and hospitals inside the 10 km downwind cone
    drawn from that line's mid point along this hour's wind. 0 means no homes were counted in that cone, not that nobody
    is at risk; None means the count is missing.
  * storm.recorded / weather.redflag_recorded: true when the state is modified (Study conditions or operator changes) and
    the block therefore still describes the recorded hour, not the modified state. The storm-damage model needs the
    weather history and Red Flag zones are the warnings NWS had in force, so neither is recomputed.
"""
from __future__ import annotations

import json

MAX_LINES = 8
MAX_ZONES = 12
SUBTRANSMISSION_MAX_KV = 180.0  # same tier split as backend/la/state.py
PEOPLE_SCOPE = "10 km downwind cone from its mid point, 2020 Census"
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


def _cache_key(state) -> tuple:
    """Everything a cached brief depends on: the hour, every input LAState.is_modified checks (a brief built before an
    operator action must not be served after it), and the site-agent cache. The toy region keeps one state object
    across scenarios and redispatches generators in place, so its scenario and last solve time are in the key too."""
    def dump(name):
        return json.dumps(getattr(state, name, None), sort_keys=True)

    return (getattr(state, "eid", None), str(getattr(state, "t_utc", "")), getattr(state, "scenario", None),
            getattr(state, "sim_time", None), tuple(getattr(state, "operator_opened", [])), tuple(getattr(state, "deenergized", [])),
            dump("conditions"), dump("caps"), dump("shed"), dump("imp_override"), _site_cache_stamp())


def _tier_counts(state, alarm: float) -> tuple[int, int]:
    """Lines at or above the fire alarm in the state's own simulation, counted over every line rather than the capped
    lists: (sub-transmission shut-off candidates, bulk corridors to patrol), split where state.simulate splits them."""
    lines = state.sim["lines"]
    kv = [b["kv"] for b in state.grid.branches
          if not b["cable"] and lines[b["id"]]["energized"] and lines[b["id"]]["fire_risk"] >= alarm]
    n_shut = sum(v < SUBTRANSMISSION_MAX_KV for v in kv)
    return n_shut, len(kv) - n_shut


def build_brief(state) -> dict:
    """The situation brief for an LA replay state (cached on the state). Synthetic states get a minimal brief."""
    cache = state.__dict__.get("_brief")
    key = _cache_key(state)
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
    modified = bool(getattr(state, "is_modified", lambda: False)())
    if modified:
        # Study conditions or operator changes: the precomputed replay no longer describes this grid, so the fire lines,
        # region index and weather come from this state's own physics (same engine and calibration). Red Flag zones are
        # the warnings NWS actually had in force; the storm-damage index needs the weather history, so it stays recorded
        # (flagged below as storm.recorded and weather.redflag_recorded).
        over, _, n_hot = state.fire_lines()
        h = {**h, "over_alarm": over, "n_over_alarm": n_hot, "region_index": sim.get("region_index"),
             "weather": {**(h.get("weather") or {}), **state.weather_extremes()}}

    def line_row(x):
        dw = x.get("downwind") or {}
        return {"id": x["id"], "name": x.get("name"), "kv": _r(x["kv"], 0), "zone": x.get("zone"), "risk": _r(x["risk"], 2),
                "gust_ms": _r(x.get("gust")), "rh_pct": _r(x.get("rh"), 0), "people_downwind": dw.get("people"),
                "hospitals_downwind": dw.get("hospitals")}

    shut = [line_row(x) for x in over if x["kv"] < SUBTRANSMISSION_MAX_KV][:MAX_LINES]
    patrol = [line_row(x) for x in over if x["kv"] >= SUBTRANSMISSION_MAX_KV][:MAX_LINES]
    n_shut, n_patrol = _tier_counts(state, fc["line_alarm"])

    storm = None
    s = h.get("storm")
    if s:
        storm = {"index": _r(s.get("index"), 3), "alarm": _r(s.get("alarm"), 3),
                 "above_alarm": bool(s.get("index") is not None and s.get("alarm") is not None and s["index"] >= s["alarm"]),
                 "recorded": modified,
                 "lines_damage_likely": [{"id": x["id"], "name": x.get("name"), "kv": _r(x["kv"], 0), "zone": x.get("zone"),
                                          "p_damage": _r(x.get("p_damage"), 2), "gust_ms": _r(x.get("gust"))}
                                         for x in (s.get("top_lines") or [])[:MAX_LINES]]}

    L = sim["lines"]
    overloaded = sorted(((lid, v["loading_pct"]) for lid, v in L.items() if v.get("energized") and v["loading_pct"] > 100),
                        key=lambda kv: -kv[1])
    tot = sim["totals"]
    crit = [state.buses[b].get("critical_name") or b for b in tot.get("critical_on_backup", [])][:MAX_LINES]
    wx = h.get("weather") or {}
    zones = list(h.get("redflag_active") or [])

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
                 "n_shut_off": n_shut, "n_patrol": n_patrol, "shut_off_candidates": shut, "patrol_corridors": patrol},
        "storm": storm,
        "grid": {"load_mw": _r(tot.get("load_mw"), 0), "unserved_mw": _r(tot.get("unserved_mw"), 0),
                 "n_violations": int(tot.get("violation_count") or 0), "n_overloaded": len(overloaded),
                 "overloaded": [{"id": lid, "loading_pct": _r(p, 0)} for lid, p in overloaded[:MAX_LINES]], "critical_on_backup": crit},
        "weather": {"max_gust_ms": _r(wx.get("max_gust")), "min_rh_pct": _r(wx.get("min_rh"), 0), "max_temp_c": _r(wx.get("max_temp")),
                    "n_redflag_active": len(zones), "redflag_zones_active": zones[:MAX_ZONES], "redflag_recorded": modified},
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
                      "n_shut_off": None, "n_patrol": None, "shut_off_candidates": [], "patrol_corridors": []},
             "storm": None,
             "grid": {"load_mw": _r(tot.get("load_mw"), 0), "unserved_mw": _r(tot.get("unserved_mw"), 0),
                      "n_violations": int(tot.get("violation_count") or len(sim.get("violations", []))),
                      "n_overloaded": None, "overloaded": [], "critical_on_backup": list(tot.get("critical_on_backup", []))},
             "weather": {}, "site": [], "trust": []}
    brief["headlines"] = [f"{brief['scenario']}: {brief['grid']['n_violations']} physics violations."]
    return brief


def _listed(n: int, rows: list, top: str = "top ") -> str:
    """Qualifier after a count whose list was capped, so the list is never read as the whole of it."""
    if len(rows) >= n:
        return ""
    return f" ({top}{len(rows)} listed)" if rows else " (none listed)"


def _headlines(b: dict) -> list[str]:
    out = []
    f = b["fire"]
    if f["n_over_alarm"]:
        out.append(f"{f['n_over_alarm']} line(s) above the fire alarm: {f['n_shut_off']} sub-transmission shut-off candidates"
                   f"{_listed(f['n_shut_off'], f['shut_off_candidates'])}, {f['n_patrol']} bulk corridors to patrol"
                   f"{_listed(f['n_patrol'], f['patrol_corridors'])}; region index {f['region_index']} vs alarm {f['region_alarm']}.")
    else:
        out.append(f"No line above the fire alarm; region index {f['region_index']} vs alarm {f['region_alarm']}.")
    s = b["storm"]
    if s:
        out.append(f"Storm-damage index {s['index']} vs alarm {s['alarm']}"
                   + (" at the recorded hour, not recomputed for this modified state" if s["recorded"] else "")
                   + (" (ABOVE: pre-stage crews)." if s["above_alarm"] else "."))
    g = b["grid"]
    if g["n_overloaded"] or g["unserved_mw"]:
        out.append(f"{g['n_overloaded']} overloaded line(s){_listed(g['n_overloaded'], g['overloaded'])}; {g['unserved_mw']} MW unserved.")
    w = b["weather"]
    if w.get("max_gust_ms") is not None:
        out.append(f"Worst weather: gust {w['max_gust_ms']} m/s, humidity {w['min_rh_pct']}%; {w['n_redflag_active']} Red Flag zone(s) "
                   f"in force{' at the recorded hour' if w['redflag_recorded'] else ''}"
                   f"{_listed(w['n_redflag_active'], w['redflag_zones_active'], top='')}.")
    counted = [x for x in f["shut_off_candidates"] + f["patrol_corridors"] if x.get("people_downwind") is not None]
    if counted:
        worst = max(counted, key=lambda x: x["people_downwind"])
        if worst["people_downwind"]:
            out.append(f"Most people downwind of a listed line: {worst['id']} (~{worst['people_downwind']:,} in the {PEOPLE_SCOPE}).")
        else:  # a zero is a count of homes in the cones, never an all-clear
            out.append(f"No homes counted downwind of the {len(counted)} listed line(s) (each line's {PEOPLE_SCOPE}); "
                       "a count, not an all-clear.")
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
