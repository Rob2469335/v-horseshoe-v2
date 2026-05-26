# swarm_os/api/health.py
from __future__ import annotations

from fastapi import APIRouter
from ..core.settings import get_settings

router = APIRouter()


@router.get("/api/health")
async def health():
    s = get_settings()
    return {
        "status":      "ok",
        "app":         s.app_name,
        "version":     "2.0.0",
        "ollama":      s.ollama_base_url,
        "environment": s.environment,
    }
