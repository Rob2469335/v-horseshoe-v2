from __future__ import annotations

import logging

from collections import Counter
from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from swarm_os.events.replay import ReplayEngine


class GenerateRequest(BaseModel):
    prompt: str
    model: str | None = None


STRICT_SYSTEM_PROMPT = (
    "You are Swarm OS execution engine. "
    "Do not be conversational. "
    "Return direct results only. "
    "No greetings. No commentary unless requested."
)


class TaskRequest(BaseModel):
    task: str
    context: dict[str, Any] | None = None


router = APIRouter()


def runtime_dep(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        return {"status": "ok", "ready": True}
    return runtime


def _safe_health_report(runtime: Any) -> dict[str, Any]:
    try:
        healing = getattr(runtime, "healing", None)
        detector = getattr(healing, "detector", None)
        if detector is None:
            return {
                "status": "ok",
                "health_score": 100,
                "overall": "healing detector unavailable",
            }
        report = detector.check()
        return {
            "status": "ok" if report.get("health_score", 0) >= 80 else "degraded",
            "health_score": report.get("health_score", 0),
            "overall": report.get("overall", "unknown"),
        }
    except Exception as exc:
        return {
            "status": "error",
            "health_score": 0,
            "overall": f"health check failed: {exc}",
        }


async def _safe_ollama_reachable(runtime: Any) -> bool:
    try:
        orchestrator = getattr(runtime, "orchestrator", None)
        ollama = getattr(orchestrator, "ollama", None)
        if ollama is None:
            return False
        return bool(await ollama.is_reachable())
    except Exception as exc:
        logging.error(f"Error checking Ollama reachability: {exc}")
        return False


async def _safe_ollama_models(runtime: Any) -> list[str]:
    try:
        orchestrator = getattr(runtime, "orchestrator", None)
        ollama = getattr(orchestrator, "ollama", None)
        if ollama is None:
            return []
        models = await ollama.list_models()
        return sorted({str(m).strip() for m in models if str(m).strip()})
    except Exception as exc:
        logging.error(f"Error listing Ollama models: {exc}")
        return []


def _build_capabilities(installed_models: list[str]) -> dict[str, Any]:
    vision_models = [
        m for m in installed_models
        if any(marker in m.lower() for marker in ["vl", "vision", "moondream", "llava"])
    ]
    coding_models = [
        m for m in installed_models
        if any(marker in m.lower() for marker in ["coder", "code"])
    ]
    reasoning_models = [
        m for m in installed_models
        if any(marker in m.lower() for marker in ["qwen3", "14b", "32k", "reason", "mistral"])
    ]

    tool_names = [
        "health",
        "readyz",
        "status",
        "events",
        "admin/events",
        "admin/causal-graph",
        "healing/evaluate",
        "admin/healing/run",
        "admin/replay",
        "traces",
        "traces/summary",
        "traces/clear",
        "admin/status",
        "admin/dashboard",
        "admin/generation",
    ]

    return {
        "tools": {
            "available": True,
            "count": len(tool_names),
            "names": tool_names,
            "source": "runtime-routes",
        },
        "vision": {
            "available": len(vision_models) > 0,
            "models": vision_models,
            "primary_model": vision_models[0] if vision_models else None,
            "provider": "ollama" if vision_models else None,
        },
        "generation": {
            "available": len(installed_models) > 0,
            "provider": "ollama" if installed_models else None,
            "models": installed_models,
            "default_model": installed_models[0] if installed_models else None,
            "coding_models": coding_models,
            "reasoning_models": reasoning_models,
        },
    }


import logging

...

def _safe_events(runtime: Any) -> list[Any]:
    try:
        event_store = getattr(runtime, "event_store", None)
        if event_store is None:
            return []
        return event_store.read_all()
    except Exception as exc:
        logging.error(f"Error reading events: {exc}")
        return []


@router.get("/health")
def health():
    return {"status": "ok", "ready": True}@router.get("/status")
async def status(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    settings = getattr(runtime, "settings", None)
    event_store = getattr(runtime, "event_store", None)
    all_events = _safe_events(runtime)
    ollama_reachable = await _safe_ollama_reachable(runtime)
    installed_models = await _safe_ollama_models(runtime)
    capabilities = _build_capabilities(installed_models)

    return {
        "status": "ok" if ollama_reachable else "degraded",
        "ready": ollama_reachable and len(installed_models) > 0,
        "app_name": getattr(settings, "app_name", "Swarm OS"),
        "environment": getattr(settings, "environment", "unknown"),
        "events_path": str(getattr(event_store, "path", "")),
        "event_count": len(all_events),
        "ollama_base_url": getattr(settings, "ollama_base_url", ""),
        "ollama_reachable": ollama_reachable,
        "installed_model_count": len(installed_models),
        "installed_models": installed_models,
        "capabilities": capabilities,
    }



