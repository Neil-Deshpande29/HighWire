"""The risk watch: a cheap score that runs in the background and tells the operator when to call the agents.

It is one equation over a handful of hard checks, read straight from the hour's numbers (no power flow, no agents, no
model call), so it can be worked out for every hour as the clock moves:

    check        value                                   1.0 means
    fire         the riskiest overhead line's fire index  it is at the calibrated line alarm
    loading      the busiest line's share of its rating   it is at 100% of its rating
    storm        the storm-damage index                   it is at the calibrated storm alarm
    wind         the strongest gust on any overhead line  GUST_ACT_MS
    heat         the highest air temperature              TEMP_ACT_C

Each check is also projected LOOKAHEAD_H hours ahead from its rate of change over the last TREND_H hours (only hours
already past are used: no hindsight), and a check counts as the larger of what it is now and where it is heading. The
score is the largest check, times 100.

    green   below YELLOW      nothing to do
    yellow  YELLOW to RED     keep an eye on it
    red     RED and above     call the agents

Red is deliberately early. It means "something is at or near a limit, or heading there fast: have the agents look", not
"a fire is certain". The agents and the physics check decide what, if anything, to do; a red watch can end in a small
fix or in none.
"""
from __future__ import annotations

import numpy as np

from . import data as D
from . import replay as RP

GUST_ACT_MS = 25.0  # gusts this strong are a hard check on their own (about 56 mph)
TEMP_ACT_C = 40.0  # air temperature this high is a hard check on its own
YELLOW = 0.6  # a check at 60% of its limit: keep an eye on it
RED = 0.9  # a check at 90% of its limit, now or projected: call the agents
TREND_H = 3  # hours of history the rate of change is taken over
LOOKAHEAD_H = 2  # how far ahead a rising check is projected
RISING = 0.03  # a check counts as "rising" when it gains at least this much of its limit per hour

LABEL = {"fire": "fire risk on the riskiest line", "loading": "load on the busiest line", "storm": "storm damage",
         "wind": "wind gusts", "heat": "air temperature"}


def _values(d: dict, h: int, overhead: np.ndarray, alarm: float, storm_alarm: float | None) -> dict[str, float]:
    """Each check at hour index h, as a share of its limit."""
    out = {
        "fire": float(np.nanmax(d["line_risk"][h][overhead])) / alarm if overhead.any() else 0.0,
        "loading": float(np.nanmax(d["loading"][h])) / 100.0,
        "wind": float(np.nanmax(d["line_gust"][h][overhead])) / GUST_ACT_MS if overhead.any() else 0.0,
        "heat": float(np.nanmax(d["wx_temp"][h])) / TEMP_ACT_C,
    }
    if storm_alarm and "storm_index" in d:
        out["storm"] = float(d["storm_index"][h]) / storm_alarm
    return {k: (0.0 if not np.isfinite(v) else max(v, 0.0)) for k, v in out.items()}


def _reason(name: str, now: float, per_h: float, d: dict, h: int) -> str:
    rising = f", rising about {round(100 * per_h)} points an hour" if per_h >= RISING else ""
    if name == "fire":
        return f"The riskiest line is at {round(100 * now)}% of its fire alarm{rising}."
    if name == "loading":
        return f"The busiest line is at {round(100 * now)}% of its rating{rising}."
    if name == "storm":
        return f"The storm-damage index is at {round(100 * now)}% of its alarm{rising}."
    if name == "wind":
        gust = float(np.nanmax(d["line_gust"][h]))
        return f"Gusts on the lines reach {gust:.0f} m/s ({gust * 2.237:.0f} mph)" + (f", rising about {per_h * GUST_ACT_MS:.0f} m/s an hour." if per_h >= RISING else ".")
    return f"Air temperature reaches {float(np.nanmax(d['wx_temp'][h])):.0f} C" + (f", rising about {per_h * TEMP_ACT_C:.0f} C an hour." if per_h >= RISING else ".")


def at(eid: str, t: str | None = None, i: int | None = None) -> dict:
    """The watch for one hour: level ("green", "yellow", "red"), score (100 = a limit is reached), the reasons in plain
    words, worst first, and every check with its value now, its rate of change per hour and where it is heading."""
    d = RP.load(eid)
    h = RP._hour_index(d, t, i)
    cal = D.calibration()
    alarm = cal["fire"]["line_alarm"]
    storm_alarm = (cal.get("storm") or {}).get("alarm")
    overhead = np.array([not b["cable"] for b in D.grid()["branches"]])
    now = _values(d, h, overhead, alarm, storm_alarm)
    back = min(TREND_H, h)
    then = _values(d, h - back, overhead, alarm, storm_alarm) if back else now
    checks = []
    for name, v in now.items():
        per_h = (v - then[name]) / back if back else 0.0
        ahead = v + max(per_h, 0.0) * LOOKAHEAD_H
        checks.append({"name": name, "label": LABEL[name], "now": round(v, 3), "per_hour": round(per_h, 3), "heading": round(ahead, 3),
                       "counts": round(max(v, ahead), 3)})
    checks.sort(key=lambda c: -c["counts"])
    top = checks[0]["counts"]
    level = "red" if top >= RED else "yellow" if top >= YELLOW else "green"
    reasons = [_reason(c["name"], c["now"], c["per_hour"], d, h) for c in checks if c["counts"] >= YELLOW][:3]
    return {"level": level, "score": int(round(100 * top)), "reasons": reasons, "checks": checks,
            "yellow_from": int(100 * YELLOW), "red_from": int(100 * RED), "t_utc": str(np.datetime_as_string(d["time"][h], unit="h")) + ":00:00+00:00"}


def trend_line(eid: str, t: str | None = None) -> str:
    """One line for the agents: which checks are moving, and how fast. Empty when nothing is moving."""
    w = at(eid, t)
    moving = [f"{c['label']} {'up' if c['per_hour'] > 0 else 'down'} {abs(round(100 * c['per_hour']))} points an hour"
              for c in w["checks"] if abs(c["per_hour"]) >= RISING]
    return ("Trend over the last hours (100 points = a limit): " + "; ".join(moving) + ".") if moving else ""
