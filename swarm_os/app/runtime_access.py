from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from swarm_os.app.main import RuntimeGraph


def get_runtime(request: Request) -> "RuntimeGraph":
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(
            status_code=503,
            detail="Swarm OS runtime is not initialized",
        )
    return runtime


def get_runtime_service(request: Request, attribute: str) -> Any:
    service = getattr(get_runtime(request), attribute, None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail=f"Runtime service unavailable: {attribute}",
        )
    return service
