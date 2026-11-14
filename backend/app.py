"""The FastAPI app: wires the route modules together. All state is in memory.

    routes_la.py         the real Los Angeles grid (the only routes the screen calls)
    routes_synthetic.py  the original 12-bus what-if region (still served and tested; no screen)
    jobs.py              co-pilot runs, shared by both regions
    bulletin.py          the responder bulletin built on approval
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import jobs, routes_la, routes_synthetic
from .la import data as LAD

app = FastAPI(title="Highwire")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",  # local dev on any port
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(routes_synthetic.router)
app.include_router(routes_la.router)
app.mount("/results", StaticFiles(directory=str(LAD.results_dir())), name="results")

# Same objects the routes use; tests set up and inspect the Operate state through backend.app.LA.
LA = routes_la.LA
JOBS = jobs.JOBS


@app.get("/copilot/{job_id}")
def copilot_status(job_id: str):
    """Poll a co-pilot run from either region."""
    job = jobs.view(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return job
