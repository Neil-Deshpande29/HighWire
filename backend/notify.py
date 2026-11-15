"""Text alerts (SMS) through LangChain's Twilio tool.

Two ways in, one function out (`send`), so both obey the same on/off switch, the same de-duplication and the same dry run:

  1. Code, always on (`on_hour`, `on_approval`): a text when a line's fire index is well over its calibrated alarm, when
     the storm model gives a line a high chance of being knocked down, and when the operator approves a plan that turns
     lines off. Every number in these texts comes from the physics.
  2. The orchestrator's `send_alert(message)` tool (`alert_tool`), for very high risk findings only.

Settings come from the environment (.env, read by backend/llm.py): TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
TWILIO_FROM_NUMBER and ALERT_PHONE (who gets the texts). If any is missing, or the from-number is still a placeholder,
nothing is sent: the message is kept and shown on screen as "would have sent" (a dry run). No number or key is ever
written into code, logs or the record; the screen shows only the last digits of the recipient.
"""
from __future__ import annotations

import os
import re
import threading
import time

from . import llm as _env  # noqa: F401  importing it reads the repo's .env

VERY_HIGH_RISK = 1.05  # a line's fire index at or above this multiple of the calibrated line alarm is "very high"
LIKELY_OUT = 0.8  # storm model: a line with at least this chance of being knocked down
COOLDOWN_S = 6 * 3600  # one text per line, per condition, per window this long of the scenario's own clock
MAX_PER_HOUR = 3  # the worst few lines per condition per window get a text each, never the whole list
KEEP = 60  # alerts kept for the bell
SYNC = False  # tests set this: send in the calling thread instead of a background one

ALERTS: list[dict] = []  # newest last: {n, at_utc, message, source, status, why}
STATE = {"on": True}
_seen: set[str] = set()
_lock = threading.Lock()
_PHONE = re.compile(r"^\+\d{8,15}$")


def _setting(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def configured() -> bool:
    """True when a real text can be sent: account, token, a real from-number and a recipient are all set."""
    return bool(_setting("TWILIO_ACCOUNT_SID") and _setting("TWILIO_AUTH_TOKEN") and _PHONE.match(_setting("TWILIO_FROM_NUMBER"))
                and _PHONE.match(_setting("ALERT_PHONE")))


def status() -> dict:
    to = _setting("ALERT_PHONE")
    missing = [n for n in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "ALERT_PHONE")
               if not _setting(n) or (n in ("TWILIO_FROM_NUMBER", "ALERT_PHONE") and not _PHONE.match(_setting(n)))]
    return {"on": STATE["on"], "dry_run": not configured(), "missing": missing,
            "to": ("number ending " + to[-4:]) if _PHONE.match(to) else None, "alerts": list(reversed(ALERTS))}


def _twilio_send(message: str, to: str) -> str:
    """LangChain's built-in Twilio wrapper; it reads the account, token and from-number from the environment."""
    from langchain_community.utilities.twilio import TwilioAPIWrapper

    return TwilioAPIWrapper().run(message, to)


def send(message: str, *, key: str | None = None, source: str = "code", why: str = "", wait: bool = False) -> dict | None:
    """Send one text, or record what would have been sent. None when alerts are off or `key` was already used
    (the same line, condition and time window). `source` is "code", "agent" or "test"."""
    message = " ".join(str(message).split())[:300]
    with _lock:
        if not STATE["on"] or not message or (key and key in _seen):
            return None
        if key:
            _seen.add(key)
        entry = {"n": (ALERTS[-1]["n"] + 1) if ALERTS else 1, "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "message": message, "source": source, "why": why, "status": "sending" if configured() else "dry run"}
        ALERTS.append(entry)
        del ALERTS[:-KEEP]
    if entry["status"] == "sending":
        def work():
            try:
                _twilio_send(message, _setting("ALERT_PHONE"))
                entry["status"] = "sent"
            except Exception as e:  # noqa: BLE001  never let a text break a run; keep keys out of the note
                entry["status"], entry["error"] = "failed", type(e).__name__

        if wait or SYNC:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
    return entry


def reset() -> None:
    """Forget what was sent and what was already alerted (a session reset, and between tests)."""
    with _lock:
        ALERTS.clear()
        _seen.clear()


def _window(t_utc) -> int:
    return int(t_utc.timestamp() // COOLDOWN_S)


def _key(event: str, line: str, condition: str, t_utc) -> str:
    return f"{event}|{line}|{condition}|{_window(t_utc)}"


def _line_name(st, line_id: str) -> str:
    ln = st.lines[line_id]
    return f"{ln.get('name') or 'Line ' + line_id} {round(ln['kv'])} kV"


# --------------------------------------------------------------------------- 1. code triggers
def very_high_fire(st, sim: dict | None = None) -> list[dict]:
    """Live overhead lines whose fire index is at or above VERY_HIGH_RISK times the calibrated alarm, worst first."""
    from .la import data as D

    alarm = D.calibration()["fire"]["line_alarm"]
    rows, _, _ = st.fire_lines(sim or st.sim, limit=len(st.lines))
    return [r for r in rows if r["risk"] >= VERY_HIGH_RISK * alarm]


def likely_out(st) -> list[dict]:
    """Lines the storm model gives at least LIKELY_OUT chance of being knocked down this hour, worst first."""
    from .la import replay

    try:
        storm = replay.hour(st.eid, t=st.t_utc.isoformat()).get("storm") or {}
    except Exception:  # noqa: BLE001
        return []
    return [t for t in storm.get("top_lines", []) if t["p_damage"] >= LIKELY_OUT]


def fire_text(st, row: dict) -> str:
    dw = row.get("downwind") or {}
    near = (f" {dw['hospitals']} hospital{'s' if dw['hospitals'] != 1 else ''} downwind." if dw.get("hospitals")
            else f" About {dw['people']:,} people downwind." if dw.get("people") else "")
    return f"Rewire ALERT: {_line_name(st, row['id'])} at very high fire risk, gusts {row['gust']:.0f} m/s.{near} Open Rewire to review plans."


def storm_text(st, top: dict) -> str:
    return (f"Rewire ALERT: {_line_name(st, top['id'])} is likely to be knocked down by this storm "
            f"({round(100 * top['p_damage'])}% chance). Open Rewire to stage crews.")


def _room(event: str, condition: str, t_utc) -> int:
    """How many more lines may be alerted for this condition in this window of the scenario's clock."""
    tail = f"|{condition}|{_window(t_utc)}"
    return max(0, MAX_PER_HOUR - sum(k.startswith(event + "|") and k.endswith(tail) for k in _seen))


def on_hour(st, source: str = "code") -> list[dict]:
    """Triggers (a) and (b) for the hour the system is looking at. Returns the alerts that went out (or would have).
    At most MAX_PER_HOUR lines per condition per window, worst first, so moving the clock does not text line after line."""
    out = []
    for rows, condition, text, why in ((very_high_fire(st), "fire", fire_text, "very high fire risk"),
                                       (likely_out(st), "storm", storm_text, "likely to be knocked down")):
        room = _room(st.eid, condition, st.t_utc)
        for row in rows:
            key = _key(st.eid, row["id"], condition, st.t_utc)
            if room <= 0:
                break
            if key in _seen:
                continue
            e = send(text(st, row), key=key, source=source, why=why)
            if e:
                out.append(e)
                room -= 1
    return out


def on_approval(st, plan: dict) -> dict | None:
    """Trigger (c): the operator approved a plan that turns lines off."""
    off = [t for a in plan.get("actions", []) for t in ([a["target"]] if a.get("type") == "open_line" else a.get("targets") or []
                                                         if a.get("type") == "deenergize_lines" else [])]
    if not off:
        return None
    r = plan.get("result") or {}
    n = len(off)
    text = (f"Rewire ALERT: operator approved switching off {n} line{'s' if n != 1 else ''}"
            + (f" ({', '.join(off[:3])}{' and more' if n > 3 else ''})") + f". About {r.get('customers_out', 0):,} customers lose power."
            + " Open Rewire for the bulletin.")
    return send(text, key=f"{st.eid}|{plan.get('id')}|approved|{st.t_utc.isoformat()}", source="code", why="plan approved")


# --------------------------------------------------------------------------- 2. the orchestrator's tool
def agent_alert(st, message: str, log=None) -> str:
    """What `send_alert` does. The model's wording is used only if it states no quantity; otherwise, and for the
    de-duplication key, the line it names decides the text, built from the physics like the code triggers."""
    from .agents.tools import _grid_ids, emit
    from .textguard import guard

    names = _grid_ids(st)
    text, withheld = guard(message, names, 280)
    line = next((w for w in re.findall(r"\b[A-Z]{1,3}\d+\b", str(message)) if w in st.lines), None)
    storm = bool(re.search(r"storm|knock|damage|outage", str(message), re.I)) and not re.search(r"fire", str(message), re.I)
    row = next((r for r in very_high_fire(st) if r["id"] == line), None)
    top = next((t for t in likely_out(st) if t["id"] == line), None)
    if log is not None:
        emit(log, "coordinator", "send_alert" + (f" {line}" if line else ""), "tool", agent="coordinator", tool="send_alert", line=line)
    if not row and not top:
        return "not sent: send_alert is only for a line at very high fire risk or likely to be knocked down"
    use_storm = bool(top) and (storm or not row)
    body = storm_text(st, top) if use_storm else fire_text(st, row)
    if text and not withheld and text.lower().startswith("rewire alert"):
        body = text
    e = send(body, key=_key(st.eid, line, "storm" if use_storm else "fire", st.t_utc), source="agent",
             why="likely to be knocked down" if use_storm else "very high fire risk")
    return "already alerted for this line" if e is None else f"alert {e['status']}"


def alert_tool(st, log):
    """`send_alert` as a LangChain tool for the orchestrator."""
    from langchain_core.tools import tool

    @tool
    def send_alert(message: str) -> str:
        """Text the on-call operator. Use ONLY for a line at very high fire risk or very likely to be knocked down by
        the storm. `message` names the line by id and says what is wrong in plain words, without numbers, starting
        "Rewire ALERT:". One alert per line; repeating it does nothing."""
        return agent_alert(st, message, log)

    return send_alert
