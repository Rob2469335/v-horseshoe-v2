from __future__ import annotations

import logging
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from swarm_os.api.admin import router as admin_router
from swarm_os.api.dashboard import router as dashboard_router
from swarm_os.api.explorer import router as explorer_router
from swarm_os.api.health import router as health_router
from swarm_os.api.schemas import (
    AssignRequest,
    AssignResponse,
    CacheStatusResponse,
    GenerateRequest,
    GenerateResponse,
    StatusResponse,
    TimelinePointResponse,
    TimelineResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolListResponse,
)
from swarm_os.core.settings import get_settings
from swarm_os.domain.models import SwarmJob, SwarmNode
from swarm_os.domain.policies import score_node
from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.status import get_status

log = logging.getLogger(__name__)

router = APIRouter()
router.include_router(health_router)
router.include_router(admin_router, prefix="/api")
router.include_router(dashboard_router, prefix="/api")
router.include_router(explorer_router, prefix="/api")


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_runtime(request: Request):
    return request.app.state.runtime


def runtime_dep(request: Request):
    return get_runtime(request)

def _build_tool_request(capability: str, payload: dict):
    from swarm_os.capabilities.models import (
        ChatSearchRequest,
        UpworkAnalysisRequest,
        VSCodeAutomationRequest,
    )

    cap = capability.lower().strip()
    if cap == "chat_search":
        return cap, ChatSearchRequest(**payload)
    if cap == "upwork_analyzer":
        return cap, UpworkAnalysisRequest(**payload)
    if cap == "vscode_automation":
        return cap, VSCodeAutomationRequest(**payload)

    raise HTTPException(status_code=400, detail=f"Unknown capability: {cap}")


def _get_configured_vision_models() -> list[str]:
    models = ["moondream:latest", "qwen3-vl:8b"]
    seen = set()
    ordered = []
    for item in models:
        key = item.lower().strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


def _is_vision_model(name: str) -> bool:
    lowered = name.lower()
    markers = ["vision", "vl", "moondream", "llava", "bakllava", "minicpm-v"]
    return any(marker in lowered for marker in markers)


def _get_installed_vision_models(ollama_base_url: str) -> list[str]:
    try:
        with httpx.Client(timeout=2.5) as client:
            response = client.get(f"{ollama_base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []

    models = payload.get("models", [])
    names: list[str] = []

    if isinstance(models, list):
        for model in models:
            name = str(model.get("name") or "").strip()
            if name and _is_vision_model(name):
                names.append(name)

    seen = set()
    ordered = []
    for item in names:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(item)
    return ordered


def _get_vision_meta(ollama_base_url: str) -> dict:
    configured = _get_configured_vision_models()
    installed = _get_installed_vision_models(ollama_base_url)
    return {
        "vision_configured": len(configured) > 0,
        "vision_runtime_available": len(installed) > 0,
        "vision_tool_exposed": True,
        "vision_models_configured": configured,
        "vision_models_installed": installed,
        "primary_vision_model": installed[0] if installed else (configured[0] if configured else None),
    }


@router.get("/readyz")
def readyz():
    return {"ready": True}


@router.get("/status", response_model=StatusResponse)
def status(orch: Orchestrator = Depends(get_orchestrator)):
    s = get_settings()
    st = get_status(s.events_dir)
    vision = _get_vision_meta(s.ollama_base_url)

    return StatusResponse(
        ready=st.ready,
        events_path=str(st.events_path),
        event_count=st.event_count,
        ollama_base_url=s.ollama_base_url,
        environment=s.environment,
        ollama_reachable=orch.ollama.is_reachable(),
        vision_configured=vision["vision_configured"],
        vision_runtime_available=vision["vision_runtime_available"],
        vision_tool_exposed=vision["vision_tool_exposed"],
        vision_models_configured=vision["vision_models_configured"],
        vision_models_installed=vision["vision_models_installed"],
        primary_vision_model=vision["primary_vision_model"],
    )


@router.get("/events")
def events(orch: Orchestrator = Depends(get_orchestrator)):
    all_ev = orch.events.read_all()
    return {"count": len(all_ev), "events": all_ev[-50:]}


@router.get("/traces")
def traces(limit: int = 50, orch: Orchestrator = Depends(get_orchestrator)):
    items = orch.get_recent_traces(limit=limit)
    return {"count": len(items), "traces": items}


@router.get("/tools", response_model=ToolListResponse)
def list_tools(runtime=Depends(runtime_dep)):
    s = get_settings()
    tools = runtime.list_tools()
    vision = _get_vision_meta(s.ollama_base_url)

    capabilities = list(tools)
    if "vision" not in capabilities:
        capabilities.append("vision")

    for model_name in vision["vision_models_installed"]:
        alias = model_name.split(":")[0]
        if alias not in capabilities:
            capabilities.append(alias)

    return ToolListResponse(
        capabilities=capabilities,
        count=len(capabilities),
        vision_configured=vision["vision_configured"],
        vision_runtime_available=vision["vision_runtime_available"],
        vision_tool_exposed=vision["vision_tool_exposed"],
        vision_models_configured=vision["vision_models_configured"],
        vision_models_installed=vision["vision_models_installed"],
    )


@router.get("/tools/cache", response_model=CacheStatusResponse)
def cache_status(request: Request, runtime=Depends(runtime_dep)):
    cache = getattr(request.app.state, "cache", None)

    if cache is not None and hasattr(cache, "_items"):
        cached_keys = list(cache._items.keys())
        return CacheStatusResponse(
            cache_size=len(cached_keys),
            cached_keys=cached_keys,
        )

    return CacheStatusResponse(
        cache_size=runtime.get_tool_cache_size(),
        cached_keys=[],
    )


@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(payload: ToolExecuteRequest, runtime=Depends(runtime_dep)):
    try:
        cap, req = _build_tool_request(payload.capability, payload.payload)
        result = await runtime.call_tool(cap, req, cache_key=payload.cache_key)
        return ToolExecuteResponse(
            status=result.status,
            capability=cap,
            data=result.model_dump() if hasattr(result, "model_dump") else str(result),
            message=getattr(result, "message", None),
            command=getattr(result, "command", None),
            exit_code=getattr(result, "exit_code", None),
            stdout=getattr(result, "stdout", None),
            stderr=getattr(result, "stderr", None),
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Tool execution failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, orch: Orchestrator = Depends(get_orchestrator)):
    try:
        result, chosen_model = await orch.generate(
            model=payload.model,
            prompt=payload.prompt,
        )
        return GenerateResponse(response=result, model=chosen_model)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/assign", response_model=AssignResponse)
def assign(payload: AssignRequest, orch: Orchestrator = Depends(get_orchestrator)):
    node = SwarmNode(**(payload.node.model_dump() if hasattr(payload.node, "model_dump") else payload.node))
    job = SwarmJob(**(payload.job.model_dump() if hasattr(payload.job, "model_dump") else payload.job))
    score = score_node(node, job)

    if hasattr(orch, "assign_job"):
        accepted = bool(orch.assign_job(node, job))
    else:
        accepted = score > 0

    return AssignResponse(
        accepted=accepted,
        node_id=node.node_id,
        job_id=job.job_id,
        score=score,
    )


@router.get("/swarm-stats")
def swarm_stats(orch: Orchestrator = Depends(get_orchestrator)):
    return dict(
        getattr(
            orch,
            "last_swarm_stats",
            {
                "status": "idling",
                "population_size": 0,
                "best_fitness": 0.0,
                "best_agent_id": "none",
                "active_generation": 0,
            },
        )
    )

@router.get("/timeline", response_model=TimelineResponse)
def timeline(window_minutes: int = 60):
    events_path = Path("data/events/events.jsonl")
    if not events_path.exists():
        return TimelineResponse(window_minutes=window_minutes, points=[])

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    buckets = defaultdict(lambda: {
        "event_count": 0,
        "success_count": 0,
        "partial_count": 0,
        "fail_count": 0,
    })

    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue

            raw_ts = event.get("occurred_at") or event.get("timestamp") or event.get("ts") or event.get("created_at")
            if not raw_ts:
                continue

            try:
                ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            except Exception:
                continue

            if ts < cutoff:
                continue

            bucket = ts.replace(second=0, microsecond=0).isoformat(timespec="minutes")
            buckets[bucket]["event_count"] += 1

            outcome = str(((event.get("learning_outcome") or {}).get("result") or "")).lower()
            if outcome == "success":
                buckets[bucket]["success_count"] += 1
            elif outcome == "partial":
                buckets[bucket]["partial_count"] += 1
            elif outcome == "fail":
                buckets[bucket]["fail_count"] += 1

    points = [
        TimelinePointResponse(bucket=bucket, **values)
        for bucket, values in sorted(buckets.items(), key=lambda item: item[0])
    ]
    return TimelineResponse(window_minutes=window_minutes, points=points)






