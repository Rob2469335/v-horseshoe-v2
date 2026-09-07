import logging
from typing import Any
from fastapi import Request, HTTPException
import asyncio

logger = logging.getLogger(__name__)

import os
import secrets
from fastapi import Header

# Read the key at call time (not import time): .env is loaded by the app
# lifespan / dotenv bootstrap, so a key set there must not be missed because
# dependencies.py was imported first. Reading per-request is cheap (env lookup).
def _api_key() -> str | None:
    k = os.getenv("SWARM_API_KEY") or os.getenv("SWARM_API_TOKEN") or ""
    return k.strip() or None


async def verify_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    """Gates sensitive routes. Accepts either `X-API-Key: <k>` or
    `Authorization: Bearer <k>` (the CLI already sends Bearer). Fail-open by
    default: with NO key set, all requests pass (single-user loopback dev,
    matching the main.py SWARM_API_TOKEN middleware posture). With a key set,
    any request missing it gets 401. Constant-time compare."""

    key = _api_key()
    if key is None:
        return  # no key configured -> fail open (documented local-dev posture)

    supplied = None
    if x_api_key:
        supplied = x_api_key.strip()
    elif authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:].strip()

    if not supplied or not secrets.compare_digest(supplied, key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def runtime_dep(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        logger.error("Runtime is unavailable")
        raise HTTPException(status_code=503, detail="runtime unavailable")
    return runtime


def get_orchestrator(request: Request) -> Any:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        runtime = getattr(request.app.state, "runtime", None)
        if runtime:
            orchestrator = getattr(runtime, "orchestrator", None)
    if orchestrator is None:
        logger.error("Orchestrator is unavailable")
        raise HTTPException(status_code=503, detail="orchestrator unavailable")
    return orchestrator


async def _safe_events(runtime: Any) -> list[Any]:
    try:
        event_store = getattr(runtime, "event_store", None)
        if event_store is None:
            return []
        if hasattr(event_store, "tail"):
            return await asyncio.to_thread(event_store.tail, 500)
        elif hasattr(event_store, "read_all"):
            return await asyncio.to_thread(event_store.read_all)
        elif hasattr(event_store, "list_all"):
            return await asyncio.to_thread(event_store.list_all)
        return []
    except Exception as e:
        logger.error(f"Error reading events: {e}")
        return []
