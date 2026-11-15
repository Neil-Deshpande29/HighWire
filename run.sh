#!/usr/bin/env bash
# Rewire: starts the backend (port 8000) and frontend (port 5173). Installs nothing; see README for setup.
set -e
cd "$(dirname "$0")"
PY="python"
[ -x .venv/bin/python ] && PY=".venv/bin/python"

# Reload only on our own code. Watching the repo root also watches .venv, and anything that touches it
# (running the tests, a pip install) restarts the server in a loop while requests hang.
$PY -m uvicorn backend.app:app --reload --reload-dir backend --reload-dir site_agent --port 8000 &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT INT TERM

cd frontend && npm run dev
