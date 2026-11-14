"""Operator feedback: why a plan was rejected, kept so the coordinator can use it on later runs.

When the operator rejects a plan the screen asks why. The answer (one or more fixed reasons, plus an optional note)
is stored here with the facts of the plan it was about. The next time plans are made, the coordinator reads the
feedback and acts on what it can: the rule-based planner adds a smaller shut-off when the operator said a plan cut
too many customers or put a critical site on backup; the AI team is given the feedback in its prompt and as a tool.

Kept in a small JSON file (data/processed/feedback/, git-ignored) so it survives a restart.
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone

from . import data as D

FILE = D.P / "feedback" / "operator_feedback.json"
REASONS = {
    "too_many_customers": "It cuts power to too many customers",
    "critical_site": "It puts a hospital or water plant on backup",
    "not_enough": "It leaves too much fire risk",
    "wrong_lines": "It switches off the wrong lines",
    "other": "Something else",
}
MAX_NOTE = 400
KEPT = 200

_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return []


def add(plan: dict, reasons: list[str], note: str = "", event: str | None = None, hour_utc: str | None = None, role: str = "operator") -> dict:
    """Record one rejection. Unknown reasons are dropped; a rejection with no reason and no note is not stored."""
    reasons = [r for r in dict.fromkeys(reasons or []) if r in REASONS]
    note = " ".join((note or "").split())[:MAX_NOTE]
    entry = {"at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "role": role, "event": event, "hour_utc": hour_utc,
             "plan": plan.get("id"), "title": plan.get("title"), "reasons": reasons, "note": note,
             "customers_out": plan.get("result", {}).get("customers_out"), "hospitals_on_backup": plan.get("result", {}).get("hospitals_on_backup")}
    if not reasons and not note:
        return entry
    with _lock:
        rows = (_load() + [entry])[-KEPT:]
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(rows, indent=1))
    return entry


def entries() -> list[dict]:
    with _lock:
        return _load()


def clear() -> None:
    with _lock:
        FILE.unlink(missing_ok=True)


def summary() -> dict:
    """What the coordinator carries into a run: how often each reason was given, and the latest notes."""
    rows = entries()
    counts = Counter(r for e in rows for r in e["reasons"])
    return {"n": len(rows), "reasons": [{"id": r, "text": REASONS[r], "n": counts[r]} for r in REASONS if counts[r]],
            "notes": [e["note"] for e in rows if e["note"]][-5:]}


def as_text() -> str:
    """The feedback as plain lines for a model's prompt or tool result. Empty when there is none."""
    s = summary()
    if not s["n"]:
        return ""
    lines = [f"- {r['text']} (said {r['n']} time{'s' if r['n'] != 1 else ''})" for r in s["reasons"]]
    lines += [f"- Operator note: {n}" for n in s["notes"]]
    return "\n".join(lines)
