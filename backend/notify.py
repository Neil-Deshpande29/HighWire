"""Text alerts (SMS) through LangChain's Twilio tool.

Two ways in, one function out (`send`), so both obey the same on/off switch, the same de-duplication and the same dry run:

  1. Code, always on (`on_red`, `on_hour`, `on_approval`): a text when the risk watch is red and the agents are called,
     when a line's fire index is well over its calibrated alarm, when the storm model gives a line a high chance of
     being knocked down, and when the operator approves a plan that turns lines off. Every number in these texts comes
     from the physics.
  2. The orchestrator's `send_alert(message)` tool (`alert_tool`), for very high risk findings only.

Alerts are OFF by default. Switch them on with the bell's switch (until the server restarts) or with ALERTS_ON=1 in
.env. While they are off nothing is sent and nothing is recorded.

Settings come from the environment (.env, read by backend/llm.py): TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
TWILIO_FROM_NUMBER and ALERT_PHONE (who gets the texts). If any is missing, or the from-number is still a placeholder,
nothing is sent: the message is kept and shown on screen as "would have sent" (a dry run). On a Twilio trial account
only Twilio's stock templates can be sent, so the red-watch, approval and test texts go out as its stock alert (the
phone buzzes, the wording is Twilio's) and the per-line texts are recorded but not sent; see TRIAL_TEMPLATE. No number or key is ever
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
MAX_PER_HOUR = 1  # the worst line per condition per window gets a text, never the whole list
KEEP = 60  # alerts kept for the bell
SYNC = False  # tests set this: send in the calling thread instead of a background one
# A Twilio trial account may not send its own wording: the message must be one of Twilio's stock templates, named in
# place of the text. This is the one that reads as an operations alert. Our wording needs an upgraded account.
TRIAL_TEMPLATE = "sms_internal_alerts"
TRIAL_ERROR = 572006  # Twilio: "Trial accounts can only use predefined SMS templates"
TRIAL_NOTE = ("Twilio trial account: the phone got Twilio's stock alert text, not this wording. Upgrade the Twilio account to send it as written.")
TRIAL_SKIP = "Twilio trial account: only the red-watch, approval and test texts go out, as Twilio's stock alert."
_trial = {"on": False}  # learned from Twilio's first refusal

ALERTS: list[dict] = []  # newest last: {n, at_utc, message, source, status, why}
def _on_at_start() -> bool:
    """Alerts are off until someone switches them on: with the bell's switch for this run of the server, or with
    ALERTS_ON=1 in .env to start switched on. Nothing texts a phone unless it was asked to."""
    return (os.environ.get("ALERTS_ON") or "").strip().lower() in ("1", "true", "on", "yes")


STATE = {"on": _on_at_start()}
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
    return {"on": STATE["on"], "dry_run": not configured(), "missing": missing, "trial": _trial["on"],
            "to": ("number ending " + to[-4:]) if _PHONE.match(to) else None, "alerts": list(reversed(ALERTS))}


def _twilio_send(message: str, to: str) -> str:
    """LangChain's built-in Twilio wrapper; it reads the account, token and from-number from the environment."""
    from langchain_community.utilities.twilio import TwilioAPIWrapper

    return TwilioAPIWrapper().run(message, to)


def _is_trial_refusal(e: Exception) -> bool:
    return getattr(e, "code", None) == TRIAL_ERROR or "predefined SMS templates" in str(e)


def send(message: str, *, key: str | None = None, source: str = "code", why: str = "", wait: bool = False, stock_ok: bool = True) -> dict | None:
    """Send one text, or record what would have been sent. None when alerts are off or `key` was already used
    (the same line, condition and time window). `source` is "code", "agent" or "test".

    On a Twilio trial account our wording is refused. Then, when `stock_ok`, Twilio's stock alert template is sent in
    its place so the phone still gets a text, and the entry says so; otherwise the entry is recorded as not sent.
    Per-line alerts pass stock_ok=False: several copies of the same stock text would tell the operator nothing."""
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
            to = _setting("ALERT_PHONE")
            try:
                if not _trial["on"]:
                    try:
                        _twilio_send(message, to)
                        entry["status"] = "sent"
                        return
                    except Exception as e:  # noqa: BLE001
                        if not _is_trial_refusal(e):
                            raise
                        _trial["on"] = True
                if stock_ok:
                    _twilio_send(TRIAL_TEMPLATE, to)
                    entry["status"], entry["note"] = "sent", TRIAL_NOTE
                else:
                    entry["status"], entry["note"] = "not sent", TRIAL_SKIP
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
            e = send(text(st, row), key=key, source=source, why=why, stock_ok=False)
            if e:
                out.append(e)
                room -= 1
    return out


def on_red(st, watch: dict) -> dict | None:
    """The risk watch is red and the agents have been called: one text saying so, with the main reason. Once per
    window of the scenario's clock, however often the agents are called in it."""
    if watch.get("level") != "red":
        return None
    why = (watch.get("reasons") or ["A hard check is at or near its limit."])[0]
    text = f"Rewire: the risk watch is RED ({st.t_utc.tz_convert('America/Los_Angeles'):%a %-I %p}). {why} The agents are working on a plan. Open Rewire."
    return send(text, key=f"{st.eid}|watch|red|{_window(st.t_utc)}", source="code", why="risk watch is red")


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
             why="likely to be knocked down" if use_storm else "very high fire risk", stock_ok=False)
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
