"""The FastAPI app: wires the route modules together. All state is in memory.

    routes_la.py         the real Los Angeles grid (the only routes the screen calls)
    routes_synthetic.py  the original 12-bus what-if region (still served and tested; no screen)
    jobs.py              co-pilot runs, shared by both regions
    bulletin.py          the responder bulletin built on approval
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import jobs, routes_la, routes_synthetic
from .la import data as LAD

app = FastAPI(title="Rewire")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",  # local dev on any port
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(routes_synthetic.router)
app.include_router(routes_la.router)
app.mount("/results", StaticFiles(directory=str(LAD.results_dir())), name="results")



@app.on_event("startup")
def _connect_the_strip():
    """Open the LED strip's port now (if one is plugged in), so its first scene does not wait for the ESP32 to start."""
    from . import lights

    lights.STRIP.warm()


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


# Hosted copy (Vercel): several copies of this server run at once, so they share what changes (backend/hosted.py).
if os.environ.get("VERCEL"):
    from . import hosted

    hosted.install(app)

# Hosted copy (Vercel): the same server also hands out the built screen, so the whole app is one address.
# Every route above wins over these files. Locally the screen runs on its own port (npm run dev) and this is unused.
_SCREEN = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _SCREEN.is_dir():
    app.frontend("/", directory=str(_SCREEN))
