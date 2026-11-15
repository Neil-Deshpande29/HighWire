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
from .la import authority as AUTH
from .la import data as LAD
from .la import record as REC
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


class LACutIn(BaseModel):
    event: str
    t: str | None = None
    line: str
    actions: list[dict] = []  # changes already made; the answer is what this line adds on top of them


class LARoleIn(BaseModel):
    role: str = "operator"  # who is acting: "operator" or "supervisor"
    reasons: list[str] = []  # on a rejection: why (ids from backend/la/feedback.py REASONS)
    note: str = ""  # on a rejection: anything else the operator wants the coordinator to know


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
    return {"available": m is not None, **(m or {"source": basemap.source()["name"]}), "surround": basemap.surround()}


@router.get("/la/imagery/surround.jpg")
def la_imagery_surround():
    """The land and sea around the region, for the space outside the grid's own picture."""
    from .la import basemap

    if basemap.surround() is None:
        raise HTTPException(404, "no surroundings yet (run `make imagery`)")
    return FileResponse(basemap.SURROUND, media_type="image/jpeg")


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
    from .la import costs
    from .la import exposure as X

    sim = st.simulate(actions)
    g = st.grid
    L = [sim["lines"][b["id"]] for b in g.branches]
    top, _, n_hot = st.fire_lines(sim)
    wxx = st.weather_extremes()
    hot = st.hot_points(sim)  # every live high-risk line, not only the ones listed
    dw = C.downwind_union([p[1:] for p in hot]) if hot else {"people": 0, "hospitals": 0, "water_plants": 0}
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
                      "people_downwind": int(dw["people"]), "hospitals_downwind": int(dw["hospitals"]),
                      "water_plants_downwind": int(dw["water_plants"])},
        "top_lines": top, "conditions": st.conditions, "t_utc": st.t_utc.isoformat(),
        # the two sides of the decision, never added together: buildings_out is what acting costs (on backup power),
        # fire_exposure is what not acting leaves downwind of the lines still live at high risk
        "fire_exposure": X.exposed([(lat, lon, wf, g.branches[k]["id"]) for k, lat, lon, wf in hot]),
        "authority": AUTH.assess(sim["is_safe"], sim["totals"]["customers_out"], built["hospitals"]),
        "cost": costs.outage_cost(sim["totals"]["customers_out"]),  # what the cut costs the customers it reaches, roughly
    }


@router.post("/la/simulate")
def la_simulate(body: LASimIn):
    """Study mode and the "Your changes" tray: physics for this moment + actions (+ condition overrides), on a copy.
    The Operate state (LA["state"]) is never touched."""
    _require_event(body.event)
    base = _base_state(body.event, body.t)
    st = base.with_conditions(body.conditions) if body.conditions else base
    return _sim_payload(st, body.actions)


@router.post("/la/risk")
def la_risk(body: LASimIn):
    """Risk register for this moment + actions: each high-risk line's likelihood and its two consequences."""
    from .la import risk

    _require_event(body.event)
    base = _base_state(body.event, body.t)
    st = base.with_conditions(body.conditions) if body.conditions else base
    return risk.register(st, body.actions)


@router.post("/la/cut")
def la_cut(body: LACutIn):
    """What loses grid power if this one line is switched off: buildings by kind and tier, apartment homes, customers.
    Topology only, so it answers for any line at once, not only the ones at high fire risk."""
    _require_event(body.event)
    st = _base_state(body.event, body.t)
    if body.line not in st.lines:
        raise HTTPException(404, f"unknown line {body.line}")
    return st.cut_impact(body.line, body.actions)


@router.get("/la/sensors/{event_id}")
def la_sensors(event_id: str, t: str | None = None):
    """Weather stations against the weather the model ran on: recorded reports for a replay hour, live ones for Now."""
    from .la import sensors

    _require_event(event_id)
    return sensors.check(_base_state(event_id, t))


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
    hour = st.t_utc.isoformat()

    def stamp(out: dict, log_entries: list[dict]):
        """Say who may approve each plan, draw how the team's work came together, and write the run into the decision
        record (Operate runs only)."""
        from .agents import synthesis
        from .la import feedback as FB

        for p in out["plans"]:
            p["authority"] = AUTH.of_result(p["result"])
        if "feedback_used" not in out:  # the AI team: the feedback went into the coordinator's instructions
            out["feedback_used"] = [{"reason": r["id"], "text": r["text"], "applied": "Given to the AI coordinator with its instructions."}
                                    for r in FB.summary()["reasons"]]
        out["synthesis"] = synthesis.build(out, log_entries)
        if study:
            return
        planner = "AI agent team" if out.get("mode") == "llm" else "rule-based planner"
        REC.add("plans", f"Co-pilot returned {len(out['plans'])} plan{'s' if len(out['plans']) != 1 else ''} from the {planner}. "
                         f"Doing nothing fails {len(out['do_nothing']['violations'])} physics check"
                         f"{'s' if len(out['do_nothing']['violations']) != 1 else ''}.",
                event=st.eid, hour_utc=hour, planner=out.get("mode"), fallback_reason=out.get("fallback_reason"),
                plans=[{"id": p["id"], "passes_physics": p["result"]["is_safe"], "customers_out": p["result"]["customers_out"],
                        "hospitals_on_backup": p["result"].get("hospitals_on_backup"), "authority": p["authority"]["level"],
                        "reasons": p["authority"]["reasons"]} for p in out["plans"]])

    if not study:
        REC.add("run", "Operator asked the co-pilot for plans.", role="operator", event=st.eid, hour_utc=hour)
    job_id = jobs.start(st, f"{'study of ' if study else 'replaying '}{st.scenario} on the real LA grid ({len(st.lines)} lines)", on_done=stamp)
    return {"job_id": job_id, "state": st.view(), "study": study}


def _role(body: LARoleIn | None) -> str:
    role = body.role if body else "operator"
    if role not in ("operator", "supervisor"):
        raise HTTPException(400, "role must be operator or supervisor")
    return role


def _plan_or_404(plan_id: str):
    st = LA["state"]
    if st is None or plan_id not in st.plans:
        raise HTTPException(404, "unknown plan (run the LA co-pilot first)")
    return st, st.plans[plan_id]


@router.post("/la/plans/{plan_id}/approve")
def la_approve(plan_id: str, body: LARoleIn | None = None):
    """Approve a plan that passed the physics check. A plan over the operator's limit (backend/la/authority.py) is
    refused unless the supervisor role approves it."""
    st, plan = _plan_or_404(plan_id)
    role = _role(body)
    if not plan["result"]["is_safe"]:
        raise HTTPException(400, "plan is not safe; it cannot be approved")
    auth = AUTH.of_result(plan["result"])
    if auth["level"] == "supervisor" and role != "supervisor":
        raise HTTPException(403, "Needs a supervisor's sign-off: " + "; ".join(auth["reasons"]) + ".")
    st.apply_actions(plan["actions"])
    with st.lock:
        for pid in st.plan_status:
            st.plan_status[pid] = "approved" if pid == plan_id else "superseded"
        st.bulletin = la_bulletin(st, plan)
    hour = st.t_utc.isoformat()
    REC.add("approved", f"Plan {plan_id.lstrip('P')} approved by the {role}.", role=role, event=st.eid, hour_utc=hour, plan=plan_id,
            authority=auth["level"], reasons=auth["reasons"], customers_out=plan["result"]["customers_out"],
            hospitals_on_backup=plan["result"].get("hospitals_on_backup"), actions=plan["actions"])
    REC.add("bulletin", "Bulletin for responders issued.", event=st.eid, hour_utc=hour, plan=plan_id)
    return {"bulletin": st.bulletin, "state": st.view()}


@router.post("/la/plans/{plan_id}/reject")
def la_reject(plan_id: str, body: LARoleIn | None = None):
    """Reject a plan. The reasons and note go to the feedback store, which the coordinator reads on later runs."""
    from .la import feedback as FB

    st, plan = _plan_or_404(plan_id)
    role = _role(body)
    st.plan_status[plan_id] = "rejected"
    hour = st.t_utc.isoformat()
    fb = FB.add(plan, body.reasons if body else [], body.note if body else "", event=st.eid, hour_utc=hour, role=role)
    why = "; ".join([FB.REASONS[r].lower() for r in fb["reasons"]] + ([f'"{fb["note"]}"'] if fb["note"] else []))
    REC.add("rejected", f"Plan {plan_id.lstrip('P')} rejected by the {role}" + (f": {why}." if why else "."), role=role, event=st.eid,
            hour_utc=hour, plan=plan_id, reasons=fb["reasons"], note=fb["note"])
    return {"plan_status": st.plan_status, "feedback": FB.summary()}


@router.get("/la/feedback")
def la_feedback():
    """What the operator said when rejecting plans, as the coordinator carries it into the next run."""
    from .la import feedback as FB

    return {**FB.summary(), "choices": FB.REASONS}


@router.delete("/la/feedback")
def la_feedback_clear():
    from .la import feedback as FB

    FB.clear()
    return FB.summary()


@router.get("/la/buildings")
def la_buildings():
    """The building records in use (CoStar where its extract is loaded, OpenStreetMap otherwise) and the priority ladder."""
    from .la import buildings as B

    return B.summary()


@router.post("/la/plans/{plan_id}/escalate")
def la_escalate(plan_id: str):
    """The operator sends a plan that is over their limit to the supervisor. Nothing is switched."""
    st, plan = _plan_or_404(plan_id)
    auth = AUTH.of_result(plan["result"])
    if auth["level"] != "supervisor":
        raise HTTPException(400, "this plan does not need a supervisor")
    st.plan_status[plan_id] = "escalated"
    REC.add("escalated", f"Plan {plan_id.lstrip('P')} sent to the supervisor: " + "; ".join(auth["reasons"]) + ".", role="operator",
            event=st.eid, hour_utc=st.t_utc.isoformat(), plan=plan_id, reasons=auth["reasons"])
    return {"plan_status": st.plan_status, "authority": auth}


# --------------------------------------------------------------------------- decision record
@router.get("/la/record")
def la_record():
    return {"entries": REC.entries(), "basis": AUTH.BASIS}


@router.post("/la/reset")
def la_reset():
    """Start the session over: forget the Operate state (plans, approvals, bulletin) and clear the record."""
    LA["state"], LA["study"] = None, None
    REC.clear()
    REC.add("reset", "Session reset: plans, approvals and the earlier record were cleared.")
    return {"entries": REC.entries()}


@router.get("/la/state")
def la_state():
    return LA["state"].view() if LA["state"] else {}
