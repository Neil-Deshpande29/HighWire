"""Routes for the synthetic 12-bus region (the original what-if demo: ridge, valley and coast zones, preset weather).

No screen calls these today, but they are still served and the planner tests run against this region."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import grid_data, jobs, llm as LLM, reports
from .bulletin import synthetic_bulletin
from .state import STATE

router = APIRouter()
_TERRAIN = grid_data.heightmap(64)


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


@router.get("/state")
def get_state():
    return _state_out()


@router.get("/terrain")
def get_terrain():
    return _TERRAIN


@router.post("/scenario")
def set_scenario(body: ScenarioIn):
    try:
        STATE.set_scenario(body.name)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e
    return _state_out()


@router.post("/lines/{line_id}/toggle")
def toggle_line(line_id: str):
    if line_id not in STATE.lines:
        raise HTTPException(404, f"unknown line {line_id}")
    STATE.toggle_line(line_id)
    return _state_out()


@router.post("/reports")
def add_report(body: ReportIn):
    if not body.text.strip():
        raise HTTPException(400, "empty report")
    rep = reports.add_report(STATE, body.text.strip(), body.x, body.y)
    return {"report": rep, "state": _state_out()}


@router.post("/reset")
def reset():
    STATE.reset("Normal")
    jobs.JOBS.clear()
    return _state_out()


@router.post("/copilot/run")
def copilot_run():
    return {"job_id": jobs.start(STATE)}


@router.post("/plans/{plan_id}/approve")
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
        STATE.bulletin = synthetic_bulletin(STATE, plan)
    return {"bulletin": STATE.bulletin, "state": _state_out()}


@router.post("/plans/{plan_id}/reject")
def reject(plan_id: str):
    if plan_id not in STATE.plans:
        raise HTTPException(404, "unknown plan")
    STATE.plan_status[plan_id] = "rejected"
    return {"plan_status": STATE.plan_status}


@router.get("/bulletin")
def bulletin():
    return STATE.bulletin or {}
