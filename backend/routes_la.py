"""Routes for the real Los Angeles grid: replays, the live forecast, physics on demand, the co-pilot and the site agent.

The screen uses only these routes. State is in memory:
    LA["state"]   the Operate state: the grid at the hour the co-pilot was last run for, plus approved plans
    LA["study"]   the latest Study copy (never read back; Study runs must not touch the Operate state)
    LA["grid"]    the grid model, built once
    _BASE         unmodified LA states per (event, hour) for POST /la/simulate
"""
from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import jobs
from .bulletin import la_bulletin
from .la import data as LAD
from .la import replay as RP

router = APIRouter()

LA: dict = {"state": None, "grid": None, "study": None}
FORECAST_JOB: dict = {"status": "idle", "error": None, "summary": None}
# Base LAState per (event, hour), reused by /la/simulate (building one takes ~0.3 s). Never mutated: every request
# simulates on it or on a with_conditions() copy. Cleared when the live forecast is refreshed.
_BASE: dict = {}


class LARunIn(BaseModel):
    event: str
    t: str | None = None
    actions: list[dict] = []  # Study mode: grid changes the agents plan on top of
    conditions: dict | None = None  # Study mode: {gust_pct, rh_pct, temp_c, demand_pct}


class LASimIn(BaseModel):
    event: str
    t: str | None = None
    actions: list[dict] = []
    conditions: dict | None = None


def _la_grid():
    if LA["grid"] is None:
        from .la.engine import LAGrid  # built on first use: it takes about a second and the synthetic routes never need it

        LA["grid"] = LAGrid()
    return LA["grid"]


def _require_event(event_id: str, detail: str = "unknown event"):
    """404 unless this is a replay event or the live forecast (FX)."""
    if event_id not in LAD.events() and event_id != "FX":
        raise HTTPException(404, detail)


# --------------------------------------------------------------------------- replays and the live forecast
@router.get("/events")
def events():
    return RP.events_list()


@router.get("/la/static")
def la_static():
    return RP.static()


@router.get("/replay/{event_id}")
def replay(event_id: str, t: str | None = None, i: int | None = None):
    _require_event(event_id, f"unknown event {event_id}")
    h = RP.hour(event_id, t, i)
    st = LA["state"]
    # the Operate state rides along when the operator has run the co-pilot for exactly this hour
    on_this_hour = st is not None and st.eid == event_id and str(st.t_utc) == str(pd.Timestamp(h["t_utc"]))
    h["copilot"] = st.view() if on_this_hour else None
    return h


@router.get("/la/series/{event_id}")
def la_series(event_id: str):
    return RP.series(event_id)


@router.post("/la/forecast/refresh")
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


@router.get("/la/forecast/status")
def la_forecast_status():
    return FORECAST_JOB


# --------------------------------------------------------------------------- site agent
@router.get("/la/site/{line_id}")
def la_site(line_id: str, event: str | None = None, i: int | None = None):
    """Site agent: data facts + NAIP aerial photo + Gemini's description (if GEMINI_API_KEY is set) for one line.
    The description is context for the operator, never an input to the physics."""
    from site_agent import agent as SA

    if event is not None and event not in LAD.events():  # replay events only; the live forecast is not accepted here
        raise HTTPException(404, "unknown event")
    from site_agent import greenery as G

    try:
        site = SA.describe(line_id, event, i)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {**site, "greenery": G.line_greenery(line_id)}  # measured from the photos just fetched or already cached


@router.get("/la/site/{line_id}/photo/{n}.jpg")
def la_site_photo(line_id: str, n: int):
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


# --------------------------------------------------------------------------- aerial imagery under the map
@router.get("/la/imagery")
def la_imagery():
    """Whether a stitched aerial picture of the region has been built (`make imagery`), and where it came from."""
    from .la import basemap

    m = basemap.meta()
    return {"available": m is not None, **(m or {"source": basemap.source()["name"]})}


@router.get("/la/imagery/basemap.jpg")
def la_imagery_picture():
    from .la import basemap

    f = basemap.path()
    if f is None:
        raise HTTPException(404, "no aerial picture yet (run `make imagery`)")
    return FileResponse(f, media_type="image/jpeg")


# --------------------------------------------------------------------------- validation
@router.get("/validation")
def validation():
    return RP.validation_summary()


@router.get("/report", response_class=PlainTextResponse)
def report():
    f = Path(__file__).resolve().parents[1] / "validation" / "REPORT.md"
    return f.read_text() if f.exists() else "Run `make validate` first."


# --------------------------------------------------------------------------- physics on demand
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
    built = sim["totals"]["buildings_out"]  # every site behind a cut-off substation, so two hospitals on one bus count as two
    load = {bid: round(st.buses[bid]["load_mw"], 1) for bid in st.buses if st.buses[bid]["load_mw"] > 0.5}
    return {
        "is_safe": sim["is_safe"], "unserved_mw": sim["totals"]["unserved_mw"], "customers_out": sim["totals"]["customers_out"],
        "critical_on_backup": crit, "hospitals_on_backup": built["hospitals"],
        "critical_names": [s["name"] for s in built["sites"]], "buildings_out": built,
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


@router.post("/la/simulate")
def la_simulate(body: LASimIn):
    """Study mode and the "Your changes" tray: physics for this moment + actions (+ condition overrides), on a copy.
    The Operate state (LA["state"]) is never touched."""
    _require_event(body.event)
    base = _base_state(body.event, body.t)
    st = base.with_conditions(body.conditions) if body.conditions else base
    return _sim_payload(st, body.actions)


# --------------------------------------------------------------------------- co-pilot: run, approve, reject
def _is_study(body: LARunIn) -> bool:
    return bool(body.actions) or any(float(v or 0) for v in (body.conditions or {}).values())


@router.post("/la/copilot/run")
def la_copilot_run(body: LARunIn):
    from .la.state import LAState

    _require_event(body.event)
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
    job_id = jobs.start(st, f"{'study of ' if study else 'replaying '}{st.scenario} on the real LA grid ({len(st.lines)} lines)")
    return {"job_id": job_id, "state": st.view(), "study": study}


@router.post("/la/plans/{plan_id}/approve")
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
        st.bulletin = la_bulletin(st, plan)
    return {"bulletin": st.bulletin, "state": st.view()}


@router.post("/la/plans/{plan_id}/reject")
def la_reject(plan_id: str):
    st = LA["state"]
    if st is None or plan_id not in st.plans:
        raise HTTPException(404, "unknown plan")
    st.plan_status[plan_id] = "rejected"
    return {"plan_status": st.plan_status}


@router.get("/la/state")
def la_state():
    return LA["state"].view() if LA["state"] else {}
