"""Co-pilot jobs: each run plans on a background thread while the UI polls for the log and the result. In memory only."""
from __future__ import annotations

import threading
import time
import uuid

from .agents.team import run_team
from .agents.tools import JobLog

JOBS: dict[str, dict] = {}


def start(state, first_line: str | None = None) -> str:
    """Create a job and plan on `state` in the background. Returns the job id the UI polls with.
    `state` is the synthetic region or an LA state; both carry `lock`, `plans` and `plan_status`."""
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"status": "running", "log": JobLog(), "output": None, "started": time.time()}
    if first_line:
        JOBS[job_id]["log"]("system", first_line)
    threading.Thread(target=_run, args=(job_id, state), daemon=True).start()
    return job_id


def _run(job_id: str, state):
    job = JOBS[job_id]
    try:
        out = run_team(state, job["log"])
        with state.lock:
            state.plans = {p["id"]: p for p in out["plans"]}
            state.plan_status = {p["id"]: "pending" for p in out["plans"]}
        job["output"] = out
        job["status"] = "done"
    except Exception as e:  # noqa: BLE001
        job["log"]("system", f"error: {e}")
        job["status"] = "error"
        job["error"] = str(e)


def view(job_id: str) -> dict | None:
    """What the UI polls: status, the log so far, and the output once the run is done. None for an unknown id."""
    job = JOBS.get(job_id)
    if not job:
        return None
    return {"status": job["status"], "log": job["log"].entries, "output": job["output"], "error": job.get("error")}
