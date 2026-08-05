from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import requests

from infrastructure.config.settings import get_settings
from infrastructure.runtime.feature_flags import get_feature_flags


@dataclass(frozen=True)
class HealthCheckResult:
    component: str
    status: str
    detail: str
    checked_at: float


def check_qdrant() -> HealthCheckResult:
    settings = get_settings()
    flags = get_feature_flags()

    if not flags.qdrant_enabled:
        return HealthCheckResult(
            component="qdrant",
            status="disabled",
            detail="Feature flag disabled",
            checked_at=time.time(),
        )

    try:
        response = requests.get(f"{settings.qdrant_url}/collections", timeout=settings.health_timeout_seconds)
        response.raise_for_status()
        return HealthCheckResult(
            component="qdrant",
            status="ok",
            detail="Qdrant API reachable",
            checked_at=time.time(),
        )
    except Exception as exc:
        return HealthCheckResult(
            component="qdrant",
            status="error",
            detail=str(exc),
            checked_at=time.time(),
        )


def check_cache() -> HealthCheckResult:
    flags = get_feature_flags()
    return HealthCheckResult(
        component="cache",
        status="ok" if flags.cache_enabled else "disabled",
        detail="Cache enabled" if flags.cache_enabled else "Cache disabled by feature flag",
        checked_at=time.time(),
    )


def check_background_jobs() -> HealthCheckResult:
    settings = get_settings()
    flags = get_feature_flags()
    enabled = settings.jobs_enabled and flags.background_jobs
    return HealthCheckResult(
        component="background_jobs",
        status="ok" if enabled else "disabled",
        detail="Background jobs enabled" if enabled else "Background jobs disabled",
        checked_at=time.time(),
    )

def check_llm_local() -> HealthCheckResult:
    try:
        response = requests.get("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer llama"}, timeout=2.0)
        response.raise_for_status()
        return HealthCheckResult(
            component="llm_local",
            status="ok",
            detail="Local Llama.cpp reachable",
            checked_at=time.time(),
        )
    except Exception as exc:
        return HealthCheckResult(
            component="llm_local",
            status="error",
            detail=str(exc),
            checked_at=time.time(),
        )


def check_openrouter() -> HealthCheckResult:
    try:
        response = requests.get("https://openrouter.ai/api/v1/models", timeout=2.0)
        response.raise_for_status()
        return HealthCheckResult(
            component="openrouter",
            status="ok",
            detail="OpenRouter API reachable",
            checked_at=time.time(),
        )
    except Exception as exc:
        return HealthCheckResult(
            component="openrouter",
            status="error",
            detail=str(exc),
            checked_at=time.time(),
        )


def run_system_health_checks() -> list[dict]:
    results = [
        check_qdrant(),
        check_cache(),
        check_background_jobs(),
        check_llm_local(),
        check_openrouter(),
    ]
    return [asdict(result) for result in results]
