"""Rule-based fallback planner. Same output shape and log style as the LLM team, so the demo always works.

1. Each "agent" reads the physics and writes findings (templated).
2. Coordinator repairs a plan greedily, one violation at a time (shows rejected attempts with exact violations).
3. Small brute force over candidate actions (cap ~500 sims) for alternatives.
4. Rank safe plans by unserved MW, then number of actions.
"""
from __future__ import annotations

import itertools
import math
import os
import re
import time

from ..physics import simulate
from .tools import JobLog, summarize_result, verify_output

try:
    from ..la import data as D
except Exception:  # noqa: BLE001
    D = None

PACE_S = float(os.environ.get("MOCK_PACE", "0.12"))
MAX_SIMS = 500

ASSUMPTIONS = {
    "Normal": ["Conditions stay as forecast for the next 3 hours"],
    "Heat Wave": ["Load peaks at +35% this afternoon", "Wind stays below 1 m/s until evening"],
    "Wind Storm": ["Gusts stay at or below 25 m/s for the next 3 hours", "Tree positions from the last vegetation survey are accurate"],
    "Coastal Flood": ["Surge crests at 2.5 m and does not rise further", "Hospital pad (3 m) and backup generators hold"],
}


def _site(state, line_id: str):
    """Cached site-agent context for an LA line (photos + Gemini's description if one was made), else None."""
    if not hasattr(state, "grid"):
        return None
    try:
        from site_agent import agent as SA

        return SA.compact(SA.describe(line_id, state.eid, t=state.t_utc.isoformat(), use_llm=False, fetch=False))
    except Exception:  # noqa: BLE001  context only: never let it break planning
        return None


def _site_phrase(s) -> str:
    """One short clause from the site context, or ''."""
    if not s:
        return ""
    if s.get("line_summary"):
        return f" Site agent (aerial photos): {s['line_summary']}"
    seen = [p for p in s["photos"] if p.get("seen")]
    edge = [p["photo"] for p in seen if p.get("wildland_urban_edge")]
    if edge:
        return f" Site agent: homes meet brush at photo {', '.join(map(str, edge))}."
    return ""


def _greenery(state, line_id: str):
    """Vegetation near an LA line from its cached aerial photos, or None (no photos yet, or not the LA grid)."""
    if not hasattr(state, "grid"):
        return None
    try:
        from site_agent import greenery as G

        return G.line_greenery(line_id)
    except Exception:  # noqa: BLE001  context only: never let it break planning
        return None


def _trees(state, line_id: str):
    """Trees counted near an LA line's wires on Vexcel imagery, or None (not counted, not covered, not the LA grid)."""
    if not hasattr(state, "grid"):
        return None
    try:
        from ..la import trees as T

        return T.line_trees(line_id)
    except Exception:  # noqa: BLE001  context only: never let it break planning
        return None


def _greenery_phrase(state, ids) -> str:
    """Names the lines with vegetation or many trees close to the wires: the ones to act on first."""
    green = [l for l in ids if (_greenery(state, l) or {}).get("level") == "high"]
    treed = sorted((l for l in ids if (_trees(state, l) or {}).get("many")), key=lambda l: -_trees(state, l)["trees"])
    out = f" Aerial photos show vegetation close to the wires on {', '.join(green[:6])}." if green else ""
    if treed:
        out += " Vexcel imagery shows the most trees near the wires on " + ", ".join(f"{l} ({_trees(state, l)['trees']})" for l in treed[:4]) + "."
    return out


def _cut_phrase(state, line_id: str) -> str:
    """What cutting one LA line costs, by kind of building behind it. '' off the LA grid."""
    if not hasattr(state, "cut_impact"):
        return ""
    from ..la import buildings as B

    cut = state.cut_impact(line_id)
    if not cut["by_kind"]:
        return " No listed building loses grid power."
    homes = f" ({cut['homes']:,} homes)" if cut.get("homes") else ""
    return f" It feeds {B.words(cut['by_kind'])}{homes}, which would lose grid power."


def _cut_order(state, risk: float, line_id: str):
    """Order for single-line cuts: vegetation near the wires first, then the cut that costs the least critical
    buildings, then the higher fire index."""
    green = (_greenery(state, line_id) or {}).get("level") == "high" or bool((_trees(state, line_id) or {}).get("many"))
    score = state.cut_impact(line_id)["score"] if hasattr(state, "cut_impact") else 0
    return (not green, score, -risk)


class _Planner:
    def __init__(self, state, log: JobLog, pace: float):
        self.state, self.log, self.pace = state, log, pace
        self.sims = 0
        self.max_sims = 150 if hasattr(state, "grid") else MAX_SIMS  # LA sim ~0.07 s (profiled), so 3-action combos fit
        self.findings: dict[str, list] = {}

    def say(self, agent, msg):
        self.log(agent, msg)
        if self.pace:
            time.sleep(self.pace)

    def sim(self, actions):
        self.sims += 1
        if hasattr(self.state, "simulate"):  # LA replay state carries its own engine
            return self.state.simulate(actions)
        return simulate(self.state, actions)

    def find(self, agent, target, issue, evidence, severity):
        self.findings.setdefault(agent, []).append({"target": target, "issue": issue, "evidence": evidence, "severity": severity})

    # ------------------------------------------------------------------ phase 1+2: agents read the physics
    def analyze(self, base):
        st, sim = self.state, base
        L = sim["lines"]
        closed = [l for l in L if L[l]["energized"]]

        self.say("coordinator", f"scenario '{st.scenario}': dispatching cause team + reports agent")
        # heat
        hot = sorted(closed, key=lambda l: -L[l]["temp_c"])[:2]
        for l in hot:
            ln = st.lines[l]
            wx = st.weather[ln["zone"]]
            sev = 3 if L[l]["temp_c"] > ln["max_temp_c"] else (2 if L[l]["temp_c"] > 45 else 1)
            self.find("heat_agent", l, "conductor heating", f"{L[l]['temp_c']} C at {L[l]['amps']:.0f} A, air {wx['air_temp_c']} C, sag {L[l]['sag_m']} m", sev)
        h = self.findings["heat_agent"][0]
        self.say("heat_agent", f"{h['target']} hottest conductor: {h['evidence']}")
        # wind
        if any("gust_ms" in L[l] for l in closed):
            for l in sorted(closed, key=lambda l: -L[l].get("gust_ms", 0))[:3]:
                if L[l]["gust_ms"] >= 12:
                    self.find("wind_agent", l, "strong gusts on the line", f"gust {L[l]['gust_ms']} m/s at the worst tower; fire risk {L[l]['fire_risk']:.2f}", 3 if L[l]["gust_ms"] >= 20 else 2)
        blown = sorted(closed, key=lambda l: -abs(L[l]["blowout_m"]))[:3]
        for l in blown:
            wx = st.weather[st.lines[l]["zone"]]
            if abs(L[l]["blowout_m"]) > 0.5:
                self.find("wind_agent", l, "conductor blowout", f"gust {wx['gust_ms']} m/s swings conductor {L[l]['swing_deg']} deg, {abs(L[l]['blowout_m']):.1f} m sideways; clearance {L[l]['clearance_m']} m", 3 if L[l]["clearance_m"] < 3 else 2)
        if self.findings.get("wind_agent"):
            for f in self.findings["wind_agent"][:2]:
                self.say("wind_agent", f"{f['target']}: {f['evidence']}")
        else:
            self.say("wind_agent", "winds too light to swing conductors meaningfully")
        # atmosphere
        ridge = [l for l in closed if st.lines[l]["zone"] in ("ridge", "mountain")]
        if ridge:
            elev = sum(st.lines[l]["mid_elevation_m"] for l in ridge) / len(ridge)
            rho = math.exp(-elev / 8500)
            self.find("atmosphere_agent", "ridge", "thin air", f"mean ridge line elevation {elev:.0f} m -> air density {rho * 100:.0f}% of sea level, convective cooling reduced", 1)
            self.say("atmosphere_agent", f"ridge lines at ~{elev:.0f} m: air density {rho * 100:.0f}% of sea level, they shed heat {100 - rho * 100:.0f}% slower")
        # water
        flooded = [b for b, v in sim["buses"].items() if v["flooded"]]
        if flooded:
            for b in flooded:
                bb = st.buses[b]
                self.find("water_agent", b, "substation flooding", f"water {st.weather[bb['zone']]['flood_level_m']} m vs pad {bb['flood_threshold_m'] + bb['elevation_m']:.1f} m", 3)
            self.say("water_agent", f"flooding at {', '.join(st.buses[b]['name'] + ' (' + b + ')' for b in flooded)}: water above the equipment pad")
        else:
            self.say("water_agent", f"coast water level {st.weather['coast']['flood_level_m']} m, all substation pads dry")
        # reports
        reps = st.reports_view()
        corr = [r for r in reps if r["corroborated"]]
        for r in reps:
            self.find("reports_agent", r.get("line") or r.get("bus"), r["kind"], f"\"{r['text'][:60]}\" corroborated={r['corroborated']}", r["severity"] + (1 if r["corroborated"] else 0))
        if reps:
            msg = f"{len(reps)} ground reports, {len(corr)} corroborated by physics"
            if corr:
                msg += ": " + "; ".join(f"{r['kind']} on {r.get('line') or r.get('bus')}" for r in corr)
            self.say("reports_agent", msg)
        else:
            self.say("reports_agent", "no ground reports yet")

        self.say("coordinator", "cause findings in; dispatching outcome team")
        # brownout
        loaded = sorted(closed, key=lambda l: -L[l]["loading_pct"])[:2]
        for l in loaded:
            self.find("brownout_agent", l, "line loading", f"{L[l]['loading_pct']}% of rating ({L[l]['flow_mw']:.0f} MW)", 3 if L[l]["loading_pct"] > 100 else (2 if L[l]["loading_pct"] > 70 else 1))
        tot = sim["totals"]
        self.say("brownout_agent", f"{tot['unserved_mw']} MW unserved; heaviest line {loaded[0]} at {L[loaded[0]]['loading_pct']}%")
        # fire
        fiery = sorted([l for l in closed if L[l]["fire_risk"] >= 0.3], key=lambda l: -L[l]["fire_risk"])
        for l in fiery:
            self.find("fire_agent", l, "ignition risk", f"fire index {L[l]['fire_risk']}, clearance {L[l]['clearance_m']} m", 3 if L[l]["fire_risk"] >= 0.7 else 2)
        if fiery:
            self.say("fire_agent", "ignition risk on " + ", ".join(f"{l} ({L[l]['fire_risk']:.2f})" for l in fiery[:4]))
            if hasattr(st, "grid"):  # consequence layer (context only): what is downwind of the riskiest line
                try:
                    from ..la import replay

                    ov = replay.hour(st.eid, t=st.t_utc.isoformat())["over_alarm"]
                    top = next((x for x in ov if x["id"] == fiery[0]), ov[0] if ov else None)
                    if top and top.get("downwind"):
                        dw = top["downwind"]
                        self.say("fire_agent", f"if {top['id']} sparks, the wind carries fire toward ~{dw['people']:,} people"
                                               + (f", {dw['hospitals']} hospital(s)" if dw["hospitals"] else "")
                                               + (f", {dw['water_plants']} water plant(s)" if dw["water_plants"] else "") + " within 10 km")
                except Exception:  # noqa: BLE001
                    pass
                for l in fiery[:2]:  # what is on the ground (site agent, cached Gemini description of aerial photos)
                    s = _site(st, l)
                    if s and s.get("line_summary"):
                        self.say("fire_agent", f"{l} on the ground: {s['line_summary']}")
                        for d in s["disagreements"][:1]:
                            self.say("fire_agent", f"{l} data check: {d}")
                    if s:
                        poor = [p["photo"] for p in s["photos"] if p.get("access") in ("limited", "none visible")]
                        if poor:
                            self.say("wind_agent", f"{l}: road access limited at photo {', '.join(map(str, poor))}; stage patrols "
                                                   "at the reachable stretches")
        else:
            self.say("fire_agent", "fire weather not present (humidity or wind too low)")
        # cascade
        for w in sim["n1_warnings"]:
            self.find("cascade_agent", w.split()[1].rstrip(","), "N-1", w, 2)
        self.say("cascade_agent", sim["n1_warnings"][0] if sim["n1_warnings"] else "N-1 secure: no single trip overloads another line")
        # flood
        fl_en = [b for b in flooded if sim["buses"][b]["energized"]]
        if fl_en:
            for b in fl_en:
                self.find("flood_agent", b, "energized in floodwater", "shock + equipment failure hazard", 3)
            self.say("flood_agent", f"{', '.join(fl_en)} energized in floodwater; hospital B10 pad still dry")
        else:
            self.say("flood_agent", "no energized equipment in water")

    # ------------------------------------------------------------------ candidates
    def candidates(self, base):
        st, L = self.state, base["lines"]
        cands = []

        def add(action, why, raised_by, label):
            key = (action["type"], action["target"], action.get("mw"))
            if key not in {(c["action"]["type"], c["action"]["target"], c["action"].get("mw")) for c in cands}:
                cands.append({"action": action, "why": why, "raised_by": raised_by, "label": label})

        overloaded = [l for l in L if L[l]["energized"] and L[l]["loading_pct"] > 100]
        fire_lines = []
        for v in base["violations"]:
            m = re.match(r"Line (\w+): fire risk ([\d.]+)", v)
            if m:
                fire_lines.append((m.group(1), float(m.group(2)), v))
        if fire_lines:
            # Only sub-transmission lines are violations (bulk 220-500 kV lines are patrol/watch items, see la/state.py).
            ids_all = [l for l, _, _ in sorted(fire_lines, key=lambda x: -x[1])]
            if len(ids_all) >= 2:
                add({"type": "deenergize_lines", "target": "all sub-transmission lines over fire alarm", "targets": ids_all},
                    f"PSPS-style shutoff of all {len(ids_all)} sub-transmission line(s) above the calibrated fire alarm "
                    f"(worst {max(rk for _, rk, _ in fire_lines):.2f}); bulk import corridors stay on and are patrolled."
                    + _greenery_phrase(st, ids_all),
                    ["fire_agent", "wind_agent"], f"PSPS: de-energize all {len(ids_all)} flagged sub-transmission lines")
            by_zone = {}
            for l, rk, _ in fire_lines:
                by_zone.setdefault(st.lines[l]["zone"], []).append((rk, l))
            if len(by_zone) > 1:
                for z, items in sorted(by_zone.items(), key=lambda kv: -len(kv[1])):
                    ids = [l for _, l in sorted(items, reverse=True)]
                    if len(ids) < 2:
                        continue  # a single line is covered by the individual open_line candidates below
                    add({"type": "deenergize_lines", "target": f"{z} sub-transmission lines over fire alarm", "targets": ids},
                        f"PSPS-style shutoff of the {len(ids)} {z} sub-transmission line(s) above the fire alarm (worst {max(items)[0]:.2f})."
                        + _greenery_phrase(st, ids),
                        ["fire_agent", "wind_agent"], f"de-energize {len(ids)} {z} sub-transmission lines")
            for l, rk, v in sorted(fire_lines, key=lambda x: _cut_order(st, x[1], x[0]))[:3]:
                add({"type": "open_line", "target": l}, f"{v.split(': ', 1)[1]}; de-energize to remove the ignition source."
                    + _greenery_phrase(st, [l]) + _cut_phrase(st, l) + _site_phrase(_site(st, l)),
                    ["fire_agent", "wind_agent"], f"de-energize {l}")
        for v in base["violations"]:
            m = re.match(r"Line (\w+): clearance ([\d.]+) m .*\((.*)\)", v)
            if m:
                l, clr, cause = m.groups()
                wind = "wind" in cause
                add({"type": "open_line", "target": l},
                    f"{l} comes within {clr} m of trees ({cause}); de-energize it to remove the ignition source.",
                    ["fire_agent", "wind_agent"] if wind else ["heat_agent", "fire_agent"], f"de-energize {l}")
                continue
            m = re.match(r"Line (\w+): conductor ([\d.]+) C", v)
            if m:
                l, t = m.groups()
                add({"type": "open_line", "target": l}, f"{l} conductor at {t} C is above its 75 C limit and will anneal.", ["heat_agent", "brownout_agent"], f"de-energize {l}")
                continue
            m = re.match(r"Bus (\w+) \((.*?)\): flooded", v)
            if m:
                b, name = m.groups()
                add({"type": "deenergize_bus", "target": b},
                    f"{name} substation is in floodwater; de-energize before equipment fails and the yard becomes a shock hazard.",
                    ["flood_agent", "water_agent"], f"isolate flooded {name}")
        for l in st.operator_opened:
            if st.lines[l]["status"] == "open":
                why = f"Re-close {l} to restore the parallel path" + (f" and unload {', '.join(overloaded)}." if overloaded else ".")
                add({"type": "close_line", "target": l}, why, ["cascade_agent", "brownout_agent"], f"re-close {l}")
        for l, ln in st.lines.items():
            if ln["tie"] and ln["status"] == "open":
                a, b = st.buses[ln["from_bus"]]["name"], st.buses[ln["to_bus"]]["name"]
                add({"type": "close_line", "target": l}, f"Close normally-open tie {l} ({a} - {b}) to add a second path.", ["cascade_agent", "brownout_agent"], f"close tie {l}")
        hot = [l for l in L if L[l]["energized"] and L[l]["temp_c"] > st.lines[l]["max_temp_c"]]
        for l in overloaded:
            add({"type": "open_line", "target": l}, f"{l} is at {L[l]['loading_pct']:.0f}% of rating; open it before it trips on its own.", ["brownout_agent", "cascade_agent"], f"open {l}")
        for l in overloaded + hot:
            for mw in (20, 40):
                for b in (st.lines[l]["from_bus"], st.lines[l]["to_bus"]):
                    bb = st.buses[b]
                    if not bb["critical"] and bb["load_mw"] * st.load_multiplier >= mw:
                        add({"type": "shed_load", "target": b, "mw": mw}, f"Shed {mw} MW of non-critical load at {bb['name']} ({b}) to unload {l}.", ["brownout_agent", "heat_agent"] if l in hot else ["brownout_agent"], f"shed {mw} MW at {bb['name']}")
        g2 = st.gens.get("G2")
        if g2 and g2["dispatch_mw"] < g2["gen_max_mw"] and (overloaded or any("conductor" in v for v in base["violations"])):
            add({"type": "dispatch_gen", "target": "G2", "mw": g2["gen_max_mw"]},
                f"Raise {g2['name']} to {g2['gen_max_mw']:.0f} MW so less power has to cross the valley.", ["brownout_agent", "atmosphere_agent"], f"raise {g2['name']} to {g2['gen_max_mw']:.0f} MW")
        return cands[:14]

    # ------------------------------------------------------------------ planning
    def greedy(self, base, cands):
        """Coordinator-style repair: fix the first violation, re-test, read the exact violation, revise."""
        plan, history = [], []
        r = base
        for attempt in range(1, 4):
            fix = None
            for v in r["violations"]:
                fix = self._fix_for(v, cands, plan) or self._relief_for(v, r, plan)
                if fix:
                    break
            if not fix:
                break
            plan = plan + [fix]
            r = self.sim([c["action"] for c in plan])
            history.append(r)
            labels = ", ".join(c["label"] for c in plan)
            if r["is_safe"]:
                self.say("coordinator", f"plan A attempt {attempt}: [{labels}] -> physics: SAFE, {r['totals']['unserved_mw']} MW unserved")
                return plan, attempt, r
            self.say("coordinator", f"plan A attempt {attempt}: [{labels}] -> REJECTED: {r['violations'][0]}")
        return plan, len(history), r

    def _relief_for(self, violation, r, plan):
        """A plan created a NEW overload (e.g. PSPS rerouted flow): shed just enough non-critical load at the
        overloaded line's heavier endpoint. Only used when no pre-built candidate applies."""
        m = re.match(r"Line (\w+): (loading|conductor)", violation)
        st = self.state
        if not m or m.group(1) not in st.lines:
            return None
        lid = m.group(1)
        L = r["lines"][lid]
        excess = abs(L["flow_mw"]) * max(0.0, 1 - 90.0 / max(L["loading_pct"], 1e-6))  # bring it to ~90%
        if excess <= 0:
            excess = abs(L["flow_mw"]) * 0.15  # temperature-limited: relieve ~15%
        used = {c["action"]["target"] for c in plan if c["action"]["type"] == "shed_load"}
        if hasattr(st, "grid"):
            # LA grid: pick the non-critical load whose shedding relieves this line most (PTDF sensitivity)
            g = st.grid
            top, *_ = st._topology([c["action"] for c in plan])
            H = g.ptdf(top)[0]
            li = g.lix[lid]
            sign = 1.0 if L["flow_mw"] >= 0 else -1.0
            best = None
            for b, info in st.buses.items():
                served = r["buses"][b]["served_mw"]
                if b in used or served < 5:
                    continue
                # substations hosting a hospital/water plant: rotate up to half their load, never the critical feeder
                sheddable = served * (0.5 if info["critical"] else 1.0)
                # shedding 1 MW = +1 MW injection at b -> flow changes by +H; |flow| drops by -sign*H
                gain = -sign * H[li, g.bix[b]]  # MW of line relief per MW shed
                key = (round(gain, 2), not info["critical"], sheddable)
                if gain > 0.05 and (best is None or key > best[0]):
                    best = (key, b, sheddable)
            if best is None:
                # no load relieves it: the flow is pushed by a fixed import point -> redispatch that import
                P, _ = g.injections(st.ldwp, st.sce, top, None, getattr(st, "_last_imp", None))
                _, _, slack_of, roots = g.ptdf(top)
                slacks = set(slack_of.values())
                cand = []
                for imp_rec in D.grid()["imports"]:
                    bi = g.bix[imp_rec["bus"]]
                    inj = float(P[0, bi])
                    if bi in slacks or inj <= 1 or not top.bus_on[bi]:
                        continue
                    gain = H[li, bi] * sign  # cutting this import by 1 MW reduces |flow| by `gain` MW
                    if gain > 0.05:
                        cand.append((gain, imp_rec, inj))
                if not cand:
                    return None
                gain, imp_rec, inj = max(cand, key=lambda c: c[0])
                new = float(round(max(0.0, inj - excess * 1.15 / gain), 0))
                return {"action": {"type": "dispatch_gen", "target": imp_rec["id"], "mw": new},
                        "why": f"The shutoff pushes imports from {imp_rec['name'].replace('Import @ ', '')} through {lid} ({L['loading_pct']:.0f}%); "
                               f"redispatch that import point from {inj:.0f} to {new:.0f} MW and bring the rest in through the main import bus.",
                        "raised_by": ["cascade_agent", "brownout_agent"], "label": f"cut import at {imp_rec['name'].replace('Import @ ', '')} to {new:.0f} MW"}
            key, b, served = best
            gain = key[0]
            mw = float(round(min(served, excess * 1.15 / gain), 0)) or 1.0
        else:
            ends = [st.lines[lid]["from_bus"], st.lines[lid]["to_bus"]]
            opts = [(r["buses"][b]["served_mw"], b) for b in ends if not st.buses[b]["critical"] and b not in used and r["buses"][b]["served_mw"] > 1]
            if not opts:
                return None
            served, b = max(opts)
            mw = float(round(min(served, excess * 1.2), 0)) or 1.0
        name = st.buses[b]["name"]
        crit = st.buses[b].get("critical_name") if st.buses[b]["critical"] else None
        return {"action": {"type": "shed_load", "target": b, "mw": mw},
                "why": f"The shutoff reroutes power onto {lid} ({L['loading_pct']:.0f}%); rotate {mw:.0f} MW of non-critical load at {name} to bring it back within rating"
                       + (f" ({crit}'s feeder stays on)." if crit else "."),
                "raised_by": ["brownout_agent", "cascade_agent"], "label": f"shed {mw:.0f} MW at {name}"}

    def _fix_for(self, violation, cands, plan):
        used = {(c["action"]["type"], c["action"]["target"]) for c in plan}
        m = re.match(r"(Line|Bus) (\w+)", violation)
        if not m:
            return None
        target = m.group(2)
        pref = []
        if "loading" in violation or "conductor" in violation:
            pref = [c for c in cands if c["action"]["type"] == "close_line" and c["action"]["target"] in self.state.operator_opened]
            pref += [c for c in cands if c["action"]["type"] == "dispatch_gen"]
            pref += [c for c in cands if c["action"]["type"] == "shed_load"]
            pref += [c for c in cands if c["action"]["type"] == "close_line"]
        pref += [c for c in cands if target in (c["action"].get("targets") or [])]
        pref += [c for c in cands if c["action"]["target"] == target]
        for c in pref:
            if (c["action"]["type"], c["action"]["target"]) not in used:
                return c
        return None

    def brute_force(self, cands):
        results = []
        for k in (1, 2, 3):
            for combo in itertools.combinations(cands, k):
                if self.sims >= MAX_SIMS:
                    return results
                if self.sims >= self.max_sims:
                    return results
                lines_touched = [c["action"]["target"] for c in combo if c["action"]["type"] in ("open_line", "close_line")]
                if len(lines_touched) != len(set(lines_touched)):
                    continue
                r = self.sim([c["action"] for c in combo])
                results.append((list(combo), r))
        return results

    # ------------------------------------------------------------------ output
    def plan_json(self, combo, r, attempts):
        tot = r["totals"]
        parts = []
        parts.append("keeps every customer on" if tot["unserved_mw"] < 0.05 else f"{tot['unserved_mw']:.0f} MW (~{tot['customers_out']:,} customers) without power")
        for b in tot["critical_on_backup"]:
            bb = self.state.buses[b]
            parts.append(f"{bb['name']} runs on backup ({bb['backup_hours']:.0f} h)")
        opened = [c["action"]["target"] for c in combo if c["action"]["type"] == "open_line"]
        if opened:
            parts.append(f"{', '.join(opened)} out of service until crews patrol them")
        if r["n1_warnings"]:
            parts.append("N-1 watch: " + r["n1_warnings"][0])
        if r.get("watch"):
            parts.append(f"{len(r['watch'])} bulk 220-500 kV line(s) above the fire alarm stay energized for patrol/monitoring")
        conf = 0.93 - 0.05 * len(combo) - min(0.3, tot["unserved_mw"] / 200) - 0.04 * len(r["n1_warnings"])
        assumptions = list(ASSUMPTIONS.get(self.state.scenario, ["Reanalysis weather for this hour holds for the next few hours",
                                                                  "Line ratings and impedances are modeled from voltage class, not utility data"]))
        if self.state.reports:
            assumptions.append("Ground report locations are accurate to about 100 m")
        title = ", ".join(c["label"] for c in combo)
        return {
            "title": title[0].upper() + title[1:],
            "steps": [{"action": c["action"], "why": c["why"], "raised_by": c["raised_by"]} for c in combo],
            "result": summarize_result(r),
            "tradeoffs": (lambda t: t[0].upper() + t[1:])("; ".join(parts)) + ".",
            "confidence": round(max(0.35, conf), 2),
            "assumptions": assumptions,
            "attempts": attempts,
        }

    def disagreements(self, best_combo):
        st, out = self.state, []
        if not best_combo:
            return out
        acts = [c["action"] for c in best_combo]
        r_best = self.sim(acts)
        L = r_best["lines"]
        risky = [l for l in L if L[l]["energized"] and L[l]["fire_risk"] >= 0.7 and not L[l]["violations"]]
        for l in risky[:2]:
            r2 = self.sim(acts + [{"type": "open_line", "target": l}])
            if len(acts) >= 3:
                obj = "the plan is already at 3 switching actions"
            elif r2["violations"]:
                obj = f"opening it causes: {r2['violations'][0]}"
            elif r2["totals"]["unserved_mw"] > r_best["totals"]["unserved_mw"] + 0.5:
                obj = f"it would drop {r2['totals']['unserved_mw'] - r_best['totals']['unserved_mw']:.0f} MW more load"
            elif r2["gens"].get("G2", 0) < r_best["gens"].get("G2", 0) - 0.5 or r2["n1_warnings"] != r_best["n1_warnings"]:
                obj = "it weakens the network (" + (r2["n1_warnings"][0] if r2["n1_warnings"] else "cuts off Ridge Hydro") + ")"
            else:
                obj = "physics shows no clearance violation, so the outage isn't justified"
            out.append(
                f"fire_agent wanted {l} opened too (fire index {L[l]['fire_risk']:.2f}); brownout_agent objected: {obj}. "
                f"Resolved: keep {l} energized ({L[l]['clearance_m']} m clearance) and send a patrol."
            )
        if any(c["action"]["type"] == "deenergize_bus" for c in best_combo) and "B10" in r_best["buses"] and not hasattr(st, "grid"):
            hosp = r_best["buses"]["B10"]
            out.append(
                "flood_agent wanted all coastal substations isolated; brownout_agent objected that Mercy Hospital loses its coastal feeds. "
                + ("Resolved: hospital stays served through C2 from Central, verified by physics." if not hosp["on_backup"] else "Resolved: hospital moves to backup generation.")
            )
        if r_best.get("watch"):
            out.append(f"fire_agent wanted the {len(r_best['watch'])} bulk 220-500 kV line(s) above the fire alarm de-energized too; "
                       "brownout_agent objected: they are the import corridors feeding the basin and switching them off risks a regional blackout. "
                       "Resolved: keep them energized, patrol and monitor them (listed in the bulletin), as utilities do during a PSPS.")
        if any(c["action"]["type"] == "close_line" and c["action"]["target"] in st.operator_opened for c in best_combo):
            out.append("cascade_agent pushed to re-close the operator-opened line; coordinator flagged it for operator confirmation since it reverses a manual switch.")
        return out

    def buildings(self, base) -> None:
        """Buildings agent (LA): compare what each flagged line feeds, by the priority ladder, and say which cuts are
        cheap and which reach a hospital. Reads backend/la/buildings.py, so a CoStar file widens it."""
        st = self.state
        if not hasattr(st, "cut_impact"):
            return
        from ..la import buildings as B

        info = B.summary()
        src = (f"CoStar ({info['costar']:,} properties, {info['homes']:,} apartment homes) and OpenStreetMap" if info["costar"]
               else "OpenStreetMap hospitals and water plants (the CoStar kit is not loaded)")
        self.say("buildings_agent", f"building records: {info['n']:,} from {src}")
        flagged = [m.group(1) for v in base["violations"] if (m := re.match(r"Line (\w+): fire risk", v))]
        cuts = {l: st.cut_impact(l) for l in flagged}
        ranked = sorted(flagged, key=lambda l: (-cuts[l]["score"], -cuts[l]["customers"], l))
        for l in ranked:
            c = cuts[l]
            if not c["n"]:
                continue
            top = next(t for t in c["ladder"] if t["n"])
            self.find("buildings_agent", l, f"cutting it reaches tier {top['tier']}: {top['name'].lower()}",
                      B.words(c["by_kind"]) + (f" ({c['homes']:,} homes)" if c.get("homes") else "") + f" lose grid power; {c['customers']:,} customers",
                      3 if top["tier"] <= 2 else 2 if top["tier"] == 3 else 1)
        free = [l for l in flagged if not cuts[l]["n"]]
        if free:
            self.find("buildings_agent", "cut first", "these lines feed no listed building",
                      f"{len(free)} of {len(flagged)} flagged lines: " + ", ".join(free[:8]) + (" and more" if len(free) > 8 else ""), 1)
        self.find("buildings_agent", "records", "where the building list comes from", f"{info['n']:,} buildings from {src}", 1 if info["costar"] else 2)
        hit = [l for l in ranked if cuts[l]["n"]]
        self.say("buildings_agent", (f"{len(hit)} of {len(flagged)} flagged lines feed a listed building; worst is {hit[0]}" if hit
                                     else f"none of the {len(flagged)} flagged lines feeds a listed building"))

    def feedback(self, base, cands) -> tuple[list, list[dict]]:
        """Coordinator (LA): act on what the operator said when rejecting earlier plans. Returns extra candidate
        combinations to offer, and one line per piece of feedback saying what was done with it."""
        st = self.state
        if not hasattr(st, "cut_impact"):
            return [], []
        from ..la import feedback as F

        fb = F.summary()
        if not fb["n"]:
            return [], []
        self.say("coordinator", f"operator feedback on record from {fb['n']} rejection{'s' if fb['n'] != 1 else ''}: "
                                + "; ".join(r["text"].lower() for r in fb["reasons"]))
        given = {r["id"] for r in fb["reasons"]}
        used, extra = [], []
        flagged = [m.group(1) for v in base["violations"] if (m := re.match(r"Line (\w+): fire risk", v))]
        spare_for = {"too_many_customers": lambda c: c["customers"] > 0, "critical_site": lambda c: c["n"] > 0}
        for reason, hits in spare_for.items():
            if reason not in given:
                continue
            text = F.REASONS[reason]
            keep_on = [l for l in flagged if hits(st.cut_impact(l))]
            off = [l for l in flagged if l not in keep_on]
            if not flagged:
                used.append({"reason": reason, "text": text, "applied": "No line is at high fire risk this hour, so there was nothing to change."})
            elif not keep_on:
                used.append({"reason": reason, "text": text, "applied": "Every plan this hour already avoids it."})
            elif not off:
                used.append({"reason": reason, "text": text, "applied": "No smaller shut-off exists: every line at high risk feeds customers or a critical site."})
            else:
                what = "cuts no customers" if reason == "too_many_customers" else "puts no listed building on backup"
                action = {"type": "deenergize_lines", "target": f"the lines whose shut-off {what}", "targets": off}
                extra.append([{"action": action, "raised_by": ["buildings_agent", "fire_agent"], "label": f"smaller shut-off: {len(off)} lines, from your feedback",
                               "why": f"You rejected an earlier plan because: {text.lower()}. This turns off only the {len(off)} flagged lines whose shut-off {what} "
                                      f"and leaves {len(keep_on)} on ({', '.join(keep_on[:6])}{' and more' if len(keep_on) > 6 else ''})."}])
                used.append({"reason": reason, "text": text, "applied": f"Added a smaller shut-off: {len(off)} lines off, {len(keep_on)} left on. The physics check decides whether it is safe."})
        for reason in ("not_enough", "wrong_lines", "other"):
            if reason in given:
                used.append({"reason": reason, "text": F.REASONS[reason], "applied": "Kept on record. The rule-based planner cannot act on it; the AI team reads it."})
        for n in fb["notes"]:
            used.append({"reason": "note", "text": n, "applied": "Kept on record for the AI team and shown here."})
        return extra, used

    def stations(self):
        """Wind agent (LA): check the weather the model ran on against what the airport stations measured this hour."""
        from ..la import sensors

        try:
            chk = sensors.check(self.state, offline=True)
        except Exception:  # noqa: BLE001  the station check is context for the operator; it never blocks planning
            return
        if not chk["reporting"]:
            self.say("wind_agent", "no weather station reported this hour: the model's weather is unchecked")
            self.find("wind_agent", "stations", "no weather station reported this hour", "the model's weather could not be checked against a measurement", 2)
            return
        low = [r for r in chk["stations"] if r["flag"] == "model_low"]
        dry = [r for r in chk["stations"] if r["drier"]]
        self.say("wind_agent", f"weather stations: {chk['agree']} of {chk['reporting']} agree with the model's gusts"
                               + (f"; {len(low)} measured stronger gusts than the model used" if low else ""))
        for r in low:
            self.find("wind_agent", r["name"], "measured gusts stronger than the model's",
                      f"station measured {r['measured']['gust_ms']} m/s, the model used {r['model']['gust_ms']} m/s", 3)
        for r in dry:
            self.find("wind_agent", r["name"], "air drier than the model assumed",
                      f"station measured {r['measured']['rh_pct']:.0f}% humidity, the model used {r['model']['rh_pct']:.0f}%", 2)
        if not low and not dry:
            self.find("wind_agent", "stations", "weather stations agree with the model",
                      f"{chk['agree']} of {chk['reporting']} within {chk['tolerance']['gust_ms']:g} m/s on gusts", 1)

    def storm(self) -> tuple[str, dict | None]:
        """Wind agent (LA replays): read the calibrated storm-damage model; returns a situation sentence + crew staging."""
        st = self.state
        if not hasattr(st, "grid"):
            return "", None
        from ..la import replay

        sd = replay.hour(st.eid, t=st.t_utc.isoformat()).get("storm")
        if not sd:
            return "", None
        above = sd["alarm"] is not None and sd["index"] >= sd["alarm"]
        tops = sd["top_lines"][:5]
        self.say("wind_agent", f"storm-damage index {sd['index']:.2f} vs alarm {sd['alarm']:.2f}; "
                               f"{sd['wind_hazard_pct']}% of network under damaging wind, {sd['rain_hazard_pct']}% under heavy rain")
        if tops:
            self.say("wind_agent", "most likely to be knocked down: " + ", ".join(f"{t['id']} ({t['p_damage']:.2f})" for t in tops[:3]))
        self.find("wind_agent", "network", "storm damage " + ("ABOVE alarm" if above else "below alarm"),
                  f"index {sd['index']:.2f} / alarm {sd['alarm']:.2f}", 2 if above else 1)
        self.stations()
        staging = {"storm_index": sd["index"], "alarm": sd["alarm"], "above_alarm": above,
                   "pre_stage_crews_near": [{"line": t["id"], "name": t["name"], "p_damage": t["p_damage"]} for t in tops]}
        sent = (f" Storm-damage index {sd['index']:.2f} is ABOVE its alarm ({sd['alarm']:.2f}): pre-stage restoration crews near "
                + ", ".join(t["id"] for t in tops[:3]) + "." if above and tops else
                f" Storm-damage index {sd['index']:.2f} (alarm {sd['alarm']:.2f}): no crew pre-staging needed yet.")
        return sent, staging

    def run(self) -> dict:
        st = self.state
        if hasattr(st, "grid"):  # LA: start from the shared situation brief
            try:
                from .brief import build_brief

                for h in build_brief(st)["headlines"]:
                    self.say("coordinator", "situation brief: " + h)
            except Exception:  # noqa: BLE001
                pass
        base = self.sim([])
        self.analyze(base)
        self.buildings(base)
        storm_sent, staging = self.storm()
        if base["is_safe"]:
            self.say("coordinator", "do-nothing case passes every physics check; no switching recommended")
            return {
                "situation": f"{st.scenario}: the grid is within limits. No violations, {base['totals']['unserved_mw']} MW unserved."
                + (f" Watch item: {base['n1_warnings'][0]}." if base["n1_warnings"] else "") + storm_sent,
                "plans": [], "crew_staging": staging,
                "disagreements": [],
                "agent_findings": self.findings,
            }

        self.say("coordinator", f"{len(base['violations'])} violations to resolve; building candidate plans")
        cands = self.candidates(base)
        self.say("coordinator", f"{len(cands)} candidate actions: " + ", ".join(c["label"] for c in cands))
        fb_combos, fb_used = self.feedback(base, cands)
        g_plan, g_attempts, g_r = self.greedy(base, cands)

        self.say("coordinator", "screening alternative combinations with the physics engine")
        results = self.brute_force(cands)
        safe = [(c, r) for c, r in results if r["is_safe"]]
        self.say("physics", f"screened {len(results)} combinations ({self.sims} simulations): {len(safe)} safe")

        ranked = sorted(safe, key=lambda cr: (cr[1]["totals"]["unserved_mw"], len(cr[1]["n1_warnings"]), len(cr[0])))
        if g_r["is_safe"]:
            ranked = [(g_plan, g_r)] + ranked
        chosen, seen = [], {}

        def key_of(combo):
            return frozenset((c["action"]["type"], c["action"]["target"], c["action"].get("mw")) for c in combo)

        for combo, r in ranked:
            key = key_of(combo)
            if key in seen:
                continue
            # skip supersets of a chosen plan unless the extra switching buys N-1 security
            if any(k <= key and len(r["n1_warnings"]) >= len(kr["n1_warnings"]) for k, kr in seen.items()):
                continue
            # skip alternatives that drop far more load than the best plan
            if chosen and r["totals"]["unserved_mw"] > chosen[0][1]["totals"]["unserved_mw"] + 60:
                continue
            seen[key] = r
            chosen.append((combo, r))
            if len(chosen) == 3:
                break
        if len(chosen) == 1:  # give the operator at least one alternative to compare against
            for combo, r in ranked:
                if key_of(combo) not in seen and r["totals"]["unserved_mw"] <= chosen[0][1]["totals"]["unserved_mw"] + 60:
                    chosen.append((combo, r))
                    break

        if not chosen:
            # least-bad: never trade a blackout for fewer violations (>10% of load unserved ranks last)
            load = max(base["totals"].get("load_mw", 1.0), 1.0)
            least = min(results or [(g_plan, g_r)], key=lambda cr: (cr[1]["totals"]["unserved_mw"] > 0.1 * load,
                                                                    len(cr[1]["violations"]), cr[1]["totals"]["unserved_mw"]))
            chosen = [least]
            self.say("coordinator", f"NO SAFE OPTION within 3 actions; least-bad plan still has: {least[1]['violations'][0]}")

        plans = []
        for combo, r in chosen:
            attempts = g_attempts if combo is g_plan else 1
            plans.append(self.plan_json(combo, r, attempts))
        for combo in fb_combos[:1]:  # one plan shaped by the operator's feedback, physics-checked like the rest
            p = self.plan_json(combo, self.sim([c["action"] for c in combo]), 1)
            p["from_feedback"] = True
            plans.append(p)
        for p in plans:
            self.say("coordinator", f"proposing: {p['title']} ({'safe' if p['result']['is_safe'] else 'NOT SAFE'}, {p['result']['unserved_mw']} MW unserved)")

        v = base["violations"]
        sit = f"{st.scenario}: {len(v)} physics violations, starting with {v[0].split(':')[0]} ({v[0].split(': ', 1)[1]})."
        corr = [r for r in st.reports_view() if r["corroborated"]]
        sit += (f" {len(corr)} ground report{'s corroborate' if len(corr) != 1 else ' corroborates'} the physics." if corr else "")
        sit += f" Best plan leaves {chosen[0][1]['totals']['unserved_mw']:.0f} MW unserved." + storm_sent
        return {
            "situation": sit, "crew_staging": staging,
            "plans": plans,
            "disagreements": self.disagreements(chosen[0][0]),
            "agent_findings": self.findings,
            "feedback_used": fb_used,
            "stats": {"candidates": len(cands), "screened": len(results), "safe": len(safe), "simulations": self.sims},
        }


def run_mock(state, log: JobLog, reason: str = "no GEMINI_API_KEY", pace: float | None = None) -> dict:
    log("system", f"rule-based planner ({reason})")
    p = _Planner(state, log, PACE_S if pace is None else pace)
    out = p.run()
    out["mode"] = "mock"
    out = verify_output(state, out, log)
    log("coordinator", f"done: {len(out['plans'])} plan(s) ready for operator review")
    return out
