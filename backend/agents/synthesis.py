"""How the team's work came together, as a small graph the screen can draw.

Built by code after a run from what the agents actually produced: each specialist with how many things it flagged and
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
    return {
        "mode": out.get("mode"),
        "agents": agents,
        "coordinator": {"candidates": stats.get("candidates"), "disagreements": len(out.get("disagreements") or []),
                        "feedback": len(out.get("feedback_used") or [])},
        "physics": {"screened": stats.get("screened"), "safe": stats.get("safe"), "verified": verified},
        "plans": [{"id": p["id"], "safe": bool(p["result"]["is_safe"]), "from_feedback": bool(p.get("from_feedback")),
                   "raised_by": sorted({a for s in p.get("steps", []) for a in (s.get("raised_by") or []) if a in NAMES})} for p in plans],
    }
