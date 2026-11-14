"""Ground reports: free text -> structured report pinned to the nearest line (or bus)."""
from __future__ import annotations

import hashlib
import math
import os
import re
import time

KINDS = ["wire_down", "sparking", "smoke", "tree_on_line", "water_at_substation", "outage"]

_KIND_RULES = [
    ("water_at_substation", r"(water|flood|surge).*(substation|sub\b|station|yard)|(substation|station).*(water|flood)"),
    ("wire_down", r"wire[s]? (is |are )?down|down(ed)? (wire|line|power)|line[s]? (is |are )?down|fallen (wire|line)|on the ground"),
    ("sparking", r"spark|arcing|\barc\b|buzzing|flash"),
    ("tree_on_line", r"\btrees?\b|branch|limb"),
    ("smoke", r"smoke|fire|flame|burning|embers"),
    ("outage", r"outage|no power|power (is )?out|lights (are )?out|blackout|dark|flicker"),
]

_BUS_ALIASES = {
    "B1": ["ridge hydro", "hydro", "dam"],
    "B2": ["summit"],
    "B3": ["pine hollow", "pine"],
    "B4": ["power plant", "gas plant", "valley plant"],
    "B5": ["valley town", "town center", "school"],
    "B6": ["water plant", "water treatment", "treatment plant"],
    "B7": ["mill district", "mill"],
    "B8": ["central", "downtown"],
    "B9": ["harbor", "harbour", "marina", "port"],
    "B10": ["mercy", "hospital"],
    "B11": ["bayside", "bay side"],
    "B12": ["ridge junction", "junction"],
}
_ZONE_WORDS = {
    "ridge": r"ridge|mountain|hill|summit|pass|canyon",
    "valley": r"valley|town|farm|river|mill",
    "coast": r"coast|beach|harbor|harbour|bay|shore|sea|marina|pier",
}


# --------------------------------------------------------------------------- parsing
def parse_rules(text: str) -> dict:
    t = text.lower()
    kind = next((k for k, rx in _KIND_RULES if re.search(rx, t)), "outage")
    sev = 2
    if re.search(r"spark|fire|flame|smoke|live|kids?|school|hospital|arcing|down", t):
        sev = 3
    if kind == "outage" and sev == 2:
        sev = 1
    hint = None
    for bid, names in _BUS_ALIASES.items():
        for n in names:
            if n in t:
                hint = n
                break
        if hint:
            break
    if not hint:
        m = re.search(r"\b(on|near|at|by|along)\s+([a-z0-9' ]{3,30}?)(,|\.|$| and | sparking| smoking)", t)
        hint = m.group(2).strip() if m else None
    return {"kind": kind, "location_hint": hint or "", "severity": sev, "parser": "rules"}


def parse_llm(text: str) -> dict | None:
    from . import llm as LLM

    if not LLM.enabled():
        return None
    try:
        from pydantic import BaseModel, Field
        from typing import Literal

        class Report(BaseModel):
            kind: Literal["wire_down", "sparking", "smoke", "tree_on_line", "water_at_substation", "outage"]
            location_hint: str = Field(description="place words from the text, e.g. 'Summit ridge road'")
            severity: int = Field(ge=1, le=3, description="3 = immediate danger to life")

        llm = LLM.chat_model(timeout=10, max_retries=0, max_tokens=512)
        out = llm.with_structured_output(Report).invoke(
            "Extract a structured field report from this storm call. Place names in this region: "
            + ", ".join(n[0] for n in _BUS_ALIASES.values())
            + f".\n\nReport: {text}"
        )
        return out.model_dump() | {"parser": "llm"}
    except Exception:
        return None


# --------------------------------------------------------------------------- geometry
def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy or 1.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), cx, cy, t


def nearest_line(state, x, y):
    best = None
    for lid, ln in state.lines.items():
        a, b = state.buses[ln["from_bus"]], state.buses[ln["to_bus"]]
        d, cx, cy, _ = _seg_dist(x, y, a["x"], a["y"], b["x"], b["y"])
        if best is None or d < best[0]:
            best = (d, lid, cx, cy)
    return best


def nearest_bus(state, x, y):
    return min(state.buses.values(), key=lambda b: math.hypot(b["x"] - x, b["y"] - y))


def _risk_score(state, lid):
    s = state.sim["lines"][lid]
    return len(s["violations"]) * 10 + s["fire_risk"] * 5 + s["loading_pct"] / 100


def _point_on_line(state, lid, frac):
    ln = state.lines[lid]
    a, b = state.buses[ln["from_bus"]], state.buses[ln["to_bus"]]
    return a["x"] + (b["x"] - a["x"]) * frac, a["y"] + (b["y"] - a["y"]) * frac


def _place(state, text: str, parsed: dict):
    t = text.lower()
    h = int(hashlib.md5(text.encode()).hexdigest(), 16)
    frac = 0.3 + (h % 40) / 100.0  # deterministic spot along the line
    bus_id = next((bid for bid, names in _BUS_ALIASES.items() if any(n in t for n in names)), None)
    if bus_id:
        if parsed["kind"] in ("water_at_substation", "outage"):
            b = state.buses[bus_id]
            return b["x"] + 60, b["y"] + 60
        cands = [l for l, ln in state.lines.items() if bus_id in (ln["from_bus"], ln["to_bus"])]
        lid = max(cands, key=lambda l: _risk_score(state, l))
        # start near the named bus
        f = 0.25 if state.lines[lid]["from_bus"] == bus_id else 0.75
        return _point_on_line(state, lid, f)
    zone = next((z for z, rx in _ZONE_WORDS.items() if re.search(rx, t)), None)
    cands = [l for l, ln in state.lines.items() if zone is None or ln["zone"] == zone]
    lid = max(cands, key=lambda l: _risk_score(state, l))
    return _point_on_line(state, lid, frac)


# --------------------------------------------------------------------------- public
def make_report(state, text: str, x: float | None = None, y: float | None = None, use_llm: bool = True) -> dict:
    parsed = (parse_llm(text) if use_llm else None) or parse_rules(text)
    if x is None or y is None:
        x, y = _place(state, text, parsed)
    rep = {
        "id": f"RPT{len(state.reports) + 1}",
        "t": time.time(),
        "text": text,
        "kind": parsed["kind"],
        "location_hint": parsed.get("location_hint", ""),
        "severity": int(parsed.get("severity", 2)),
        "parser": parsed.get("parser", "rules"),
        "line": None,
        "bus": None,
    }
    if parsed["kind"] in ("water_at_substation", "outage"):
        b = nearest_bus(state, x, y)
        rep["bus"] = b["id"]
        rep["x"], rep["y"] = b["x"] + 80, b["y"] + 80
        rep["snapped_to"] = f"{b['id']} {b['name']}"
    else:
        d, lid, cx, cy = nearest_line(state, x, y)
        rep["line"] = lid
        rep["x"], rep["y"] = round(cx, 1), round(cy, 1)
        rep["snapped_to"] = f"line {lid} ({d:.0f} m away)"
    return rep


def add_report(state, text: str, x=None, y=None) -> dict:
    with state.lock:
        rep = make_report(state, text, x, y)
        state.reports.append(rep)
        return next(r for r in state.reports_view() if r["id"] == rep["id"])


SEED_REPORTS = {
    "Wind Storm": [
        "Tree limb whipping into the wires along Summit ridge road, sparking every few seconds",
        "Power out and lights flickering on the side streets in the Mill District",
        "Smell of smoke near the Valley Town school, can't see flames",
    ],
    "Coastal Flood": [
        "Water up to the fence at the Harbor substation, still humming",
    ],
}


def seed_reports_for(state):
    # Seeds never call the LLM so scenario switching stays instant.
    for text in SEED_REPORTS.get(state.scenario, []):
        state.reports.append(make_report(state, text, use_llm=False))
