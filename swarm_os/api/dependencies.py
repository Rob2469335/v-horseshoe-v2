import logging
from typing import Any
from fastapi import Request, HTTPException
import asyncio

logger = logging.getLogger(__name__)

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
        if hasattr(event_store, "read_all"):
            return await asyncio.to_thread(event_store.read_all)
        elif hasattr(event_store, "list_all"):
            return await asyncio.to_thread(event_store.list_all)
        return []
    except Exception as e:
        logger.error(f"Error reading events: {e}")
        return []
