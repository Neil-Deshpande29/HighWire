#!/usr/bin/env bash
# Highwire: starts the backend (port 8000) and frontend (port 5173). Installs nothing; see README for setup.
set -e
cd "$(dirname "$0")"
PY="python"
[ -x .venv/bin/python ] && PY=".venv/bin/python"

$PY -m uvicorn backend.app:app --reload --port 8000 &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT INT TERM

cd frontend && npm run dev
