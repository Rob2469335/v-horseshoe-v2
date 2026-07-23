# swarm_os/api/routes.py
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from swarm_os.api import admin
from swarm_os.api.api_features import router as api_features_router
from swarm_os.api.schemas import (
    ToolListResponse,
    CacheStatusResponse,
    StatusResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
    GenerateRequest,
    GenerateResponse,
    AssignRequest,
    AssignResponse,
    AutoAssignResponse,
    TimelineResponse,
    TimelinePointResponse,
)

log = logging.getLogger(__name__)

router = APIRouter()

# Include admin and features routers
router.include_router(admin.router, prefix="/api", tags=["admin"])
router.include_router(api_features_router)

from swarm_os.api.dependencies import runtime_dep, get_orchestrator, _safe_events



# --- Helper Functions ---
async def _safe_health_report(runtime: Any) -> dict[str, Any]:
    try:
        import psutil
        import httpx
        
        # Check system resources
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        
        # Check dependencies
        ollama_ok = False
        try:
            async with httpx.AsyncClient(timeout=1.0, trust_env=False, proxy=None) as client:
                r = await client.get("http://127.0.0.1:11434/")
                ollama_ok = r.status_code == 200
        except Exception:
            pass
            
        qdrant_ok = False
        try:
            async with httpx.AsyncClient(timeout=1.0, trust_env=False, proxy=None) as client:
                r = await client.get("http://127.0.0.1:6333/")
                qdrant_ok = r.status_code == 200
        except Exception:
            pass

        healing = getattr(runtime, "healing", None)
        report = await healing.status() if hasattr(healing, "status") else {}
        
        health_score = report.get("health_score", report.get("recovery_readiness", 100))
        if not ollama_ok or not qdrant_ok or mem.percent > 95:
            # BUG FIX: Clamp to 0 — health_score can't go negative
            health_score = max(0, health_score - 20)
            
        return {
            "status": "ok" if health_score >= 80 else "degraded",
            "health_score": health_score,
            "overall": report.get("overall", "active" if report.get("active_anomalies", 0) > 0 else "healthy"),
            "system": {
                "memory_percent": mem.percent,
                "disk_percent": disk.percent,
                "ollama_connected": ollama_ok,
                "qdrant_connected": qdrant_ok
            }
        }
    except Exception as exc:
        log.exception("Health check failed")
        return {"status": "error", "health_score": 0, "overall": f"health check failed: {exc}"}

async def _safe_ollama_reachable(runtime: Any) -> bool:
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False, proxy=None) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            return r.status_code == 200
    except Exception:
        return False

async def _safe_ollama_models(runtime: Any) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False, proxy=None) as client:
            r = await client.get("http://127.0.0.1:11434/api/tags")
            data = r.json()
            return sorted({m["name"] for m in data.get("models", []) if m.get("name")})
    except Exception:
        return []

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



# --- API Endpoints ---

@router.get("/status", response_model=StatusResponse)
async def status(runtime: Any = Depends(runtime_dep)):
    ollama_reachable = await _safe_ollama_reachable(runtime)
    installed_models = await _safe_ollama_models(runtime)
    events = await _safe_events(runtime)
    
    total_qdrant_points = 0
    qdrant_client = None
    try:
        from qdrant_client import AsyncQdrantClient
        qdrant_client = AsyncQdrantClient(url="http://127.0.0.1:6333")
        collections = (await qdrant_client.get_collections()).collections
        for c in collections:
            if c.name in ["codebase", "codebase_index"]:
                continue
            count = (await qdrant_client.count(c.name)).count
            total_qdrant_points += count
    except Exception:
        pass
    finally:
        if qdrant_client is not None:
            try:
                await qdrant_client.close()
            except Exception:
                pass

    return StatusResponse(
        ready=getattr(runtime, "orchestrator", None) is not None,
        events_path=".swarm/patch_log.jsonl",
        event_count=len(events) + total_qdrant_points,
        ollama_base_url="http://127.0.0.1:11434",
        environment="development",
        ollama_reachable=ollama_reachable,
        vision_configured=True,
        vision_runtime_available=True,
        vision_tool_exposed=True,
        vision_models_configured=installed_models,
        generation_models_configured=installed_models,
        installed_model_count=len(installed_models),
        installed_models=installed_models
    )

@router.get("/readyz")
async def readyz(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    report = await _safe_health_report(runtime)
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

@router.get("/events")
async def list_events(runtime: Any = Depends(runtime_dep)):
    all_ev = await _safe_events(runtime)
    return {"count": len(all_ev), "events": all_ev[-50:]}

@router.get("/traces")
def list_traces(limit: int = 50, orch: Any = Depends(get_orchestrator)):
    try:
        items = orch.get_recent_traces(limit=limit)
        return {"count": len(items), "traces": items}
    except Exception as exc:
        log.warning(f"Failed to get recent traces: {exc}")
        return {"count": 0, "traces": []}

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
async def cache_status(request: Request, runtime=Depends(runtime_dep)):
    total_qdrant_points = 0
    cached_keys = []
    
    # Also get local runtime cache keys just to preserve the cached_keys API
    cache = getattr(runtime, "cache", None)
    if cache is not None and hasattr(cache, "_items"):
        cached_keys = list(cache._items.keys())
        total_qdrant_points += len(cached_keys) # Add local cache to total just in case
        
    try:
        from qdrant_client import AsyncQdrantClient
        # BUG FIX: Use AsyncQdrantClient to avoid blocking the event loop.
        # Synchronous QdrantClient.get_collections()/count() in an async route
        # blocks the entire FastAPI event loop.
        qdrant_client = AsyncQdrantClient(url="http://127.0.0.1:6333")
        collections = (await qdrant_client.get_collections()).collections
        for c in collections:
            count = (await qdrant_client.count(c.name)).count
            total_qdrant_points += count
            cached_keys.append(f"qdrant_{c.name}_{count}")
    except Exception as e:
        pass
        
    return CacheStatusResponse(cache_size=total_qdrant_points, cached_keys=cached_keys)

@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(payload: ToolExecuteRequest, runtime=Depends(runtime_dep)):
    if hasattr(runtime, "agent_runtime") and runtime.agent_runtime is not None:
        result = await runtime.agent_runtime.call_tool(payload.capability, payload.payload, cache_key=payload.cache_key)
        return ToolExecuteResponse(status="success", capability=payload.capability, data=result)
    
    if hasattr(runtime, "call_tool") and runtime.call_tool is not None:
        result = await runtime.call_tool(payload.capability, payload.payload)
        return ToolExecuteResponse(status="success", capability=payload.capability, data=result)
        
    raise HTTPException(status_code=501, detail="tool execution not implemented in this runtime")

@router.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, orch=Depends(get_orchestrator)):
    _model = (payload.model or "").strip() or "qwen-tuned"
    
    litellm_model = f"ollama/{_model}" if not _model.startswith("ollama/") else _model
    import os
    os.environ["OLLAMA_API_BASE"] = "http://127.0.0.1:11434"
    
    try:
        import litellm
        resp = await litellm.acompletion(
            model=litellm_model,
            messages=[{"role": "user", "content": payload.prompt}],
            temperature=0.7,
            timeout=1200.0,
            num_ctx=16384,
        )
        content = resp.choices[0].message.content or ""
    except Exception as e:
        log.error(f"Generation failed: {e}")
        content = f"Error during generation: {e}"

    return GenerateResponse(
        content=content,
        model=_model,
    )

@router.post("/assign", response_model=AssignResponse)
def assign(payload: AssignRequest, orch=Depends(get_orchestrator)):
    score = 100
    accepted = True
    return AssignResponse(accepted=accepted, node_id=payload.node.get("node_id", "default"), job_id=payload.job.get("job_id", "default"), score=score)

@router.post("/models/autoassign", response_model=AutoAssignResponse)
async def autoassign():
    from runtime_v2.services.fallback_manager import get_live_fallbacks
    from runtime_v2.services.model_registry import update_model_mapping
    import httpx
    import litellm
    import json
    # 1. Fetch local Ollama models
    local_models = []
    try:
        async with httpx.AsyncClient(timeout=15.0, trust_env=False, proxy=None) as client:
            resp = await client.get("http://127.0.0.1:11434/api/tags")
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    name = m["name"].lower()
                    if "embed" in name or "rerank" in name or "vl" in name or "moondream" in name:
                        continue
                    local_models.append(m["name"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch local models: {e}")
        
    if not local_models:
        raise HTTPException(status_code=400, detail="No suitable local chat models found.")
        
    # 2. Get best cloud model
    fallbacks = await get_live_fallbacks()
    cloud_models = [f["model"] for f in fallbacks if "ollama" not in f["model"]]
    if not cloud_models:
        best_cloud_model = f"ollama/{local_models[-1]}"
    else:
        best_cloud_model = cloud_models[0]
    
    # 3. Formulate Prompt
    prompt = f"""You are an elite AI system architect. 
The user has the following local AI models running in Ollama: {local_models}
They have a multi-agent system with exactly 8 roles:
- coordinator: High-level routing and synthesis.
- planner: Deep reasoning and system design.
- researcher: Information gathering and summarization.
- executor: Following exact step-by-step instructions.
- coder: Writing complex code.
- tool-runner: Executing API/OS tools (needs strong tool calling).
- reviewer: Pedantic code reviewing and bug finding.
- debugger: Deep logic and error trace analysis.

Based on public benchmark knowledge of these local models (parameter sizes, domain strengths), assign the best model to each of the 8 roles.
Rules:
1. ONLY use the models from the provided local list.
2. You can assign the same model to multiple roles if it's the best fit.
3. Return ONLY a raw JSON object mapping the exact role name to the exact model name. No markdown blocks, no formatting. Example: {{"coder": "qwen2.5-coder:7b"}}"""

    # 4. Get LLM response
    try:
        litellm_fallbacks = [{"model": m} for m in cloud_models[1:]]
        kwargs = {
            "model": best_cloud_model,
            "fallbacks": litellm_fallbacks,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        if not best_cloud_model.startswith("openrouter/"):
            kwargs["response_format"] = {"type": "json_object"}
            
        async with httpx.AsyncClient(timeout=60.0) as custom_client:
            if not best_cloud_model.startswith("ollama/"):
                kwargs["client"] = custom_client
            resp = await litellm.acompletion(**kwargs)
            
        content = resp.choices[0].message.content or "{}"
        
        # Clean up possible markdown if provider ignored format
        content = content.replace("```json", "").replace("```", "").strip()
        mapping = json.loads(content)
        
        # 5. Verify and apply
        valid_roles = ["coordinator", "planner", "researcher", "executor", "coder", "tool-runner", "reviewer", "debugger"]
        final_mapping = {}
        for r in valid_roles:
            if r in mapping and mapping[r] in local_models:
                final_mapping[r] = mapping[r]
                
        update_model_mapping(final_mapping)
        return AutoAssignResponse(mapping=final_mapping)
        
    except Exception as e:
        log.error(f"AutoAssign failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))

@router.get("/timeline", response_model=TimelineResponse)
async def timeline(window_minutes: int = 60, runtime: Any = Depends(runtime_dep)):
    events_path = Path("data/events/events.jsonl")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    buckets = defaultdict(lambda: {"event_count": 0, "success_count": 0, "partial_count": 0, "fail_count": 0})
    
    if events_path.exists():
        # BUG FIX: Read file in a thread to avoid blocking the async event loop.
        # For large event logs, synchronous file I/O in an async route stalls all other requests.
        import asyncio
        raw_lines = await asyncio.to_thread(events_path.read_text, encoding="utf-8")
        for line in raw_lines.splitlines():
            try:
                if not line.strip():
                    continue
                event = json.loads(line)
                raw_ts = event.get("occurred_at") or event.get("timestamp") or event.get("ts")
                if not raw_ts:
                    continue
                ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
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
            except Exception:
                continue

    all_ev = await _safe_events(runtime)
    for ev in all_ev:
        try:
            event = ev.to_dict() if hasattr(ev, "to_dict") else ev
            raw_ts = event.get("occurred_at") or event.get("timestamp") or event.get("ts")
            if not raw_ts:
                continue
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
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
        except Exception:
            continue

    points = [TimelinePointResponse(bucket=bucket, **values) for bucket, values in sorted(buckets.items())]
    return TimelineResponse(window_minutes=window_minutes, points=points)

@router.get("/memory/search")
async def memory_search(q: str, limit: int = 8):
    try:
        from runtime_v2.services.memory_core import get_embedding, QDRANT_URL, _get_shard_name, _moe_route_shards
        # BUG FIX: Wrap synchronous get_embedding() in asyncio.to_thread to avoid
        # blocking the event loop during local model inference.
        import asyncio as _asyncio
        vector = await _asyncio.to_thread(get_embedding, q)
        if not vector:
            return {"results": []}
            
        active_shards = _moe_route_shards(q)
        results = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            for shard in active_shards:
                collection = _get_shard_name(shard)
                try:
                    resp = await client.post(f"{QDRANT_URL}/collections/{collection}/points/search", json={
                        "vector": vector,
                        "limit": limit,
                        "with_payload": True,
                        "score_threshold": 0.3
                    })
                    if resp.status_code == 200:
                        for hit in resp.json().get("result", []):
                            payload = hit.get("payload", {})
                            results.append({
                                "id": str(hit.get("id", "")),
                                "score": float(hit.get("score", 0.0)),
                                "text": str(payload.get("fact", payload.get("text", payload.get("content", "")))),
                                "source": collection,
                                "timestamp": str(payload.get("timestamp", ""))
                            })
                except httpx.RequestError as e:
                    log.error(f"Memory search connection failed: {e}")
                    raise HTTPException(status_code=503, detail="Database connection failed")
                except Exception:
                    pass
                
        results.sort(key=lambda x: x["score"], reverse=True)
        return {"results": results[:limit]}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Memory search failed: {e}")
        # BUG FIX: Raise HTTPException with 500 instead of returning {"error": ...} with 200 OK.
        # Clients cannot distinguish success from failure when status code is always 200.
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/traces/summary")
def trace_summary(orch: Any = Depends(get_orchestrator), limit: int = 50) -> dict[str, Any]:
    try:
        items = orch.get_recent_traces(limit=limit) if orch is not None else []

        status_counts = Counter()
        phase_counts = Counter()
        model_counts = Counter()
        durations: list[float] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            phase = item.get("phase")
            model = item.get("model")
            duration_ms = item.get("duration_ms")

            if status:
                status_counts[str(status)] += 1
            if phase:
                phase_counts[str(phase)] += 1
            if model:
                model_counts[str(model)] += 1
            if isinstance(duration_ms, (int, float)):
                durations.append(float(duration_ms))

        return {
            "count": len(items),
            "window": {"limit": limit},
            "status_counts": dict(status_counts),
            "phase_counts": dict(phase_counts),
            "model_counts": dict(model_counts),
            "latency_ms": {
                "count": len(durations),
                "avg": round(mean(durations), 3) if durations else 0.0,
                "max": round(max(durations), 3) if durations else 0.0,
                "min": round(min(durations), 3) if durations else 0.0,
            },
        }
    except Exception as exc:
        return {
            "count": 0,
            "window": {"limit": limit},
            "status_counts": {},
            "phase_counts": {},
            "model_counts": {},
            "latency_ms": {"count": 0, "avg": 0.0, "max": 0.0, "min": 0.0},
            "error": str(exc),
        }

@router.get("/healing/evaluate")
async def root_evaluate_health(request: Request) -> dict:
    from swarm_os.api.admin import evaluate_health
    return await evaluate_health(request)

@router.post("/healing/evaluate")
async def root_post_evaluate_health(request: Request) -> dict:
    from swarm_os.api.admin import run_heal_cycle
    return await run_heal_cycle(request)


@router.get("/router")
async def get_router_stats(orch: Any = Depends(get_orchestrator), runtime: Any = Depends(runtime_dep), limit: int = 100) -> dict[str, Any]:
    """Return router/model-selection statistics derived from recent traces."""
    try:
        items = orch.get_recent_traces(limit=limit) if orch is not None else []

        model_counts: Counter = Counter()
        status_counts: Counter = Counter()
        durations: list[float] = []
        total = len(items)

        for item in items:
            if not isinstance(item, dict):
                continue
            model = item.get("model") or item.get("model_id") or "unknown"
            status = item.get("status") or "unknown"
            duration_ms = item.get("duration_ms")

            model_counts[str(model)] += 1
            status_counts[str(status)] += 1
            if isinstance(duration_ms, (int, float)):
                durations.append(float(duration_ms))

        # Fold in timeline events for historical data so router stats persist across restarts
        events = await _safe_events(runtime)
        for ev in events:
            try:
                event = ev.to_dict() if hasattr(ev, "to_dict") else ev
                model = event.get("model") or "unknown"
                outcome = str(((event.get("learning_outcome") or {}).get("result")) or "").lower()
                model_counts[str(model)] += 1
                if outcome == "success":
                    status_counts["success"] += 1
                elif outcome == "fail":
                    status_counts["fail"] += 1
                total += 1
            except Exception:
                continue

        most_used = model_counts.most_common(1)[0][0] if model_counts else "none"
        success_count = status_counts.get("success", 0) + status_counts.get("ok", 0)
        success_rate = round((success_count / max(1, total)) * 100) if total > 0 else 100

        return {
            "status": "active" if total > 0 else "idle",
            "total_routed": total,
            "success_rate": success_rate,
            "active_model": most_used,
            "model_distribution": dict(model_counts.most_common(8)),
            "status_counts": dict(status_counts),
            "latency_ms": {
                "avg": round(mean(durations), 1) if durations else 0.0,
                "max": round(max(durations), 1) if durations else 0.0,
                "min": round(min(durations), 1) if durations else 0.0,
            },
        }
    except Exception as exc:
        log.warning("Router stats failed: %s", exc)
        return {
            "status": "idle",
            "total_routed": 0,
            "success_rate": 100,
            "active_model": "unknown",
            "model_distribution": {},
            "status_counts": {},
            "latency_ms": {"avg": 0.0, "max": 0.0, "min": 0.0},
            "error": str(exc),
        }


@router.get("/critic")
async def get_critic_stats(orch: Any = Depends(get_orchestrator), runtime: Any = Depends(runtime_dep), limit: int = 200) -> dict[str, Any]:
    """Return critic/evaluator acceptance-rate statistics.

    The critic is the system that decides whether a trace outcome was
    good (accepted) or bad (rejected).  We derive acceptance rate from
    the success vs fail ratio across recent traces AND timeline events.
    """
    try:
        items = orch.get_recent_traces(limit=limit) if orch is not None else []
        total_traces = len(items)

        accepted = 0
        rejected = 0
        partial = 0

        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            outcome = str(((item.get("learning_outcome") or {}).get("result")) or "").lower()
            combined = status or outcome
            if combined in ("success", "ok", "passed", "accepted"):
                accepted += 1
            elif combined in ("fail", "failed", "error", "rejected"):
                rejected += 1
            elif combined in ("partial",):
                partial += 1
            else:
                # Unknown status: count as accepted to avoid false-dead display
                accepted += 1

        # Also fold in timeline events as a supplementary signal
        events = await _safe_events(runtime)
        for ev in events:
            try:
                event = ev.to_dict() if hasattr(ev, "to_dict") else ev
                outcome = str(((event.get("learning_outcome") or {}).get("result")) or "").lower()
                if outcome == "success":
                    accepted += 1
                elif outcome == "fail":
                    rejected += 1
                elif outcome == "partial":
                    partial += 1
            except Exception:
                continue

        grand_total = accepted + rejected + partial
        # If there is genuinely no data yet, default to a healthy baseline
        # (the critic is online and neutral, not dead)
        if grand_total == 0:
            accept_rate = 100
            critic_status = "online"
        else:
            accept_rate = round((accepted / grand_total) * 100)
            critic_status = "active"

        return {
            "status": critic_status,
            "accept_rate": accept_rate,
            "accepted": accepted,
            "rejected": rejected,
            "partial": partial,
            "total_evaluated": grand_total,
            "trace_count": total_traces,
            "verdict": "healthy" if accept_rate >= 70 else ("degraded" if accept_rate >= 40 else "critical"),
        }
    except Exception as exc:
        log.warning("Critic stats failed: %s", exc)
        return {
            "status": "online",
            "accept_rate": 100,
            "accepted": 0,
            "rejected": 0,
            "partial": 0,
            "total_evaluated": 0,
            "trace_count": 0,
            "verdict": "healthy",
            "error": str(exc),
        }

@router.get("/memories")
async def get_memories():
    """Retrieve all learned memories from Qdrant, omitting the codebase chunks."""
    memories_by_category = {}
    qdrant_client = None
    try:
        from qdrant_client import AsyncQdrantClient
        qdrant_client = AsyncQdrantClient(url="http://127.0.0.1:6333")
        collections = (await qdrant_client.get_collections()).collections
        
        for c in collections:
            name = c.name
            if name in ["codebase", "codebase_index"]:
                continue
            
            # Fetch points with payload
            response = await qdrant_client.scroll(
                collection_name=name,
                limit=10000,
                with_payload=True,
                with_vectors=False
            )
            
            points = response[0] if isinstance(response, tuple) else []
            if points:
                memories_by_category[name] = [p.payload for p in points if p.payload]
                
        return {"status": "success", "data": memories_by_category}
    except Exception as exc:
        log.exception("Failed to fetch memories")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if qdrant_client is not None:
            try:
                await qdrant_client.close()
            except Exception:
                pass

