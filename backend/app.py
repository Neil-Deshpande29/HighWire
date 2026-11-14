"""FastAPI routes. In-memory state only."""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import grid_data, llm as LLM, reports
from .agents.team import run_team
from .agents.tools import JobLog
from .state import STATE

app = FastAPI(title="Highwire")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",  # local dev on any port
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: dict[str, dict] = {}
_TERRAIN = grid_data.heightmap(64)
SAFETY_LINE = "Crews must verify on site. Treat all downed wires as live."


class ScenarioIn(BaseModel):
    name: str


class ReportIn(BaseModel):
    text: str
    x: float | None = None
    y: float | None = None


def _state_out():
    v = STATE.view()
    v["mode"] = "llm" if LLM.enabled() else "mock"
    return v


@app.get("/state")
def get_state():
    return _state_out()


@app.get("/terrain")
def get_terrain():
    return _TERRAIN


@app.post("/scenario")
def set_scenario(body: ScenarioIn):
    try:
        STATE.set_scenario(body.name)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e
    return _state_out()


@app.post("/lines/{line_id}/toggle")
def toggle_line(line_id: str):
    if line_id not in STATE.lines:
        raise HTTPException(404, f"unknown line {line_id}")
    STATE.toggle_line(line_id)
    return _state_out()


@app.post("/reports")
def add_report(body: ReportIn):
    if not body.text.strip():
        raise HTTPException(400, "empty report")
    rep = reports.add_report(STATE, body.text.strip(), body.x, body.y)
    return {"report": rep, "state": _state_out()}


@app.post("/reset")
def reset():
    STATE.reset("Normal")
    JOBS.clear()
    return _state_out()


# --------------------------------------------------------------------------- co-pilot jobs
def _run_job(job_id: str):
    job = JOBS[job_id]
    try:
        out = run_team(STATE, job["log"])
        with STATE.lock:
            STATE.plans = {p["id"]: p for p in out["plans"]}
            STATE.plan_status = {p["id"]: "pending" for p in out["plans"]}
        job["output"] = out
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["log"]("system", f"error: {e}")
        job["status"] = "error"
        job["error"] = str(e)


@app.post("/copilot/run")
def copilot_run():
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "log": JobLog(), "output": None, "started": time.time()}
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/copilot/{job_id}")
def copilot_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return {"status": job["status"], "log": job["log"].entries, "output": job["output"], "error": job.get("error")}


# --------------------------------------------------------------------------- approve / reject / bulletin
def make_bulletin(plan: dict) -> dict:
    sim = STATE.sim
    off = [
        {"id": l, "area": f"{STATE.lines[l]['zone']} ({STATE.buses[STATE.lines[l]['from_bus']]['name']} - {STATE.buses[STATE.lines[l]['to_bus']]['name']})"}
        for l in STATE.operator_opened
        if STATE.lines[l]["status"] == "open"
    ]
    reported = {}
    for r in STATE.reports_view():
        if r.get("line") and sim["lines"][r["line"]]["energized"]:
            reported.setdefault(r["line"], []).append(f"{r['kind']}: \"{r['text'][:70]}\"")
    energized_reported = [{"id": l, "reports": v} for l, v in reported.items()]
    subs = []
    for bid, b in sim["buses"].items():
        if b["flooded"] or b["deenergized"]:
            state = "de-energized" if b["deenergized"] else "ENERGIZED"
            subs.append({"id": bid, "name": STATE.buses[bid]["name"], "flooded": b["flooded"], "state": state})
    backup = [
        {"id": bid, "name": STATE.buses[bid]["name"], "hours_left": STATE.buses[bid]["backup_hours"]}
        for bid in sim["totals"]["critical_on_backup"]
    ]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines_txt = [f"GRID CO-PILOT RESPONDER BULLETIN  {ts}", f"Scenario: {STATE.scenario}.  Plan approved: {plan['id']} - {plan['title']}", ""]
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


@app.post("/plans/{plan_id}/approve")
def approve(plan_id: str):
    plan = STATE.plans.get(plan_id)
    if not plan:
        raise HTTPException(404, "unknown plan (run the co-pilot first)")
    if not plan["result"]["is_safe"]:
        raise HTTPException(400, "plan is not safe; it cannot be approved")
    if STATE.plan_status.get(plan_id) == "approved":
        raise HTTPException(400, "plan already approved")
    STATE.apply_actions(plan["actions"])
    with STATE.lock:
        for pid in STATE.plan_status:
            STATE.plan_status[pid] = "approved" if pid == plan_id else "superseded"
        STATE.bulletin = make_bulletin(plan)
    return {"bulletin": STATE.bulletin, "state": _state_out()}


@app.post("/plans/{plan_id}/reject")
def reject(plan_id: str):
    if plan_id not in STATE.plans:
        raise HTTPException(404, "unknown plan")
    STATE.plan_status[plan_id] = "rejected"
    return {"plan_status": STATE.plan_status}


@app.get("/bulletin")
def bulletin():
    return STATE.bulletin or {}


# =========================================================================== Phase 2: real LA replay + validation
from pathlib import Path  # noqa: E402

from .la import data as LAD  # noqa: E402
from .la import replay as RP  # noqa: E402

_RESULTS = LAD.results_dir()
app.mount("/results", StaticFiles(directory=str(_RESULTS)), name="results")
LA: dict = {"state": None, "grid": None, "study": None}


def _la_grid():
    if LA["grid"] is None:
        from .la.engine import LAGrid

        LA["grid"] = LAGrid()
    return LA["grid"]


@app.get("/events")
def events():
    return RP.events_list()


@app.get("/la/static")
def la_static():
    return RP.static()


@app.get("/replay/{event_id}")
def replay(event_id: str, t: str | None = None, i: int | None = None):
    if event_id not in LAD.events() and event_id != "FX":
        raise HTTPException(404, f"unknown event {event_id}")
    h = RP.hour(event_id, t, i)
    st = LA["state"]
    h["copilot"] = st.view() if st is not None and st.eid == event_id and str(st.t_utc) == str(__import__("pandas").Timestamp(h["t_utc"])) else None
    return h


@app.get("/la/series/{event_id}")
def la_series(event_id: str):
    return RP.series(event_id)


FORECAST_JOB: dict = {"status": "idle", "error": None, "summary": None}


@app.post("/la/forecast/refresh")
def la_forecast_refresh():
    """Fetch the current Open-Meteo forecast and rebuild the live FX replay in the background (~1 min)."""
    from .la import forecast as FCST

    if FORECAST_JOB["status"] == "running":
        return FORECAST_JOB

    def work():
        try:
            FORECAST_JOB["summary"] = FCST.refresh()
            for k in [k for k in _BASE if k[0] == "FX"]:
                _BASE.pop(k, None)
            FORECAST_JOB["status"], FORECAST_JOB["error"] = "done", None
        except Exception as e:  # noqa: BLE001
            FORECAST_JOB["status"], FORECAST_JOB["error"] = "error", str(e)[:300]

    FORECAST_JOB.update(status="running", error=None)
    threading.Thread(target=work, daemon=True).start()
    return FORECAST_JOB


@app.get("/la/forecast/status")
def la_forecast_status():
    return FORECAST_JOB


@app.get("/la/site/{line_id}")
def la_site(line_id: str, event: str | None = None, i: int | None = None):
    """Site agent: data facts + NAIP aerial photo + Gemini's description (if GEMINI_API_KEY is set) for one line.
    The description is context for the operator, never an input to the physics."""
    from site_agent import agent as SA

    if event is not None and event not in LAD.events():
        raise HTTPException(404, "unknown event")
    try:
        return SA.describe(line_id, event, i)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/la/site/{line_id}/photo/{n}.jpg")
def la_site_photo(line_id: str, n: int):
    from fastapi.responses import FileResponse

    from site_agent import facts as SF
    from site_agent import naip

    try:
        SF.branch(line_id)  # only real line ids reach the file system
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    t = naip.cached_tile(line_id, n) if 0 <= n < 8 else None
    if not t:
        raise HTTPException(404, "no cached photo (GET /la/site/{line_id} fetches the line's photos)")
    return FileResponse(t["img"], media_type="image/jpeg")


@app.get("/validation")
def validation():
    return RP.validation_summary()


@app.get("/report", response_class=PlainTextResponse)
def report():
    f = Path(__file__).resolve().parents[1] / "validation" / "REPORT.md"
    return f.read_text() if f.exists() else "Run `make validate` first."


class LARunIn(BaseModel):
    event: str
    t: str | None = None
    actions: list[dict] = []  # Study mode: grid changes the agents plan on top of
    conditions: dict | None = None  # Study mode: {gust_pct, rh_pct, temp_c, demand_pct}


def _is_study(body) -> bool:
    return bool(body.actions) or any(float(v or 0) for v in (body.conditions or {}).values())


# Base LAState per (event, hour), reused by /la/simulate (building one takes ~0.3 s). Never mutated: every request
# simulates on it or on a with_conditions() copy. Cleared when the live forecast is refreshed.
_BASE: dict = {}


def _base_state(event: str, t: str | None):
    from .la.state import LAState

    key = (event, t or "")
    if key not in _BASE:
        if len(_BASE) > 24:
            _BASE.pop(next(iter(_BASE)))
        _BASE[key] = LAState(event, t, grid=_la_grid())
    return _BASE[key]


def _sim_payload(st, actions: list[dict]) -> dict:
    """Plan-result shape + per-line arrays (branch order) + situation numbers, for the map and the changes tray."""
    from .la import consequence as C

    sim = st.simulate(actions)
    g = st.grid
    L = [sim["lines"][b["id"]] for b in g.branches]
    top, pts, n_hot = st.fire_lines(sim)
    wxx = st.weather_extremes()
    dw = C.downwind_union(pts) if pts else {"people": 0, "hospitals": 0, "water_plants": 0}
    crit = sim["totals"]["critical_on_backup"]
    load = {bid: round(st.buses[bid]["load_mw"], 1) for bid in st.buses if st.buses[bid]["load_mw"] > 0.5}
    return {
        "is_safe": sim["is_safe"], "unserved_mw": sim["totals"]["unserved_mw"], "customers_out": sim["totals"]["customers_out"],
        "critical_on_backup": crit, "hospitals_on_backup": int(sum(st.buses[b]["critical_type"] == "hospital" for b in crit)),
        "critical_names": [st.buses[b]["critical_name"] for b in crit],
        "violations": sim["violations"][:40], "n_violations": len(sim["violations"]), "watch": sim["watch"], "n1_warnings": [],
        "lines": {"risk": [l["fire_risk"] for l in L], "loading": [l["loading_pct"] for l in L], "energized": [l["energized"] for l in L],
                  "gust": [l["gust_ms"] for l in L], "rh": [l["rh_pct"] for l in L], "temp": [l["temp_c"] for l in L]},
        "buses_out": [bid for bid, b in sim["buses"].items() if b["unserved_mw"] > 0.05],
        "bus_load": load,
        "situation": {"n_high_risk": n_hot, "max_loading": round(max(l["loading_pct"] for l in L), 0),
                      "n_overloaded": int(sum(bool(l["energized"]) and l["loading_pct"] > 100 for l in L)), "load_mw": sim["totals"]["load_mw"],
                      **wxx,
                      "people_downwind": int(dw["people"]), "hospitals_downwind": int(dw["hospitals"])},
        "top_lines": top, "conditions": st.conditions, "t_utc": st.t_utc.isoformat(),
    }


class LASimIn(BaseModel):
    event: str
    t: str | None = None
    actions: list[dict] = []
    conditions: dict | None = None


@app.post("/la/simulate")
def la_simulate(body: LASimIn):
    """Study mode and the "Your changes" tray: physics for this moment + actions (+ condition overrides), on a copy.
    The Operate state (LA["state"]) is never touched."""
    if body.event not in LAD.events() and body.event != "FX":
        raise HTTPException(404, "unknown event")
    base = _base_state(body.event, body.t)
    st = base.with_conditions(body.conditions) if body.conditions else base
    return _sim_payload(st, body.actions)


def _run_la_job(job_id: str, st=None):
    job = JOBS[job_id]
    try:
        st = st or LA["state"]
        out = run_team(st, job["log"])
        with st.lock:
            st.plans = {p["id"]: p for p in out["plans"]}
            st.plan_status = {p["id"]: "pending" for p in out["plans"]}
        job["output"] = out
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["log"]("system", f"error: {e}")
        job["status"] = "error"
        job["error"] = str(e)


@app.post("/la/copilot/run")
def la_copilot_run(body: LARunIn):
    from .la.state import LAState

    if body.event not in LAD.events() and body.event != "FX":
        raise HTTPException(404, "unknown event")
    st = LAState(body.event, body.t, grid=_la_grid())
    study = _is_study(body)
    if study:  # plan on a study copy; the Operate state (and its plans / approvals) is left alone
        st = st.with_conditions(body.conditions)
        if body.actions:
            st.apply_actions(body.actions)
            st.scenario = st.scenario if "[STUDY" in st.scenario else f"{st.scenario} [STUDY]"
        LA["study"] = st
    else:
        LA["state"] = st
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "log": JobLog(), "output": None, "started": time.time()}
    JOBS[job_id]["log"]("system", f"{'study of ' if study else 'replaying '}{st.scenario} on the real LA grid ({len(st.lines)} lines)")
    threading.Thread(target=_run_la_job, args=(job_id, st), daemon=True).start()
    return {"job_id": job_id, "state": st.view(), "study": study}


def _la_bulletin(st, plan):
    g = st.grid
    sim = st.sim
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"HIGHWIRE RESPONDER BULLETIN  {ts}", f"{'Forecast' if st.eid == 'FX' else 'Replay'}: {st.scenario}.  Plan approved: {plan['id']} - {plan['title']}", ""]
    lines.append("LINES SWITCHED OFF BY OPERATOR:")
    off = [st.lines[l] for l in st.operator_opened if l in st.lines]
    lines += [f"  - {l['id']} {l['kv']:.0f} kV {l['zone']}{(' (' + l['name'] + ')') if l['name'] else ''}" for l in off[:40]] or ["  - none"]
    if len(off) > 40:
        lines.append(f"  - ... and {len(off) - 40} more")
    hot = sorted([v for v in sim["lines"].values() if v["energized"] and v["fire_risk"] >= LAD.calibration()["fire"]["line_alarm"]], key=lambda v: -v["fire_risk"])
    lines.append("LINES STILL ENERGIZED ABOVE THE FIRE ALARM (assume LIVE, patrol):")
    lines += [f"  - {v['id']}: risk {v['fire_risk']:.2f}, gust {v['gust_ms']:.0f} m/s" for v in hot[:20]] or ["  - none"]
    lines.append("CRITICAL SITES ON BACKUP POWER:")
    lines += [f"  - {st.buses[b]['critical_name']} (bus {b}): about {st.buses[b]['backup_hours']:.0f} h of backup" for b in sim["totals"]["critical_on_backup"]] or ["  - none"]
    lines += ["", f"Unserved load: {sim['totals']['unserved_mw']} MW (~{sim['totals']['customers_out']:,} customers, modeled).",
              "Electrical values are modeled from voltage class; weather is reanalysis.", "", SAFETY_LINE]
    return {"timestamp": ts, "plan_id": plan["id"], "safety": SAFETY_LINE, "text": "\n".join(lines)}


@app.post("/la/plans/{plan_id}/approve")
def la_approve(plan_id: str):
    st = LA["state"]
    if st is None or plan_id not in st.plans:
        raise HTTPException(404, "unknown plan (run the LA co-pilot first)")
    plan = st.plans[plan_id]
    if not plan["result"]["is_safe"]:
        raise HTTPException(400, "plan is not safe; it cannot be approved")
    st.apply_actions(plan["actions"])
    with st.lock:
        for pid in st.plan_status:
            st.plan_status[pid] = "approved" if pid == plan_id else "superseded"
        st.bulletin = _la_bulletin(st, plan)
    return {"bulletin": st.bulletin, "state": st.view()}


@app.post("/la/plans/{plan_id}/reject")
def la_reject(plan_id: str):
    st = LA["state"]
    if st is None or plan_id not in st.plans:
        raise HTTPException(404, "unknown plan")
    st.plan_status[plan_id] = "rejected"
    return {"plan_status": st.plan_status}


@app.get("/la/state")
def la_state():
    return LA["state"].view() if LA["state"] else {}
