"""The hosted copy only (Vercel). Local runs never load this.

Vercel runs several copies of this server at once and sends each request to any one of them. Rewire keeps its state in
memory (the hour the agents were called on, their plans, approvals, the record), so one copy would start a run and the
next request, asking for its result, would reach a copy that never heard of it.

So each action that changes state is written to one shared list, and every copy replays the actions it has not seen
before it answers. The rule-driven agent team gives the same plans for the same hour every time, which is what makes
replaying safe: the copies end up in the same state.

The list is kept short. It starts over after MAX_ACTIONS actions or IDLE_S seconds without one, and then every copy
goes back to a clean session, the same as pressing reset.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from . import jobs

MAX_ACTIONS = 12  # a new copy replays the whole list before its first answer, and an agent run takes a second or two
IDLE_S = 30 * 60  # the shared list is dropped after this long without an action
KEY = "session"

# Requests that change state: these are the ones written to the list and replayed.
ACTIONS = [("POST", re.compile(p)) for p in (r"^/la/copilot/run$", r"^/la/plans/[^/]+/(approve|reject|escalate)$",
                                             r"^/alerts/(switch|test)$", r"^/reports$")] + [("DELETE", re.compile(r"^/la/feedback$"))]
RESET = ("POST", "/la/reset")
# Requests that read state: the copy catches up before answering these. Everything else is worked out from the data.
READS = ("/copilot/", "/la/state", "/la/record", "/la/feedback", "/alerts", "/bulletin", "/replay/", "/report")

_REPLAY = uuid.uuid4().hex  # marks this copy's own replayed requests, so they are not written to the list again
_seen = {"session": None, "n": 0}  # which list this copy has applied, and how far
_lock: asyncio.Lock | None = None


class _VercelCache:
    """Vercel's Runtime Cache: shared by every copy of the server in the region."""

    def __init__(self):
        from vercel.functions import AsyncRuntimeCache

        self.cache = AsyncRuntimeCache(namespace="rewire")

    async def get(self):
        return await self.cache.get(KEY)

    async def put(self, log):
        await self.cache.set(KEY, log, {"ttl": IDLE_S})


class _FileStore:
    """One JSON file, for trying this out with two local servers (REWIRE_SHARED_FILE)."""

    def __init__(self, path: str):
        self.path = Path(path)

    async def get(self):
        try:
            log = json.loads(self.path.read_text())
            return log if time.time() - log.get("at", 0) < IDLE_S else None
        except (OSError, ValueError):
            return None

    async def put(self, log):
        self.path.write_text(json.dumps({**log, "at": time.time()}))


def _is_action(method: str, path: str) -> bool:
    return any(m == method and p.match(path) for m, p in ACTIONS)


def _job_id(session: str, n: int) -> str:
    """The id of the run started by action n: every copy must give it the same one."""
    return hashlib.sha1(f"{session}:{n}".encode()).hexdigest()[:8]


def install(app) -> None:
    """Keep this server's copies in step. Call once, after the routes are added."""
    import httpx
    from starlette.requests import Request

    from . import notify

    notify.SYNC = True  # a text is logged before the answer goes out: a paused copy would never finish a background send
    store = _FileStore(os.environ["REWIRE_SHARED_FILE"]) if os.environ.get("REWIRE_SHARED_FILE") else _VercelCache()

    async def replay(method: str, path: str, body: str | None = None, job_id: str | None = None) -> None:
        jobs.FORCE_ID = job_id
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://replay", timeout=120) as c:
                await c.request(method, path, content=body, headers={"content-type": "application/json", "x-rewire-replay": _REPLAY})
        finally:
            jobs.FORCE_ID = None

    async def clean() -> None:
        """Back to a fresh session on this copy."""
        await replay(*RESET)
        await replay("DELETE", "/la/feedback")
        jobs.JOBS.clear()
        _seen.update(session=None, n=0)

    async def catch_up() -> dict:
        """Replay what this copy has not seen. Returns the shared list as it stands."""
        log = await store.get() or {"session": None, "actions": []}
        if log["session"] != _seen["session"]:  # another copy started a new session, or the old one ran out
            if _seen["session"] is not None:
                await clean()
            _seen.update(session=log["session"], n=0)
        for n in range(_seen["n"], len(log["actions"])):
            a = log["actions"][n]
            await replay(a["method"], a["path"], a["body"], _job_id(log["session"], n))
            _seen["n"] = n + 1
        return log

    async def ready_for_action(reset: bool) -> dict | None:
        """Catch up, and start a new shared list when one is due. None when the shared list cannot be reached: the
        request is then answered by this copy alone, as it would be locally."""
        try:
            log = await catch_up()
            if reset or log["session"] is None or len(log["actions"]) >= MAX_ACTIONS:
                if _seen["session"] is not None and not reset:
                    await clean()
                log = {"session": uuid.uuid4().hex[:12], "actions": []}
            return log
        except Exception:  # noqa: BLE001
            return None

    @app.middleware("http")
    async def in_step(request: Request, call_next):
        global _lock
        method, path = request.method, request.url.path
        if request.headers.get("x-rewire-replay") == _REPLAY:
            return await call_next(request)
        action, reset = _is_action(method, path), (method, path) == RESET
        if not (action or reset or (method == "GET" and path.startswith(READS))):
            return await call_next(request)
        if _lock is None:
            _lock = asyncio.Lock()
        if not (action or reset):
            async with _lock:
                try:
                    await catch_up()
                except Exception:  # noqa: BLE001  the shared list is a help, never a reason to refuse a request
                    pass
            return await call_next(request)
        body = (await request.body()).decode() or None
        async with _lock:
            log = await ready_for_action(reset)
            if log is None:
                return await call_next(request)
            jobs.FORCE_ID = _job_id(log["session"], len(log["actions"]))
            try:
                response = await call_next(request)
            finally:
                jobs.FORCE_ID = None
            if not reset and response.status_code >= 300:
                return response  # it changed nothing: nothing to share
            try:
                if reset:
                    await replay("DELETE", "/la/feedback")
                    jobs.JOBS.clear()
                else:
                    full = path + (f"?{request.url.query}" if request.url.query else "")
                    log["actions"].append({"method": method, "path": full, "body": body})
                await store.put(log)
                _seen.update(session=log["session"], n=len(log["actions"]))
            except Exception:  # noqa: BLE001
                pass
            return response
