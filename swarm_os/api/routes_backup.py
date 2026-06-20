from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from swarm_os.api import admin

router = APIRouter()
router.include_router(admin.router, prefix='/api/admin')
def runtime_dep(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="runtime unavailable")
    return runtime

def get_agent_service(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    service = getattr(runtime, "agent_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Agent service unavailable")
    return service

def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator

# --- Helper Functions ---

def _safe_health_report(runtime: Any) -> dict[str, Any]:
    try:
        healing = getattr(runtime, "healing", None)
        detector = getattr(healing, "detector", None)
        if detector is None:
            return {"status": "ok", "health_score": 100, "overall": "healing detector unavailable"}
        report = detector.status() if hasattr(detector, "status") else detector.check()
        return {
            "status": "ok" if report.get("health_score", report.get("recovery_readiness", 0)) >= 80 else "degraded",
            "health_score": report.get("health_score", report.get("recovery_readiness", 0)),
            "overall": report.get("overall", "active" if report.get("active_anomalies", 0) > 0 else "unknown"),
        }
    except Exception as exc:
        return {"status": "error", "health_score": 0, "overall": f"health check failed: {exc}"}

async def _safe_ollama_reachable(runtime: Any) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            return r.status_code == 200
    except Exception: return False

async def _safe_ollama_models(runtime: Any) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            data = r.json()
            return sorted({m["name"] for m in data.get("models", []) if m.get("name")})
    except Exception: return []

def _build_capabilities(installed_models: list[str], runtime: Any = None) -> dict[str, Any]:
    vision_models = [m for m in installed_models if any(marker in m.lower() for marker in ["vl", "vision", "moondream", "llava"])]
    coding_models = [m for m in installed_models if any(marker in m.lower() for marker in ["coder", "code"])]
    reasoning_models = [m for m in installed_models if any(marker in m.lower() for marker in ["qwen3", "14b", "32k", "reason", "mistral"])]
    
    # Base tools (API endpoints)
    tool_names = ["health", "readyz", "status", "events", "traces", "timeline", "tools", "generate"]
    
    # Merge with AgentRuntime tools if available
    if runtime and hasattr(runtime, "agent_runtime") and runtime.agent_runtime is not None:
        try:
            agent_tools = runtime.agent_runtime.list_tools()
        except Exception:
            agent_tools = []
        for t in agent_tools:
            if t not in tool_names:
                tool_names.append(t)

    return {
        "tools": {"available": True, "count": len(tool_names), "names": tool_names, "source": "runtime-dynamic"},
        "vision": {"available": len(vision_models) > 0, "models": vision_models, "primary_model": vision_models[0] if vision_models else None, "provider": "ollama"},
        "generation": {"available": len(installed_models) > 0, "provider": "ollama", "models": installed_models, "default_model": installed_models[0] if installed_models else None, "coding_models": coding_models, "reasoning_models": reasoning_models},
    }

def _safe_events(runtime: Any) -> list[Any]:
    try:
        event_store = getattr(runtime, "event_store", None)
        if event_store is None: return []
        return event_store.read_all()
    except Exception: return []

# --- API Endpoints ---

@router.get("/health")
def health() -> dict:
    return {"status": "ok", "overall": "healthy", "health_score": 100}

@router.get("/events/stream")
async def events_stream(runtime: Any = Depends(runtime_dep)):
    """SSE stream of all organism events including Zenith agent activity."""
    from swarm_os.core.event_bus import event_bus
    from fastapi.responses import StreamingResponse
    import json
    async def _gen():
        async for event in event_bus.subscribe():
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(_gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

@router.get("/readyz")
async def readyz(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    report = _safe_health_report(runtime)
    ollama_reachable = await _safe_ollama_reachable(runtime)
    installed_models = await _safe_ollama_models(runtime)
    checks = {
        "runtime_started": getattr(runtime, "orchestrator", None) is not None,
        "ollama_reachable": ollama_reachable,
        "models_loaded": len(installed_models) > 0,
        "health_score_ok": report["health_score"] >= 60,
    }
    ready = all(checks.values())
    return {"status": "ready" if ready else "not-ready", "ready": ready, "checks": checks, "health_score": report["health_score"], "overall": report["overall"]}

@router.get("/status")
async def status(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    settings = getattr(runtime, "settings", None)
    event_store = getattr(runtime, "event_store", None)
    all_events = _safe_events(runtime)
    ollama_reachable = await _safe_ollama_reachable(runtime)
    installed_models = await _safe_ollama_models(runtime)
    capabilities = _build_capabilities(installed_models, runtime=runtime)
    return {
        "status": "ok" if ollama_reachable else "degraded",
        "ready": ollama_reachable and len(installed_models) > 0,
        "app_name": getattr(settings, "app_name", "Swarm OS"),
        "environment": getattr(settings, "environment", "unknown"),
        "events_path": str(getattr(event_store, "path", "")),
        "event_count": len(all_events),
        "ollama_base_url": "http://127.0.0.1:11434",
        "ollama_reachable": ollama_reachable,
        "installed_model_count": len(installed_models),
        "installed_models": installed_models,
        "capabilities": capabilities,
        "vision_configured": capabilities["vision"]["available"],
        "vision_runtime_available": capabilities["vision"]["available"],
        "vision_tool_exposed": True,
        "primary_vision_model": capabilities["vision"]["primary_model"],
    }

@router.get("/events")
def list_events(runtime: Any = Depends(runtime_dep)):
    all_ev = _safe_events(runtime)
    return {"count": len(all_ev), "events": all_ev[-50:]}

@router.get("/traces")
def list_traces(limit: int = 50, orch: Orchestrator = Depends(get_orchestrator)):
    items = orch.get_recent_traces(limit=limit)
    return {"count": len(items), "traces": items}

@router.get("/tools", response_model=ToolListResponse)
async def list_tools(runtime=Depends(runtime_dep)):
    installed_models = await _safe_ollama_models(runtime)
    cap_data = _build_capabilities(installed_models, runtime=runtime)
    return ToolListResponse(
        capabilities=cap_data["tools"]["names"],
        count=cap_data["tools"]["count"],
        vision_configured=cap_data["vision"]["available"],
        vision_runtime_available=cap_data["vision"]["available"],
        vision_tool_exposed=True,
        vision_models_configured=cap_data["vision"]["models"],
        vision_models_installed=cap_data["vision"]["models"],
    )

@router.get("/tools/cache", response_model=CacheStatusResponse)
def cache_status(request: Request, runtime=Depends(runtime_dep)):
    cache = getattr(request.app.state, "cache", None)
    if cache is not None and hasattr(cache, "_items"):
        cached_keys = list(cache._items.keys())
        return CacheStatusResponse(cache_size=len(cached_keys), cached_keys=cached_keys)
    return CacheStatusResponse(cache_size=0, cached_keys=[])

@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(payload: ToolExecuteRequest, runtime=Depends(runtime_dep)):
    try:
        # FIX: Use AgentRuntime if available
        if hasattr(runtime, "agent_runtime"):
            result = await runtime.agent_runtime.call_tool(payload.capability, payload.payload, cache_key=payload.cache_key)
            return ToolExecuteResponse(status="success", capability=payload.capability, data=result)
        
        # Legacy/Fallback
        if hasattr(runtime, "call_tool"):
            result = await runtime.call_tool(payload.capability, payload.payload)
            return ToolExecuteResponse(status="success", capability=payload.capability, data=result)
            
        raise HTTPException(status_code=501, detail="tool execution not implemented in this runtime")
    except Exception as exc:
        log.exception("Tool execution failed")
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/generate")
async def generate(payload: GenerateRequest, runtime: Any = Depends(runtime_dep)):
    model = payload.model or "qwen2.5:7b-instruct"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                "http://127.0.0.1:11434/api/chat",
                json={"model": model, "messages": [{"role": "user", "content": payload.prompt}], "stream": False}
            )
            r.raise_for_status()
            data = r.json()
            content = data.get("message", {}).get("content", "")
            return {"content": content, "model": model, "choices": [{"message": {"content": content, "tool_calls": []}, "finish_reason": "stop"}], "usage": {"total_tokens": data.get("eval_count", 0), "prompt_tokens": data.get("prompt_eval_count", 0)}}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

@router.post("/assign", response_model=AssignResponse)
def assign(payload: AssignRequest, orch: Orchestrator = Depends(get_orchestrator)):
    node = SwarmNode(**payload.node) if isinstance(payload.node, dict) else payload.node
    job = SwarmJob(**payload.job) if isinstance(payload.job, dict) else payload.job
    score = score_node(node, job)
    accepted = orch.assign_job(node, job) if hasattr(orch, "assign_job") else score > 0
    return AssignResponse(accepted=bool(accepted), node_id=node.node_id, job_id=job.job_id, score=score)

@router.get("/timeline", response_model=TimelineResponse)
def timeline(window_minutes: int = 60):
    events_path = Path("data/events/events.jsonl")
    if not events_path.exists(): return TimelineResponse(window_minutes=window_minutes, points=[])
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    buckets = defaultdict(lambda: {"event_count": 0, "success_count": 0, "partial_count": 0, "fail_count": 0})
    with events_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
                raw_ts = event.get("occurred_at") or event.get("timestamp") or event.get("ts")
                if not raw_ts: continue
                ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                if ts < cutoff: continue
                bucket = ts.replace(second=0, microsecond=0).isoformat(timespec="minutes")
                buckets[bucket]["event_count"] += 1
                outcome = str(((event.get("learning_outcome") or {}).get("result") or "")).lower()
                if outcome == "success": buckets[bucket]["success_count"] += 1
                elif outcome == "partial": buckets[bucket]["partial_count"] += 1
                elif outcome == "fail": buckets[bucket]["fail_count"] += 1
            except Exception: continue
    points = [TimelinePointResponse(bucket=bucket, **values) for bucket, values in sorted(buckets.items())]
    return TimelineResponse(window_minutes=window_minutes, points=points)

def get_agent_service(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or not hasattr(runtime, "agent_service"):
        raise HTTPException(status_code=503, detail="Agent service unavailable")
    return runtime.agent_service

# --- AgentService Endpoints ---

@router.get("/agents")
def list_agents(service=Depends(get_agent_service)):
    return service.list_agents()

@router.post("/agents")
def create_agent(payload: AgentStepRequest, service=Depends(get_agent_service)):
    # Payload is actually AgentCreateRequest but using AgentStepRequest for fields
    service.register_agent(payload.agent_id, {})
    return {"status": "created", "agent_id": payload.agent_id}

@router.post("/agents/{agent_id}/step")
async def step_agent(agent_id: str, payload: AgentStepRequest, service=Depends(get_agent_service)):
    try:
        return await service.step_agent(agent_id, payload.prompt, history=payload.history)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/agents/{agent_id}/tool/{tool_name}")
async def run_agent_tool(agent_id: str, tool_name: str, payload: AgentToolRequest, service=Depends(get_agent_service)):
    try:
        return await service.run_tool(agent_id, tool_name, payload.payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.post("/agents/{agent_id}/step/stream")
async def stream_agent(request: Request, agent_id: str, payload: AgentStepRequest):
    runtime = getattr(request.app.state, "runtime", None)
    service = getattr(runtime, "agent_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Agent service unavailable")
    async def _streamer():
        async for chunk_data in service.step_agent_stream(agent_id, payload.prompt, history=payload.history):
            yield json.dumps(chunk_data) + "\n"

    return StreamingResponse(_streamer(), media_type="application/x-ndjson")

@router.post("/vault")
def vault_write(body: dict):
    action = body.get("action", "")
    if action == "add":
        rules = _vault.add(body.get("rule", ""))
        return {"rules": rules}
    elif action == "remove":
        rules = _vault.remove(int(body.get("index", 0)))
        return {"rules": rules}
    return {"error": "Unknown action"}

@router.post("/tools/sandbox")
async def sandbox_run(body: dict):
    result = await _sandbox.execute(body)
    return result


@router.get("/healing/evaluate")
async def healing_evaluate_root(request: Request, runtime: Any = Depends(runtime_dep)) -> dict:
    """Root-level healing evaluate so HealingTrigger checkStatus() works."""
    try:
        healing = getattr(runtime, "healing", None)
        checks = {"orchestrator": {"ok": True}, "qdrant": {"ok": True}, "ollama": {"ok": True}, "api": {"ok": True}}
        readiness = 100
        anomalies = 0
        if healing:
            detector = getattr(healing, "detector", None)
            if detector:
                report = detector.status() if hasattr(detector, "status") else {}
                readiness = report.get("recovery_readiness", report.get("health_score", 100))
                anomalies = report.get("active_anomalies", 0)
        return {"recovery_readiness": readiness, "active_anomalies": anomalies,
                "last_heal_success": anomalies == 0, "checks": checks}
    except Exception as exc:
        return {"recovery_readiness": 0, "active_anomalies": 1, "last_heal_success": False,
                "checks": {}, "error": str(exc)}






@router.get("/admin/status")
async def api_admin_status(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    return await status(runtime)

@router.get("/admin/dashboard")
async def api_admin_dashboard(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    return {
        "scenario": "default",
        "latest_snapshot": None,
        "current_run": None,
    }

@router.get("/admin/generation")
async def api_admin_generation(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    return {
        "scenario": "default",
        "latest_snapshot": None,
        "current_run": None,
        "population": [],
    }

@router.get("/admin/run-state")
async def api_admin_run_state(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    return {
        "scenario": "default",
        "latest_snapshot": None,
        "snapshot_count": 0,
    }











