"""Tools the agents call. Plain Python functions returning JSON strings.

They wrap the deterministic physics + in-memory state. `make_tools(state, log, agent)` returns
closures bound to one agent name, so every call lands in the activity log under that agent.
Also holds `verify_output`: the backend re-runs physics on every plan and overwrites its numbers.
"""
from __future__ import annotations

import json
import re
import math
import time

from ..physics import simulate as _phys_simulate
from ..textguard import guard
from .brief import TRUST_CHECKS, brief_text, build_brief


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


EVENT_KINDS = ("route", "spawn", "vfs_write", "vfs_read", "tool", "agent_done", "physics", "plans")


class JobLog:
    """What a run says while it works, and the files its agents share.

    Every entry has a time, who spoke and a message. An entry may also be a structured event, with a `kind` (one of
    EVENT_KINDS) and `meta`: the screen draws the live agent graph from these alone. `vfs` is the run's virtual file
    system: path -> text. Specialists write their findings there and the orchestrator reads them back."""

    structured = True  # it keeps events (kind, meta), not only lines

    def __init__(self):
        self.entries: list[dict] = []
        self.vfs: dict[str, str] = {}

    def __call__(self, agent: str, message: str, kind: str | None = None, /, **meta):
        e = {"t": round(time.time(), 3), "agent": agent, "team": TEAMS.get(agent, "system"), "message": message}
        if kind:
            e["kind"], e["meta"] = kind, meta
        self.entries.append(e)

    def write(self, agent: str, path: str, content: str):
        self.vfs[path] = content
        self(agent, f"wrote {path}", "vfs_write", agent=agent, path=path, bytes=len(content.encode()))

    def read(self, reader: str, path: str) -> str | None:
        self(reader, f"read {path}", "vfs_read", reader=reader, path=path)
        return self.vfs.get(path)


def emit(log, agent: str, message: str, kind: str | None = None, /, **meta):
    """Log a structured event on a JobLog; on a plain two-argument log (tests, the silent backup run) just the line."""
    try:
        log(agent, message, kind, **meta)
    except TypeError:
        log(agent, message)


def findings_path(agent: str, ext: str = "json") -> str:
    return f"/findings/{agent}.{ext}"


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
    tot = r["totals"]
    out = {
        "is_safe": r["is_safe"],
        "unserved_mw": tot["unserved_mw"],
        "customers_out": tot["customers_out"],
        "critical_on_backup": tot["critical_on_backup"],
        "n_violations": len(r["violations"]),
        "violations": r["violations"][:5],  # top 5 only: dozens of lines per revision swamp the LLM context
        "n1_warnings": r["n1_warnings"][:5],
        "n_watch": len(r.get("watch", [])),
        "watch": r.get("watch", [])[:5],
    }
    out.update({k: tot[k] for k in ("hospitals_on_backup", "critical_names") if k in tot})
    built = tot.get("buildings_out")  # LA grid only: the buildings behind cut-off substations, by kind
    if built is not None:
        out["hospitals_on_backup"] = built["hospitals"]
        out["buildings_out"] = {"by_kind": built["by_kind"], "score": built["score"], "n": built["n"], "severity": built["severity"],
                                "critical": built["critical"], "homes": built["homes"], "ladder": built["ladder"], "sites": built["sites"][:12]}
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
        emit(log, agent, "reading grid summary", "tool", tool="get_grid_summary")
        cache = state.__dict__.setdefault("_summary_cache", {})
        key = id(state.sim)  # computed once per grid state, shared by every agent in the run
        if key not in cache:
            cache.clear()
            cache[key] = json.dumps(grid_summary(state))
        return cache[key]

    def get_weather() -> str:
        """Current weather per zone (ridge, valley, coast): air temp, wind, gust, wind direction
        (degrees the wind blows toward), humidity, solar, flood level. Also the load multiplier."""
        emit(log, agent, "reading zone weather", "tool", tool="get_weather")
        return json.dumps({"scenario": state.scenario, "load_multiplier": state.load_multiplier, "zones": state.weather})

    def get_reports() -> str:
        """Ground reports from residents and 911, each snapped to a line or substation, with
        corroborated=true when the physics also flags that asset."""
        reps = state.reports_view()
        emit(log, agent, f"reading {len(reps)} ground reports ({sum(r['corroborated'] for r in reps)} corroborated)", "tool", tool="get_reports")
        return json.dumps(
            [{k: r.get(k) for k in ("id", "text", "kind", "severity", "line", "bus", "corroborated")} for r in reps]
        )

    def get_critical_sites() -> str:
        """Hospital and water plant: their feeding lines, backup hours, and whether they are served."""
        emit(log, agent, "checking critical sites", "tool", tool="get_critical_sites")
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
        if not isinstance(actions, list):
            return json.dumps({"error": "actions_json must be a JSON list of actions"})
        if len(actions) > MAX_STEPS:  # the AI team is told three; the rule-driven team may need one move per overloaded line
            return json.dumps({"error": f"a plan may have at most {MAX_STEPS} actions"})
        actions = [_clean_action(state, a) if isinstance(a, dict) else None for a in actions]
        if None in actions:
            return json.dumps({"error": "an action cannot be run on this grid: use an action type, ids and a numeric amount that "
                                        "exist here (get_candidate_actions lists valid actions)"})
        _guard_labels(actions, _grid_ids(state))  # the description below goes to the activity log
        r = summarize_result(simulate(state, actions))
        desc = ", ".join(_action_label(a) for a in actions) or "do nothing"
        if r["is_safe"]:
            log("physics", f"[{agent}] {desc} -> SAFE (unserved {r['unserved_mw']} MW)")
        else:
            log("physics", f"[{agent}] {desc} -> REJECTED: {r['violations'][0]}")
        return json.dumps(r)

    def get_validation_summary() -> str:
        """Headline numbers of the historical validation checks that say how far to trust the fire and storm signals
        (the same checks as the situation brief), with their baselines, read from validation/results.
        Cite these when explaining how much to trust the model. You do not compute them."""
        emit(log, agent, "reading validation summary", "tool", tool="get_validation_summary")
        return json.dumps(_trusted_cards())

    def get_event_context(event_id: str = "", t: str = "") -> str:
        """What an operator can observe at THIS run's hour: region fire index vs its alarm, lines over the line alarm,
        Red Flag zones in force, worst weather, the storm-damage model, and customers out this hour where recorded
        (EAGLE-I). Always this run's event and hour: the arguments are ignored and nothing after this hour is shown."""
        from ..la import replay

        emit(log, agent, f"reading this hour's context ({state.eid} {state.t_utc:%Y-%m-%d %H:%M} UTC)", "tool", tool="get_event_context")
        ctx = replay.hour_context(state.eid, state.t_utc.isoformat())
        if state.is_modified():
            ctx["note"] = "As recorded for this hour; Study conditions and operator switching are in the situation brief."
        return json.dumps(ctx, default=str)

    def get_storm_damage() -> str:
        """Wind agent tool (LA replays): the calibrated STORM-DAMAGE model for this hour, i.e. how likely wind and rain
        are to knock lines down and put customers out (different from fire risk). Returns the storm index (0-1) vs its
        alarm, % of the network under damaging wind / heavy rain, the lines most likely to be damaged, customers out this
        hour where recorded, and how well the model separated outage hours over other years (the brief's trust checks).
        Use it to recommend crew pre-staging and patrols."""
        from ..la import replay

        emit(log, agent, "reading storm-damage model", "tool", tool="get_storm_damage")
        ctx = replay.hour_context(state.eid, state.t_utc.isoformat())
        val = {c["id"]: {k: c[k] for k in ("headline", "baseline", "status")} for c in _trusted_cards()}
        out = {"storm": ctx["storm_damage"], **{k: ctx[k] for k in ("customers_out_this_hour",) if k in ctx}, "validation": val}
        return json.dumps(out, default=str)

    def get_candidate_actions() -> str:
        """The valid switching actions for this grid state (real line, bus and import IDs), pre-screened by the
        rule-based planner. Build plans ONLY from these actions (you may combine up to 3)."""
        from .mock_planner import _Planner

        emit(log, agent, "reading candidate actions", "tool", tool="get_candidate_actions")
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

        emit(log, agent, f"reading site context {line_id}", "tool", tool="get_site_context")
        try:
            out = SA.describe(line_id, state.eid, t=state.t_utc.isoformat(), use_llm=False, fetch=False)
        except KeyError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(SA.compact(out), default=str)

    def get_situation_brief() -> str:
        """The shared situation brief every agent starts from: fire alarm, storm, grid, weather, site context and
        validation for this hour. Numbers are copied from physics and data; no hindsight (no real outages or fires)."""
        emit(log, agent, "reading situation brief", "tool", tool="get_situation_brief")
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
        emit(log, agent, f"comparing what {len(ids)} line(s) feed, by building tier", "tool", tool="get_building_impact")
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

        emit(log, agent, "reading operator feedback from earlier rejections", "tool", tool="get_operator_feedback")
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


def _trusted_cards() -> list[dict]:
    """Validation cards an agent may read: the brief's TRUST_CHECKS only. The other cards name the replayed fires or say
    how the replayed events turned out, which an agent working inside one of those hours must not learn."""
    from ..la import replay

    return [c for c in replay.validation_summary().get("cards", []) if c.get("id") in TRUST_CHECKS]


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
OUTPUT_KEYS = ("mode", "situation", "plans", "disagreements")  # all a model's reply may carry into the result
PLAN_KEYS = ("title", "steps", "result", "tradeoffs", "confidence", "assumptions", "attempts")
TEXT_LIMIT = 400
_VERBS = {"open_line": "de-energize", "close_line": "re-close", "deenergize_bus": "de-energize substation", "limit_line": "limit"}
_AMOUNTS = {"shed_load": "mw", "dispatch_gen": "mw", "limit_line": "pct"}


def _strs(x) -> list[str]:
    """A list of strings from whatever shape arrived: one string is a one-item list, anything not a string is dropped."""
    return [x] if isinstance(x, str) else [v for v in x if isinstance(v, str)] if isinstance(x, list) else []


def _agents(x) -> list[str]:
    return [a for a in _strs(x) if a in TEAMS]


def _raised(x, files: dict | None) -> dict:
    """Who raised a plan step, and the findings files it came from. A step may name agents or their files
    ("/findings/fire_agent.json"). When the run kept shared files, only agents whose file exists are kept, so a plan
    never cites findings nobody wrote."""
    named = [re.sub(r"^/findings/(\w+)\.\w+$", r"\1", s) for s in _strs(x)]
    agents = list(dict.fromkeys(a for a in named if a in TEAMS))
    if files is None:
        return {"raised_by": agents}
    agents = [a for a in agents if findings_path(a) in files]
    return {"raised_by": agents, "raised_from": [findings_path(a) for a in agents]}


def _number(x) -> float | int | None:
    """A finite number, or None. A numeric string is coerced; None, a bool or "20 MW" is not a number."""
    if isinstance(x, str):
        try:
            x = float(x)
        except ValueError:
            return None
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _dispatchable(state) -> set[str]:
    """Ids dispatch_gen may name: import points on the LA grid, generators on the synthetic region."""
    if hasattr(state, "grid"):
        from ..la import data as D

        return {x["id"] for x in D.grid()["imports"]}
    return set(state.gens)


def _clean_action(state, a: dict) -> dict | None:
    """The action with only its own fields, coerced, or None when it cannot be run on this grid (unknown type or id,
    missing or non-numeric amount). Checked here so a malformed plan is dropped rather than simulated as something else."""
    kind, target = a.get("type"), a.get("target")
    if kind == "deenergize_lines":
        targets = a.get("targets")
        if not isinstance(targets, list) or not targets or not all(isinstance(t, str) and t in state.lines for t in targets):
            return None
        return {"type": kind, "target": target if isinstance(target, str) and target else "selected lines", "targets": targets}
    if not isinstance(target, str):
        return None
    known = (state.lines if kind in ("open_line", "close_line", "limit_line") else state.buses if kind in ("deenergize_bus", "shed_load")
             else _dispatchable(state) if kind == "dispatch_gen" else ())
    if target not in known:
        return None
    out = {"type": kind, "target": target}
    if kind in _AMOUNTS:
        amount = _number(a.get(_AMOUNTS[kind]))
        if amount is None:
            return None
        out[_AMOUNTS[kind]] = amount
    return out


def _grid_ids(state) -> set[str]:
    """Every id on this grid. In model-written text these are names, not quantities, although they hold digits."""
    return set(state.lines) | set(state.buses) | _dispatchable(state)


def _guard_labels(actions: list[dict], names: set[str]) -> bool:
    """The label of a group shut-off is model-written prose that reaches titles and the activity log: replace it, in
    place, when it states a quantity. True when a label was withheld."""
    held = False
    for a in actions:
        if a["type"] == "deenergize_lines":
            label, withheld = guard(a["target"], names, 80)
            a["target"], held = label or "selected lines", held or withheld
    return held


def _code_title(actions: list[dict]) -> str:
    title = ", ".join(f"{_VERBS[a['type']]} {a['target']}" if a["type"] in _VERBS else _action_label(a) for a in actions)
    return title[0].upper() + title[1:]


def _code_why(a: dict, base: dict) -> str:
    """What the do-nothing physics says about the lines this action switches."""
    found = [v for lid in a.get("targets") or [a["target"]] for v in base["lines"].get(lid, {}).get("violations", [])]
    return found[0] + (f" (and {len(found) - 1} more on these lines)" if len(found) > 1 else "") if found else ""


def _code_tradeoffs(res: dict) -> str:
    parts = ["Keeps every customer on" if res["unserved_mw"] < 0.05
             else f"{res['unserved_mw']:.0f} MW (~{res['customers_out']:,} customers) without power"]
    n_backup = len(res.get("critical_names") or res["critical_on_backup"])
    if n_backup:
        parts.append(f"{n_backup} critical site(s) on backup power")
    if res["n_watch"]:
        parts.append(f"{res['n_watch']} bulk line(s) above the fire alarm stay energized for patrol")
    if not res["is_safe"]:
        parts.append(f"{res['n_violations']} physics violation(s) remain")
    return "; ".join(parts) + "."


def _code_situation(state, base: dict) -> str:
    v = base["violations"]
    if not v:
        return f"{state.scenario}: the grid is within limits with no action."
    return f"{state.scenario}: {len(v)} physics violation(s) with no action, starting with {v[0]}."


def _code_confidence(n_actions: int, r: dict) -> float:
    """Heuristic 0-1 score from the re-simulated result, with the rule planner's own formula (mock_planner plan_json):
    fewer actions, less unserved load and fewer N-1 warnings score higher. It is not a probability."""
    conf = 0.93 - 0.05 * n_actions - min(0.3, r["totals"]["unserved_mw"] / 200) - 0.04 * len(r["n1_warnings"])
    return round(max(0.35, conf), 2)


MAX_STEPS = 6  # the most steps a rule-based plan may carry (on a heat day: one move per overloaded line)
MODEL_STEPS = 3  # the most steps the AI team is told a plan may carry (team.py prompts; simulate_plan refuses more)


def verify_output(state, output: dict, log: JobLog | None = None) -> dict:
    """Never trust LLM-reported numbers: re-run physics on every plan and overwrite `result`.

    The rule-based planner's output (mode "mock") keeps its text, which code wrote. Anything else is model-written: its
    prose is shown only when it states no quantity (textguard), withheld or missing text is rebuilt here from the verified
    result and flagged `text_withheld`, and confidence and attempts are computed here instead of copied. A plan that is
    not an object, has no actions or names something this grid does not have is dropped, never simulated."""
    model = output.get("mode") != "mock"
    names = _grid_ids(state) if model else set()
    files = getattr(log, "vfs", None) or None  # the run's shared files, when the run kept any
    if model:
        for k in [k for k in output if k not in OUTPUT_KEYS]:
            del output[k]

    def text(value, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
        """(text to show, whether it was withheld). Rule-based text passes as written."""
        if model:
            return guard(value, names, limit)
        return ("" if value is None else str(value)), False

    base = simulate(state)
    output["do_nothing"] = {
        "violations": base["violations"],
        "unserved_mw": base["totals"]["unserved_mw"],
        "customers_out": base["totals"]["customers_out"],
        "critical_on_backup": base["totals"]["critical_on_backup"],
        "n1_warnings": base["n1_warnings"],
    }
    plans = []
    proposed = output.get("plans")
    for i, given in enumerate(proposed if isinstance(proposed, list) else []):
        steps = given.get("steps") if isinstance(given, dict) else None
        steps = [s for s in (steps if isinstance(steps, list) else []) if isinstance(s, dict) and isinstance(s.get("action"), dict)]
        limit = MODEL_STEPS if model else MAX_STEPS
        if len(steps) > limit:  # never cut a plan short: its first steps alone are a different plan, with its title and reasons wrong
            if log:
                log("physics", f"dropped a proposed plan: it has more than {limit} steps")
            continue
        actions = [_clean_action(state, s["action"]) for s in steps]
        if not actions or None in actions:
            if log:
                log("physics", "dropped a proposed plan: " + ("an action cannot be run on this grid" if actions else "it has no actions"))
            continue
        r = simulate(state, actions)
        result = summarize_result(r)
        result["verified_by"] = "physics engine"
        p = {k: given.get(k) for k in PLAN_KEYS} if model else given
        title, *held = text(given.get("title"), 120)
        tradeoffs, w = text(given.get("tradeoffs"))
        held.append(w)
        assumptions = [text(a) for a in _strs(given.get("assumptions"))]
        whys = [text(s.get("why")) for s in steps]
        held += [w for _, w in assumptions + whys]
        if model:
            held.append(_guard_labels(actions, names))
        p["title"] = title or _code_title(actions)
        p["steps"] = [{"action": a, "why": why or (_code_why(a, base) if model else ""), **_raised(s.get("raised_by"), files)}
                      for s, a, (why, _) in zip(steps, actions, whys)]
        if hasattr(state, "step_impacts"):  # LA grid: what each step cuts off, the five heaviest named. Code picks them.
            for step, built in zip(p["steps"], state.step_impacts(actions)):
                step["buildings"] = {"by_kind": built["by_kind"], "score": built["score"], "n": built["n"], "severity": built["severity"],
                                     "critical": built["critical"], "homes": built["homes"], "sites": built["sites"][:WARNINGS_PER_STEP]}
        p["id"] = (not model and given.get("id")) or f"P{i + 1}"
        p["actions"] = actions
        p["result"] = result
        p["tradeoffs"] = tradeoffs or (_code_tradeoffs(result) if model else "")
        p["assumptions"] = [a for a, _ in assumptions if a]
        p["raised_by"] = _agents(given.get("raised_by"))
        if model:
            # the backend's own re-simulation is the one attempt it can vouch for
            p["confidence"], p["attempts"], p["text_withheld"] = _code_confidence(len(actions), r), 1, any(held)
        else:
            attempts = _number(given.get("attempts"))
            p["attempts"] = 1 if attempts is None else int(attempts)
            try:
                p["confidence"] = max(0.0, min(1.0, float(given.get("confidence", 0.6))))
            except (TypeError, ValueError):
                p["confidence"] = 0.6
        plans.append(p)
        if log:
            verdict = "safe" if r["is_safe"] else f"NOT SAFE ({r['violations'][0]})"
            emit(log, "physics", f"backend re-verified {p['id']}: {verdict}", "physics", plan=p["id"], safe=bool(r["is_safe"]))
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
    if model:
        situation, held = text(output.get("situation"))
        said = [text(d) for d in _strs(output.get("disagreements"))]
        output["situation"] = situation or _code_situation(state, base)
        output["disagreements"] = [d for d, _ in said if d]
        output["text_withheld"] = held or any(w for _, w in said) or any(p["text_withheld"] for p in output["plans"])
    else:
        output.setdefault("disagreements", [])
        output.setdefault("situation", "")
    try:  # the shared brief travels with every result; a brief failure never breaks planning
        output["brief"] = build_brief(state)
    except Exception as e:  # noqa: BLE001
        if log:
            log("system", f"situation brief unavailable: {str(e)[:120]}")
    return output
