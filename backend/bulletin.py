"""Responder bulletins: the plain-text notice built when the operator approves a plan.

Built by code from the grid state after the plan is applied; nothing is sent anywhere."""
from __future__ import annotations

from datetime import datetime

from .la import data as LAD

SAFETY_LINE = "Crews must verify on site. Treat all downed wires as live."


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def synthetic_bulletin(state, plan: dict) -> dict:
    """Bulletin for the synthetic 12-bus region, including ground reports on lines that are still energized."""
    sim = state.sim
    off = [
        {"id": l, "area": f"{state.lines[l]['zone']} ({state.buses[state.lines[l]['from_bus']]['name']} - {state.buses[state.lines[l]['to_bus']]['name']})"}
        for l in state.operator_opened
        if state.lines[l]["status"] == "open"
    ]
    reported = {}
    for r in state.reports_view():
        if r.get("line") and sim["lines"][r["line"]]["energized"]:
            reported.setdefault(r["line"], []).append(f"{r['kind']}: \"{r['text'][:70]}\"")
    energized_reported = [{"id": l, "reports": v} for l, v in reported.items()]
    subs = []
    for bid, b in sim["buses"].items():
        if b["flooded"] or b["deenergized"]:
            status = "de-energized" if b["deenergized"] else "ENERGIZED"
            subs.append({"id": bid, "name": state.buses[bid]["name"], "flooded": b["flooded"], "state": status})
    backup = [
        {"id": bid, "name": state.buses[bid]["name"], "hours_left": state.buses[bid]["backup_hours"]}
        for bid in sim["totals"]["critical_on_backup"]
    ]
    ts = _now()

    lines_txt = [f"GRID CO-PILOT RESPONDER BULLETIN  {ts}", f"Scenario: {state.scenario}.  Plan approved: {plan['id']} - {plan['title']}", ""]
    lines_txt.append("LINES SWITCHED OFF BY OPERATOR:")
    lines_txt += [f"  - {o['id']}  {o['area']}" for o in off] or ["  - none"]
    lines_txt.append("LINES STILL ENERGIZED WITH GROUND REPORTS (assume LIVE):")
    lines_txt += [f"  - {e['id']}: " + "; ".join(e["reports"]) for e in energized_reported] or ["  - none"]
    lines_txt.append("FLOODED OR DE-ENERGIZED SUBSTATIONS:")
    lines_txt += [f"  - {s['id']} {s['name']}: {'flooded, ' if s['flooded'] else ''}{s['state']}" for s in subs] or ["  - none"]
    lines_txt.append("CRITICAL SITES ON BACKUP POWER:")
    lines_txt += [f"  - {b['id']} {b['name']}: about {b['hours_left']:.0f} h of backup left" for b in backup] or ["  - none"]
    lines_txt += ["", f"Unserved load: {sim['totals']['unserved_mw']} MW (~{sim['totals']['customers_out']:,} customers).", "", SAFETY_LINE]
    return {
        "timestamp": ts,
        "plan_id": plan["id"],
        "lines_switched_off": off,
        "energized_lines_with_reports": energized_reported,
        "substations": subs,
        "critical_on_backup": backup,
        "safety": SAFETY_LINE,
        "text": "\n".join(lines_txt),
    }


def la_bulletin(st, plan: dict) -> dict:
    """Bulletin for the real LA grid: lines switched off, lines still energized above the fire alarm, sites on backup."""
    sim = st.sim
    ts = _now()
    lines = [f"HIGHWIRE RESPONDER BULLETIN  {ts}", f"{'Forecast' if st.eid == 'FX' else 'Replay'}: {st.scenario}.  Plan approved: {plan['id']} - {plan['title']}", ""]
    lines.append("LINES SWITCHED OFF BY OPERATOR:")
    off = [st.lines[l] for l in st.operator_opened if l in st.lines]
    lines += [f"  - {l['id']} {l['kv']:.0f} kV {l['zone']}{(' (' + l['name'] + ')') if l['name'] else ''}" for l in off[:40]] or ["  - none"]
    if len(off) > 40:
        lines.append(f"  - ... and {len(off) - 40} more")
    alarm = LAD.calibration()["fire"]["line_alarm"]
    hot = sorted([v for v in sim["lines"].values() if v["energized"] and v["fire_risk"] >= alarm], key=lambda v: -v["fire_risk"])
    lines.append("LINES STILL ENERGIZED ABOVE THE FIRE ALARM (assume LIVE, patrol):")
    lines += [f"  - {v['id']}: risk {v['fire_risk']:.2f}, gust {v['gust_ms']:.0f} m/s" for v in hot[:20]] or ["  - none"]
    lines.append("CRITICAL SITES ON BACKUP POWER:")
    built = sim["totals"]["buildings_out"]
    backup = lambda s: f"about {s['backup_hours']:.0f} h of backup" if s["backup_hours"] else "backup not known"  # noqa: E731
    lines += [f"  - {s['name']} ({s['kind']}, bus {s['bus']}): {backup(s)}" for s in built["sites"]] or ["  - none"]
    if built["n"] > len(built["sites"]):
        lines.append(f"  - ... and {built['n'] - len(built['sites'])} more buildings without grid power")
    lines += ["", f"Unserved load: {sim['totals']['unserved_mw']} MW (~{sim['totals']['customers_out']:,} customers, modeled).",
              "Electrical values are modeled from voltage class; weather is reanalysis.", "", SAFETY_LINE]
    return {"timestamp": ts, "plan_id": plan["id"], "safety": SAFETY_LINE, "text": "\n".join(lines)}
