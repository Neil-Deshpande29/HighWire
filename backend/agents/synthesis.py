"""How the team's work came together, as a small graph the screen can draw.

Built by code after a run from the run's structured events and shared files (who the orchestrator called and why, which
findings files were written and read) and from what the agents actually produced: each specialist with how many things it flagged and
how many plan steps it raised, the coordinator with how many candidate actions it weighed and the operator feedback
it carried, the physics check with how many combinations it tested and how many passed, and the plans that came out.
The same structure is produced for the rule-based planner and the AI team, so the picture never depends on a model.
"""
from __future__ import annotations

import re

NAMES = {"fire_agent": "Fire", "wind_agent": "Wind", "heat_agent": "Heat", "brownout_agent": "Brownout", "buildings_agent": "Buildings"}


def build(out: dict, log: list[dict]) -> dict:
    findings = out.get("agent_findings") or {}
    plans = out.get("plans") or []
    said = {}
    for e in log:
        said[e["agent"]] = said.get(e["agent"], 0) + 1
    agents = []
    for aid, name in NAMES.items():
        found = findings.get(aid, [])
        agents.append({
            "id": aid, "name": name, "findings": len(found), "act_now": sum(f.get("severity", 0) >= 3 for f in found),
            "log_lines": said.get(aid, 0),  # what it reported while working (tool calls, in an AI run)
            "raised_steps": sum(aid in (s.get("raised_by") or []) for p in plans for s in p.get("steps", [])),
        })
    stats = dict(out.get("stats") or {})
    for e in log:  # the rule-based planner logs these; an AI run may not, so they stay optional
        m = re.match(r"(\d+) candidate actions", e["message"])
        if m and e["agent"] == "coordinator":
            stats.setdefault("candidates", int(m.group(1)))
        m = re.match(r"screened (\d+) combinations \((\d+) simulations\): (\d+) safe", e["message"])
        if m and e["agent"] == "physics":
            stats.setdefault("screened", int(m.group(1)))
            stats.setdefault("safe", int(m.group(3)))
    verified = sum(e["agent"] == "physics" and e["message"].startswith("backend re-verified") for e in log)
    events = [e for e in log if e.get("kind")]
    routing = {e["meta"]["agent"]: {"called": bool(e["meta"].get("called")), "reason": e["meta"].get("reason") or ""}
               for e in events if e["kind"] == "route" and e["meta"].get("agent") in NAMES}
    wrote = {e["meta"]["path"]: e["meta"].get("agent") for e in events if e["kind"] == "vfs_write"}
    read = {e["meta"]["path"] for e in events if e["kind"] == "vfs_read"}
    files = out.get("vfs") or {}
    for a in agents:
        a["called"] = routing[a["id"]]["called"] if a["id"] in routing else bool(a["findings"] or a["log_lines"])
        a["reason"] = routing.get(a["id"], {}).get("reason", "")
        a["wrote"] = [p for p, who in wrote.items() if who == a["id"] and p in files]
    return {
        "mode": out.get("mode"),
        "confidence": confidence(out),
        "routing": [{"agent": a, **routing[a]} for a in NAMES if a in routing],
        "files": [{"path": p, "by": wrote.get(p), "bytes": len(files[p].encode()), "read": p in read} for p in sorted(files)],
        "alerts": sum(e["kind"] == "tool" and e["meta"].get("tool") == "send_alert" for e in events),
        "agents": agents,
        "coordinator": {"candidates": stats.get("candidates"), "disagreements": len(out.get("disagreements") or []),
                        "feedback": len(out.get("feedback_used") or [])},
        "physics": {"screened": stats.get("screened"), "safe": stats.get("safe"), "verified": verified},
        "plans": [{"id": p["id"], "safe": bool(p["result"]["is_safe"]), "from_feedback": bool(p.get("from_feedback")),
                   "raised_by": sorted({a for s in p.get("steps", []) for a in (s.get("raised_by") or []) if a in NAMES})} for p in plans],
    }


def confidence(out: dict) -> dict:
    """One overall confidence figure for the run: how sure the system is of its read of the hour. Worked out by code
    from the run; a rule of thumb, not a probability.

    A run that finds trouble and says so is not marked down for it, however much switching the fix takes: the physics
    flagged it and every plan was re-checked. What lowers confidence is the chance of the opposite mistake, a missed or
    understated problem, and evidence that is thin or disagrees:

      start   0.97
      -0.10   a weather station measured stronger gusts or drier air than the model used (the risk may be understated)
      -0.10   the grid reads as within limits while warning signs are up: a Red Flag warning in force, or the risk
              watch not green (a missed problem costs more than a false alarm)
      -0.08   no plan passes the physics check yet (the trouble is real, the fix on offer is not safe)
      -0.05   no weather station reported, so the model's weather is unchecked
      -0.04   lines are flagged for fire risk with no Red Flag warning in force to back the reading
      -0.02   the AI team did not finish and the rule-based planner answered instead

    Returns {"score": 0-1, "level": "high" | "medium" | "low", "reasons": [plain words, the things that lowered it]}."""
    plans = out.get("plans") or []
    safe = [p for p in plans if p["result"]["is_safe"]]
    trouble = (out.get("do_nothing") or {}).get("violations") or []
    brief = out.get("brief") or {}
    wx = brief.get("weather") or {}
    redflag = int(wx.get("n_redflag_active") or len(wx.get("redflag_zones_active") or []))
    score, reasons = 0.97, []
    wind = (out.get("agent_findings") or {}).get("wind_agent") or []
    if any("measured gusts stronger" in f.get("issue", "") or "drier" in f.get("issue", "") for f in wind):
        score -= 0.10
        reasons.append("a weather station measured worse conditions than the model used, so the risk may be understated")
    elif any("no weather station reported" in f.get("issue", "") for f in wind):
        score -= 0.05
        reasons.append("no weather station reported this hour")
    if not trouble:
        level = "green"
        try:
            from ..la import watch

            level = watch.at(brief["event"], brief["t_utc"])["level"]
        except Exception:  # noqa: BLE001  no brief (the toy region): only the Red Flag count is used
            pass
        if redflag or level != "green":
            score -= 0.10
            reasons.append("the grid reads as within limits while warning signs are up")
    if plans and not safe:
        score -= 0.08
        reasons.append("no plan passes the physics check yet")
    if any("fire risk" in v for v in trouble) and brief and not redflag:
        score -= 0.04
        reasons.append("no Red Flag warning is in force to back the fire reading")
    if out.get("fallback_reason"):
        score -= 0.02
        reasons.append("the AI team did not finish; the rule-based planner answered")
    score = round(min(0.99, max(0.5, score)), 2)
    return {"score": score, "level": "high" if score >= 0.85 else "medium" if score >= 0.7 else "low", "reasons": reasons}
