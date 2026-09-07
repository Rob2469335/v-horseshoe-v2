import logging
from typing import Any
from fastapi import Request, HTTPException
import asyncio

logger = logging.getLogger(__name__)

import os
from fastapi import Header

SWARM_API_KEY = os.getenv("SWARM_API_KEY")


async def verify_api_key(x_api_key: str | None = Header(default=None)):
    if SWARM_API_KEY and x_api_key != SWARM_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


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
