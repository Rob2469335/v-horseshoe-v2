# swarm_os/api/health.py
from __future__ import annotations
from fastapi import APIRouter
from ..core.settings import get_settings
from ops.health.system_health import run_system_health_checks

router = APIRouter(tags=["health"])

@router.api_route("/health", methods=["GET", "HEAD"])
async def health():
    s = get_settings()
    return {
        "status": "ok",
        "app": s.app_name,
        "version": "2.0.0",
        "llamacpp": s.llamacpp_base_url,
        "environment": s.environment,
    }

@router.get("/health/system")
async def health_system():
    return {
        "status": "ok",
        "checks": run_system_health_checks(),
    }
