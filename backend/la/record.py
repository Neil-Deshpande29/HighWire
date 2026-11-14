"""The decision record: an append-only list of what the co-pilot proposed and what people decided, in order.

In memory, for this backend process. Entries are written by the routes (a run, its plans, an escalation, an approval,
a rejection, a reset), never by a model, and each carries the wall-clock time and the role that acted.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

KINDS = {"run", "plans", "escalated", "approved", "rejected", "bulletin", "report", "reset"}
ROLES = {"operator", "supervisor", "system"}
MAX_ENTRIES = 500

_lock = threading.Lock()
_entries: list[dict] = []


def add(kind: str, text: str, role: str = "system", event: str | None = None, hour_utc: str | None = None, **data) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown record kind {kind!r}")
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    with _lock:
        entry = {"n": len(_entries) + 1, "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
                 "role": role, "text": text, "event": event, "hour_utc": hour_utc, "data": data}
        _entries.append(entry)
        del _entries[:-MAX_ENTRIES]
        return entry


def entries() -> list[dict]:
    with _lock:
        return list(_entries)


def clear() -> None:
    with _lock:
        _entries.clear()
