"""Tools the agents call. Plain Python functions returning JSON strings.

They wrap the deterministic physics + in-memory state. `make_tools(state, log, agent)` returns
closures bound to one agent name, so every call lands in the activity log under that agent.
Also holds `verify_output`: the backend re-runs physics on every plan and overwrites its numbers.
"""
from __future__ import annotations

import json
import time

from ..physics import simulate as _phys_simulate
from .brief import brief_text, build_brief


def simulate(state, actions=None):
    """LA replay states carry their own engine; the synthetic region uses physics.simulate."""
    return state.simulate(actions) if hasattr(state, "simulate") else _phys_simulate(state, actions)

TEAMS = {
    "heat_agent": "cause",
    "wind_agent": "cause",
    "atmosphere_agent": "cause",
    "water_agent": "cause",
    "reports_agent": "reports",
    "brownout_agent": "outcome",
    "buildings_agent": "outcome",
    "fire_agent": "outcome",
    "cascade_agent": "outcome",
    "flood_agent": "outcome",
    "coordinator": "coordinator",
    "physics": "physics",
    "system": "system",
}


class JobLog:
    def __init__(self):
        self.entries: list[dict] = []

    def __call__(self, agent: str, message: str):
        self.entries.append({"t": round(time.time(), 2), "agent": agent, "team": TEAMS.get(agent, "system"), "message": message})


# --------------------------------------------------------------------------- data views (pure)
def grid_summary(state) -> dict:
    sim = state.sim
    lines = []
    ids = list(state.lines)
    if len(ids) > 60:  # LA grid: only the 40 most stressed lines (by fire risk, then loading) to keep prompts small
        ids = sorted(ids, key=lambda l: (-sim["lines"][l]["fire_risk"], -sim["lines"][l]["loading_pct"]))[:40]
    for lid in ids:
        ln = state.lines[lid]
        s = sim["lines"][lid]
        lines.append(
            {
                "id": lid,
                "from": ln["from_bus"],
                "to": ln["to_bus"],
                "zone": ln["zone"],
                "status": ln["status"],
                "tie": ln["tie"],
                "flow_mw": s["flow_mw"],
                "loading_pct": s["loading_pct"],
                "temp_c": s["temp_c"],
                "sag_m": s["sag_m"],
                "clearance_m": s["clearance_m"],
                "blowout_m": s["blowout_m"],
                "fire_risk": s["fire_risk"],
                "mid_elevation_m": ln["mid_elevation_m"],
                "violations": s["violations"],
            }
        )
    bus_items = list(state.buses.values())
    if len(bus_items) > 60:
        bus_items = [b for b in bus_items if b["critical"] or sim["buses"][b["id"]]["unserved_mw"] > 0][:40]
    buses = [
        {
            "id": b["id"],
            "name": b["name"],
            "zone": b["zone"],
            "load_mw": round(b["load_mw"] * state.load_multiplier, 1),
            "critical_type": b["critical_type"],
            "elevation_m": b["elevation_m"],
            **{k: sim["buses"][b["id"]][k] for k in ("served_mw", "unserved_mw", "flooded", "energized", "on_backup")},
        }
        for b in bus_items
    ]
    gens = [{**g, "output_mw": sim["gens"][g["id"]]} for g in state.gens.values()]
    return {
        "scenario": state.scenario,
        "lines": lines,
        "buses": buses,
        "generators": gens,
        "totals": sim["totals"],
        "violations": sim["violations"],
        "n1_warnings": sim["n1_warnings"],
        "operator_opened_lines": state.operator_opened,
        "note": "LA lines/buses truncated to the most stressed; electrical values are modeled" if len(state.lines) > 60 else "",
    }


def critical_sites(state) -> list[dict]:
    out = []
    for b in state.buses.values():
        if not b["critical"]:
            continue
        if len(out) >= 40:
            break
        feeds = [l for l, ln in state.lines.items() if b["id"] in (ln["from_bus"], ln["to_bus"])]
        s = state.sim["buses"][b["id"]]
        out.append(
            {
                "bus": b["id"],
                "name": b["name"],
                "type": b["critical_type"],
                "backup_hours": b["backup_hours"],
                "feeds": [{"line": l, "status": state.lines[l]["status"]} for l in feeds],
                "served_mw": s["served_mw"],
                "on_backup": s["on_backup"],
                "flooded": s["flooded"],
            }
        )
    return out


def summarize_result(r: dict) -> dict:
    out = {
        "is_safe": r["is_safe"],
        "unserved_mw": r["totals"]["unserved_mw"],
        "customers_out": r["totals"]["customers_out"],
        "critical_on_backup": r["totals"]["critical_on_backup"],
        "n_violations": len(r["violations"]),
        "violations": r["violations"][:5],  # top 5 only: dozens of lines per revision swamp the LLM context
        "n1_warnings": r["n1_warnings"][:5],
        "n_watch": len(r.get("watch", [])),
        "watch": r.get("watch", [])[:5],
    }
    built = r["totals"].get("buildings_out")  # LA grid only: the buildings behind cut-off substations, by kind
    if built is not None:
        out["hospitals_on_backup"] = built["hospitals"]
        out["buildings_out"] = {"by_kind": built["by_kind"], "score": built["score"], "n": built["n"], "severity": built["severity"],
                                "sites": built["sites"][:12]}
    return out


WARNINGS_PER_STEP = 5  # buildings named per plan step; more than a handful stops being read


def building_score(result: dict) -> int:
    """Triage weight of the buildings a plan cuts off (0 where buildings are not modelled)."""
    return (result.get("buildings_out") or {}).get("score", 0)


# --------------------------------------------------------------------------- tool factory
def make_tools(state, log: JobLog, agent: str, names: list[str] | None = None):
    def get_grid_summary() -> str:
        """Current grid: every line (status, flow, loading %, conductor temp, sag, tree clearance, fire risk,
        violations) and every substation (load, served/unserved MW, flooded). Also totals and N-1 warnings."""
        log(agent, "reading grid summary")
        cache = state.__dict__.setdefault("_summary_cache", {})
        key = id(state.sim)  # computed once per grid state, shared by every agent in the run
        if key not in cache:
            cache.clear()
            cache[key] = json.dumps(grid_summary(state))
        return cache[key]

    def get_weather() -> str:
        """Current weather per zone (ridge, valley, coast): air temp, wind, gust, wind direction
        (degrees the wind blows toward), humidity, solar, flood level. Also the load multiplier."""
        log(agent, "reading zone weather")
        return json.dumps({"scenario": state.scenario, "load_multiplier": state.load_multiplier, "zones": state.weather})

    def get_reports() -> str:
        """Ground reports from residents and 911, each snapped to a line or substation, with
        corroborated=true when the physics also flags that asset."""
        reps = state.reports_view()
        log(agent, f"reading {len(reps)} ground reports ({sum(r['corroborated'] for r in reps)} corroborated)")
        return json.dumps(
            [{k: r.get(k) for k in ("id", "text", "kind", "severity", "line", "bus", "corroborated")} for r in reps]
        )

    def get_critical_sites() -> str:
        """Hospital and water plant: their feeding lines, backup hours, and whether they are served."""
        log(agent, "checking critical sites")
        return json.dumps(critical_sites(state))

    def simulate_plan(actions_json: str) -> str:
        """Test a plan with the deterministic physics engine. THE ONLY WAY TO JUDGE A PLAN.
        actions_json is a JSON list of at most 3 actions, e.g.
        [{"type":"open_line","target":"R3"},{"type":"close_line","target":"V8"},
         {"type":"shed_load","target":"B7","mw":20},{"type":"dispatch_gen","target":"G2","mw":60},
         {"type":"deenergize_bus","target":"B9"}]
        Returns is_safe, the exact violations, unserved MW, customers out, critical sites on backup, N-1 warnings."""
        try:
            actions = json.loads(actions_json) if isinstance(actions_json, str) else actions_json
            if isinstance(actions, dict):
                actions = actions.get("actions", [actions])
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": f"bad JSON: {e}"})
        if len(actions) > 3:
            return json.dumps({"error": "a plan may have at most 3 actions"})
        r = summarize_result(simulate(state, actions))
        desc = ", ".join(_action_label(a) for a in actions) or "do nothing"
        if r["is_safe"]:
            log("physics", f"[{agent}] {desc} -> SAFE (unserved {r['unserved_mw']} MW)")
        else:
            log("physics", f"[{agent}] {desc} -> REJECTED: {r['violations'][0]}")
        return json.dumps(r)

    def get_validation_summary() -> str:
        """Headline numbers of the historical validation (V1-V7) with their baselines, read from validation/results.
        Cite these when explaining how much to trust the model. You do not compute them."""
        from ..la import replay

        log(agent, "reading validation summary")
        return json.dumps(replay.validation_summary().get("cards", []))

    def get_event_context(event_id: str, t: str = "") -> str:
        """Facts for a historical replay hour: region risk index vs alarm, lines over the fire alarm, actual customers
        out (EAGLE-I), active Red Flag zones, fires, and this event's validation results. event_id like 'E1'."""
        from ..la import replay

        log(agent, f"reading event context {event_id} {t}")
        return json.dumps(replay.event_context(event_id, t or None), default=str)

    def get_storm_damage() -> str:
        """Wind agent tool (LA replays): the calibrated STORM-DAMAGE model for this hour, i.e. how likely wind and rain
        are to knock lines down and put customers out (different from fire risk). Returns the storm index (0-1) vs its
        alarm, % of the network under damaging wind / heavy rain, the lines most likely to be damaged, and how well this
        model matched real outages historically (V4a/V4b). Use it to recommend crew pre-staging and patrols."""
        from ..la import replay

        log(agent, "reading storm-damage model")
        h = replay.hour(state.eid, t=state.t_utc.isoformat())
        val = {c["id"]: {k: c[k] for k in ("headline", "baseline", "status")} for c in replay.validation_summary().get("cards", [])
               if c["id"] in ("V2", "V4a", "V4b")}
        return json.dumps({"storm": h.get("storm"), "actual_customers_out": h["outages"], "validation": val}, default=str)

    def get_candidate_actions() -> str:
        """The valid switching actions for this grid state (real line, bus and import IDs), pre-screened by the
        rule-based planner. Build plans ONLY from these actions (you may combine up to 3)."""
        from .mock_planner import _Planner

        log(agent, "reading candidate actions")
        p = _Planner(state, lambda *a, **k: None, 0)
        base = p.sim([])
        p.analyze(base)
        return json.dumps([{"action": c["action"], "label": c["label"], "why": c["why"]} for c in p.candidates(base)][:25])

    def get_site_context(line_id: str) -> str:
        """Site agent (LA replays): what is on the ground along a line, photo by photo (up to 4 aerial photos of its most
        fire-prone stretches), and who is downwind of each this hour. Per photo: LANDFIRE fuel, 2020 Census people and
        hospitals in the 10 km downwind cone, and (when Gemini has described the photos) what is seen: land cover,
        buildings, vegetation at the tower, wildland-urban edge, road access for crews. Also a line summary, the worst
        photo, and where Gemini's view disagrees with the data. Context only: never use it to change a number.
        line_id like 'L29'."""
        from site_agent import agent as SA

        log(agent, f"reading site context {line_id}")
        try:
            out = SA.describe(line_id, state.eid, t=state.t_utc.isoformat(), use_llm=False, fetch=False)
        except KeyError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(SA.compact(out), default=str)

    def get_situation_brief() -> str:
        """The shared situation brief every agent starts from: fire alarm, storm, grid, weather, site context and
        validation for this hour. Numbers are copied from physics and data; no hindsight (no real outages or fires)."""
        log(agent, "reading situation brief")
        return brief_text(build_brief(state))

    def get_building_impact(line_ids_json: str) -> str:
        """LA grid: what loses grid power if each of these lines is switched off. For every line id in the JSON list:
        buildings by kind, their priority tier (1 life safety, 2 lifelines, 3 cannot ride through an outage, 4 homes and
        industry, 5 offices and shops), the heaviest names, and the customers behind it. Use it to decide which of two
        equally risky lines to cut first: the one that reaches the lower tier. Building records are CoStar where the
        extract is loaded, OpenStreetMap hospitals and water plants otherwise; the answer says which."""
        if not hasattr(state, "cut_impact"):
            return json.dumps({"error": "building impact is available on the LA grid only"})
        from ..la import buildings as B

        try:
            ids = [str(x) for x in json.loads(line_ids_json)][:12]
        except (ValueError, TypeError):
            return json.dumps({"error": "pass a JSON list of line ids, e.g. [\"L558\", \"L226\"]"})
        log(agent, f"comparing what {len(ids)} line(s) feed, by building tier")
        out = {}
        for lid in ids:
            if lid not in state.lines:
                out[lid] = {"error": "unknown line"}
                continue
            c = state.cut_impact(lid)
            out[lid] = {"by_kind": c["by_kind"], "tiers": [t for t in c["ladder"] if t["n"]], "customers": c["customers"],
                        "names": [s["name"] for s in c["sites"][:5]], "triage_score": c["score"]}
        info = B.summary()
        return json.dumps({"lines": out, "records": {"n": info["n"], "from_costar": info["costar"], "sources": info["sources"]},
                           "ladder": [{"tier": t["tier"], "name": t["name"], "kinds": t["kinds"]} for t in B.TIERS]})

    def get_operator_feedback() -> str:
        """What the operator said when rejecting earlier plans (fixed reasons with counts, and short notes). Read it
        before ranking plans and say in "tradeoffs" how a plan answers it. Empty when nothing was rejected yet."""
        from ..la import feedback as F

        log(agent, "reading operator feedback from earlier rejections")
        return json.dumps(F.summary())

    all_tools = {
        "get_building_impact": get_building_impact,
        "get_operator_feedback": get_operator_feedback,
        "get_situation_brief": get_situation_brief,
        "get_site_context": get_site_context,
        "get_candidate_actions": get_candidate_actions,
        "get_storm_damage": get_storm_damage,
        "get_grid_summary": get_grid_summary,
        "get_weather": get_weather,
        "get_reports": get_reports,
        "get_critical_sites": get_critical_sites,
        "simulate_plan": simulate_plan,
        "get_validation_summary": get_validation_summary,
        "get_event_context": get_event_context,
    }
    return [all_tools[n] for n in (names or all_tools)]


def _action_label(a: dict) -> str:
    t = a.get("type")
    if t == "deenergize_lines":
        return f"de-energize {len(a.get('targets') or [])} lines ({a.get('target')})"
    if t == "shed_load":
        return f"shed {a.get('mw')} MW @ {a.get('target')}"
    if t == "dispatch_gen":
        return f"dispatch {a.get('target')} to {a.get('mw')} MW"
    return f"{t} {a.get('target')}"


def _distinct(plans: list[dict]) -> list[dict]:
    """Drop a plan that switches off the same lines and does the same other things as one ranked above it."""
    seen, out = set(), []
    for p in plans:
        off, other = set(), []
        for a in p["actions"]:
            if a.get("type") == "open_line":
                off.add(a.get("target"))
            elif a.get("type") == "deenergize_lines":
                off |= set(a.get("targets") or [])
            else:
                other.append(json.dumps(a, sort_keys=True))
        key = (frozenset(off), tuple(sorted(other)))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# --------------------------------------------------------------------------- verification
def verify_output(state, output: dict, log: JobLog | None = None) -> dict:
    """Never trust LLM-reported numbers: re-run physics on every plan and overwrite `result`."""
    base = simulate(state)
    output["do_nothing"] = {
        "violations": base["violations"],
        "unserved_mw": base["totals"]["unserved_mw"],
        "customers_out": base["totals"]["customers_out"],
        "critical_on_backup": base["totals"]["critical_on_backup"],
        "n1_warnings": base["n1_warnings"],
    }
    plans = []
    for i, p in enumerate(output.get("plans") or []):
        steps = [s for s in (p.get("steps") or []) if isinstance(s, dict) and isinstance(s.get("action"), dict)][:3]
        actions = [s["action"] for s in steps]
        r = simulate(state, actions)
        if hasattr(state, "step_impacts"):  # LA grid: what each step cuts off, the five heaviest named. Code picks them.
            for step, built in zip(steps, state.step_impacts(actions)):
                step["buildings"] = {"by_kind": built["by_kind"], "score": built["score"], "n": built["n"], "severity": built["severity"],
                                     "sites": built["sites"][:WARNINGS_PER_STEP]}
        p["steps"] = steps
        p["id"] = p.get("id") or f"P{i + 1}"
        p["actions"] = actions
        p["result"] = summarize_result(r)
        p["result"]["verified_by"] = "physics engine"
        p.setdefault("attempts", 1)
        p.setdefault("assumptions", [])
        p.setdefault("tradeoffs", "")
        p.setdefault("raised_by", [])
        try:
            p["confidence"] = max(0.0, min(1.0, float(p.get("confidence", 0.6))))
        except (TypeError, ValueError):
            p["confidence"] = 0.6
        plans.append(p)
        if log:
            verdict = "safe" if r["is_safe"] else f"NOT SAFE ({r['violations'][0]})"
            log("physics", f"backend re-verified {p['id']}: {verdict}")
    # safe plans first, then fewer violations, then the plan that cuts off less critical buildings (a hospital outranks
    # an office), then less unserved load, fewer actions
    load = max(base["totals"].get("load_mw", 1.0), 1.0)
    plans.sort(key=lambda p: (not p["result"]["is_safe"], p["result"]["unserved_mw"] > 0.1 * load, len(p["result"]["violations"]),
                              building_score(p["result"]), p["result"]["unserved_mw"], len(p["actions"])))
    plans = _distinct(plans)  # two plans that switch the same things are one option
    for i, p in enumerate(plans):
        p["id"] = f"P{i + 1}"
    safe = [p for p in plans if p["result"]["is_safe"]]
    if base["is_safe"] and not plans:
        output["no_safe_option"] = False
    else:
        output["no_safe_option"] = len(safe) == 0
    if output["no_safe_option"] and plans:
        plans = plans[:1]  # show only the least-bad plan, clearly labelled
        plans[0]["least_bad"] = True
    # three plans at most, plus the one shaped by the operator's feedback if it did not make the cut
    output["plans"] = plans[:3] + [p for p in plans[3:] if p.get("from_feedback")][:1]
    output.setdefault("disagreements", [])
    output.setdefault("situation", "")
    try:  # the shared brief travels with every result; a brief failure never breaks planning
        output["brief"] = build_brief(state)
    except Exception as e:  # noqa: BLE001
        if log:
            log("system", f"situation brief unavailable: {str(e)[:120]}")
    return output
