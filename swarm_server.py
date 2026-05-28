from __future__ import annotations

from fastapi import APIRouter

from swarm_os.core.settings import get_settings
from swarm_os.services.status import get_status

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/readyz")
def readyz() -> dict[str, bool]:
    return {"ready": True}


@router.get("/status")
def status() -> dict[str, object]:
    settings = get_settings()
    current = get_status(settings.events_dir)
    return {
        "ready": current.ready,
        "events_path": str(current.events_path),
        "event_count": current.event_count,
        "ollama_base_url": settings.ollama_base_url,
    }
