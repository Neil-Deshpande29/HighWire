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
LA: dict = {"state": None, "grid": None}


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
            FORECAST_JOB["status"], FORECAST_JOB["error"] = "done", None
        except Exception as e:  # noqa: BLE001
            FORECAST_JOB["status"], FORECAST_JOB["error"] = "error", str(e)[:300]

    FORECAST_JOB.update(status="running", error=None)
    threading.Thread(target=work, daemon=True).start()
    return FORECAST_JOB


@app.get("/la/forecast/status")
def la_forecast_status():
    return FORECAST_JOB


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


def _run_la_job(job_id: str):
    job = JOBS[job_id]
    try:
        st = LA["state"]
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
    LA["state"] = LAState(body.event, body.t, grid=_la_grid())
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "log": JobLog(), "output": None, "started": time.time()}
    JOBS[job_id]["log"]("system", f"replaying {LA['state'].scenario} on the real LA grid ({len(LA['state'].lines)} lines)")
    threading.Thread(target=_run_la_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id, "state": LA["state"].view()}


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
