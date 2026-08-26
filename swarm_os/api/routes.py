# swarm_os/api/routes.py
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

import httpx
from fastapi import APIRouter, Query, Depends, HTTPException, Request

from swarm_os.api import admin
from swarm_os.api.api_features import router as api_features_router
from swarm_os.api.legal import router as legal_router
from swarm_os.api.books import router as books_router
from swarm_os.api.chess_trainer import router as chess_trainer_router
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
router.include_router(legal_router)
router.include_router(books_router)
router.include_router(chess_trainer_router)

from swarm_os.api.dependencies import runtime_dep, get_orchestrator, _safe_events
from swarm_os.services.system_service import SystemService
from swarm_os.services.chat_service import ChatService


# --- Helper Functions ---
# Shared pooled client for the per-port model probes: a fresh AsyncClient per
# port per /status poll wasted a TCP/TLS handshake every call and, with the
# old 1.0s timeout, produced transient "Failed checking port" warnings when
# the event loop was busy at startup (the ports were actually up).
_PROBE_CLIENT: httpx.AsyncClient | None = None


def _get_probe_client() -> httpx.AsyncClient:
    global _PROBE_CLIENT
    if _PROBE_CLIENT is None or _PROBE_CLIENT.is_closed:
        _PROBE_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(3.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
    return _PROBE_CLIENT


async def _safe_ollama_models(runtime: Any) -> list[str]:
    models = set()
    import asyncio

    async def fetch_port(port: int):
        try:
            client = _get_probe_client()
            resp = await client.get(
                f"http://127.0.0.1:{port}/v1/models",
                headers={"Authorization": "Bearer llama"},
            )
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    mid = m.get("id")
                    if not mid:
                        continue
                    # Normalize Windows file-path model ids like ".\models\foo.gguf" to "foo"
                    if ".gguf" in mid or "\\" in mid or "/" in mid:
                        mid = (
                            mid.replace("\\", "/")
                            .split("/")[-1]
                            .replace(".gguf", "")
                        )
                    models.add(mid)
        except Exception as exc:
            log.warning("Failed checking port %s: %s", port, exc)

    # Check all llama.cpp ports — ONLY report models actually being served
    await asyncio.gather(
        fetch_port(8080),  # Generation
        fetch_port(8081),  # Embeddings
        fetch_port(8082),  # Reranker
        fetch_port(8083),  # Vision
        return_exceptions=True,
    )

    # Sort deterministically; prefer the generation model first
    ordered = sorted(models)
    if any("qwen3.5" in m.lower() for m in ordered):
        ordered = [m for m in ordered if "qwen3.5" in m.lower()] + [
            m for m in ordered if "qwen3.5" not in m.lower()
        ]
    return ordered


def _build_capabilities(
    installed_models: list[str], runtime: Any = None
) -> dict[str, Any]:
    vision_models = [
        m
        for m in installed_models
        if any(marker in m.lower() for marker in ["vl", "vision", "moondream", "llava"])
    ]
    coding_models = [
        m
        for m in installed_models
        if any(marker in m.lower() for marker in ["coder", "code"])
    ]
    reasoning_models = [
        m
        for m in installed_models
        if any(
            marker in m.lower()
            for marker in ["qwen3", "14b", "32k", "reason", "mistral"]
        )
    ]

    # Base tools (API endpoints)
    tool_names = [
        "health",
        "readyz",
        "status",
        "events",
        "traces",
        "timeline",
        "tools",
        "generate",
    ]

    # Merge with AgentRuntime tools if available
    if (
        runtime
        and hasattr(runtime, "agent_runtime")
        and runtime.agent_runtime is not None
    ):
        try:
            agent_tools = runtime.agent_runtime.list_tools()
        except Exception:
            agent_tools = []
        for t in agent_tools:
            if t not in tool_names:
                tool_names.append(t)

    return {
        "tools": {
            "available": True,
            "count": len(tool_names),
            "names": tool_names,
            "source": "runtime-dynamic",
        },
        "vision": {
            "available": len(vision_models) > 0,
            "models": vision_models,
            "primary_model": vision_models[0] if vision_models else None,
            "provider": "llamacpp",
        },
        "generation": {
            "available": len(installed_models) > 0,
            "provider": "llamacpp",
            "models": installed_models,
            "default_model": installed_models[0] if installed_models else None,
            "coding_models": coding_models,
            "reasoning_models": reasoning_models,
        },
    }


@router.get("/status", response_model=StatusResponse)
async def status(runtime: Any = Depends(runtime_dep)):
    ollama_reachable = await SystemService.check_ollama_reachable()
    installed_models = await _safe_ollama_models(runtime)
    events = await _safe_events(runtime)

    total_qdrant_points = 0
    try:
        from swarm_os.services.vector_store import VectorStore

        vs = VectorStore()
        collections = (await vs.client.get_collections()).collections
        for c in collections:
            if c.name in ["codebase", "codebase_index"]:
                continue
            count = (await vs.client.count(c.name)).count
            total_qdrant_points += count
    except Exception as exc:
        log.warning("Failed counting Qdrant points: %s", exc)

    # BUG FIX: primary_vision_model must be the ACTUAL vision model (moondream),
    # not the first installed/generation model (qwen3.5-4b).
    vision_models = [
        m
        for m in installed_models
        if any(marker in m.lower() for marker in ["vl", "vision", "moondream", "llava"])
    ]

    # LIVE cloud fallback chain: count ready models per provider bucket so the
    # console banner's FALLBACKS row reflects reality, not "Checking status...".
    fallback_pool = {}
    try:
        from runtime_v2.services.fallback_manager import get_live_fallbacks

        chain = await get_live_fallbacks(mode="auto")
        buckets: dict[str, int] = {}
        for f in chain:
            mid = str(f.get("model", "") or "")
            # OpenCode (zen / openai-prefixed) FIRST — its model ids contain
            # "deepseek" too and would otherwise be miscounted as deepseek-direct.
            if "zen/" in mid or mid.startswith("openai"):
                b = "opencode"
            elif mid.startswith("openrouter"):
                b = "openrouter"
            elif mid.startswith("nvidia"):
                b = "nvidia"
            elif mid.startswith("groq"):
                b = "groq"
            elif mid.startswith("gemini"):
                b = "gemini"
            elif "deepseek" in mid:
                b = "deepseek"
            else:
                b = "local"
            buckets[b] = buckets.get(b, 0) + 1
        fallback_pool = {
            "total": len(chain),
            "openrouter": buckets.get("openrouter", 0),
            "groq": buckets.get("groq", 0),
            "gemini": buckets.get("gemini", 0),
            "nvidia": buckets.get("nvidia", 0),
            "deepseek": buckets.get("deepseek", 0),
            "opencode": buckets.get("opencode", 0),
            "local": buckets.get("local", 0),
        }
    except Exception as exc:
        log.debug("fallback_pool unavailable: %s", exc)

    return StatusResponse(
        ready=getattr(runtime, "orchestrator", None) is not None,
        events_path=".swarm/patch_log.jsonl",
        event_count=len(events) + total_qdrant_points,
        llamacpp_base_url=os.getenv("LLAMACPP_URL", "http://127.0.0.1:8080"),
        environment="development",
        llamacpp_reachable=ollama_reachable,
        vision_configured=True,
        vision_runtime_available=True,
        vision_tool_exposed=True,
        vision_models_configured=vision_models or installed_models,
        vision_models_installed=vision_models or installed_models,
        installed_model_count=len(installed_models),
        installed_models=installed_models,
        primary_vision_model=vision_models[0] if vision_models else None,
        fallback_pool=fallback_pool,
    )


@router.get("/readyz")
async def readyz(runtime: Any = Depends(runtime_dep)) -> dict[str, Any]:
    report = await SystemService.get_health_report(runtime)
    ollama_reachable = await SystemService.check_ollama_reachable()
    installed_models = await _safe_ollama_models(runtime)
    checks = {
        "runtime_started": getattr(runtime, "orchestrator", None) is not None,
        "llamacpp_reachable": ollama_reachable,
        "models_loaded": len(installed_models) > 0,
        "health_score_ok": report["health_score"] >= 60,
    }
    ready = all(checks.values())
    return {
        "status": "ready" if ready else "not-ready",
        "ready": ready,
        "checks": checks,
        "health_score": report["health_score"],
        "overall": report["overall"],
    }


@router.get("/events")
async def list_events(runtime: Any = Depends(runtime_dep)):
    all_ev = await _safe_events(runtime)
    return {"count": len(all_ev), "events": all_ev[-50:]}


@router.get("/traces")
def list_traces(limit: int = Query(50, le=1000), orch: Any = Depends(get_orchestrator)):
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
    cap_names = list(cap_data["tools"]["names"])

    # Include the LIVE external MCP tools (once loaded by the background startup
    # task) so the count/banner reflect what the agent can ACTUALLY call via
    # action=mcp — not just the built-in ~22. Non-spawning: never starts npx/uvx
    # on a dashboard poll; returns [] while the manager is still initializing.
    try:
        from runtime_v2.services.tool_executor import get_loaded_mcp_tools

        mcp_tools = get_loaded_mcp_tools()
        for t in mcp_tools:
            name = f"mcp:{t['server']}:{t['name']}"
            if name not in cap_names:
                cap_names.append(name)
    except Exception as exc:
        log.debug("MCP tools merge skipped on /tools: %s", exc)

    return ToolListResponse(
        capabilities=cap_names,
        count=len(cap_names),
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
        total_qdrant_points += len(cached_keys)  # Add local cache to total just in case

    try:
        from swarm_os.services.vector_store import VectorStore

        vs = VectorStore()
        collections = (await vs.client.get_collections()).collections
        for c in collections:
            count = (await vs.client.count(c.name)).count
            total_qdrant_points += count
            cached_keys.append(f"qdrant_{c.name}_{count}")
    except Exception as exc:
        log.debug("Failed to fetch Qdrant cache status: %s", exc)
        pass

    return CacheStatusResponse(cache_size=total_qdrant_points, cached_keys=cached_keys)


@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(payload: ToolExecuteRequest, runtime=Depends(runtime_dep)):
    if hasattr(runtime, "agent_runtime") and runtime.agent_runtime is not None:
        try:
            result = await runtime.agent_runtime.call_tool(
                payload.capability, payload.payload, cache_key=payload.cache_key
            )
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            elif hasattr(result, "dict"):
                result = result.dict()
            return ToolExecuteResponse(
                status="success", capability=payload.capability, data=result
            )
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Capability '{payload.capability}' not found"
            )
        except Exception:
            log.exception("Tool execution failed in agent_runtime.call_tool")
            raise HTTPException(status_code=500, detail="Tool execution failed")

    if hasattr(runtime, "call_tool") and runtime.call_tool is not None:
        try:
            result = await runtime.call_tool(payload.capability, payload.payload)
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            elif hasattr(result, "dict"):
                result = result.dict()
            return ToolExecuteResponse(
                status="success", capability=payload.capability, data=result
            )
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"Capability '{payload.capability}' not found"
            )
        except Exception:
            log.exception("Tool execution failed in runtime.call_tool")
            raise HTTPException(status_code=500, detail="Tool execution failed")

    raise HTTPException(
        status_code=501, detail="tool execution not implemented in this runtime"
    )


@router.post("/generate", response_model=GenerateResponse)
async def generate(payload: GenerateRequest, orch=Depends(get_orchestrator)):
    # Fixes/corrections (T2 deep repair, mutation loop, self-repair) call
    # /generate without a model — default to DeepSeek V4 Flash (funded, cheap,
    # instruction-following) instead of local qwen. A repair fix is a complex
    # reasoning task; the local 4B burns turns and produces weak patches.
    try:
        from runtime_v2.services._llm_client import (
            _analysis_cloud_model,
            _analysis_cloud_enabled,
        )

        if _analysis_cloud_enabled():
            _model = _analysis_cloud_model()  # openai/deepseek-v4-flash by default
        else:
            _model = "qwen3.5-4b"
    except Exception:
        _model = "openai/deepseek-v4-flash"
    _model = (payload.model or "").strip() or _model

    # Accept both shapes: a bare `prompt` string (frontend/fix clients) and the
    # legacy brain payload that sends a full `messages` list. Derive the prompt
    # from the last user message when `prompt` is absent, otherwise the brain
    # path 422s (messages is not a valid `prompt`).
    if payload.prompt:
        _messages = [{"role": "user", "content": payload.prompt}]
    elif payload.messages:
        _messages = payload.messages
    else:
        raise HTTPException(
            status_code=422, detail="Either 'prompt' or 'messages' is required"
        )

    # Check if the model requests a cloud provider, otherwise default to local llama.cpp.
    # Use the shared _is_local_model() (NOT startswith("openai/")) so cloud models
    # like openai/deepseek-v4-flash are correctly treated as cloud — the old check
    # misclassified them as local and sent them to the wrong endpoint.
    from runtime_v2.services.fallback_manager import _is_local_model

    is_local = _is_local_model(_model)
    litellm_model = (
        f"openai/{_model}" if is_local and not _model.startswith("openai/") else _model
    )

    kwargs = {
        "model": litellm_model,
        "messages": _messages,
        "temperature": 0.7,
        "timeout": 120.0,
        "num_ctx": 16384,
        "num_retries": 5,
        "stop": ["<|im_end|>", "<|endoftext|>", "</s>"],
    }

    if is_local:
        kwargs["api_base"] = os.getenv("LLAMACPP_URL", "http://127.0.0.1:8080") + "/v1"
        kwargs["api_key"] = "llama"
    else:
        # Cloud fix/correction path: give the model its own endpoint/key so the
        # request genuinely reaches the cloud provider (DeepSeek via OpenCode Go,
        # or whatever OPENAI_API_BASE is set to) — not the local llama.cpp slot.
        base = os.getenv("OPENAI_API_BASE", "")
        key = os.getenv("OPENAI_API_KEY", "")
        if base:
            kwargs["api_base"] = base
        if key:
            kwargs["api_key"] = key

    try:
        import litellm

        resp = await litellm.acompletion(**kwargs)
        content = resp.choices[0].message.content or ""
        try:
            from runtime_v2.services.usage_log import record_response

            record_response(resp, litellm_model, source="api_generate")
        except Exception as usage_err:
            log.debug("usage log skipped: %s", usage_err)
    except Exception:
        # Cloud failure (e.g. OpenCode Go 'Insufficient balance') must NOT take
        # /generate down — degrade to the local llama.cpp model like every
        # other consumer of the fallback chain does.
        if not is_local:
            log.exception(
                "Generation failed on cloud model %s — falling back to local", _model
            )
            try:
                local_kwargs = dict(kwargs)
                local_kwargs["model"] = "openai/qwen3.5-4b"
                local_kwargs["api_base"] = (
                    os.getenv("LLAMACPP_URL", "http://127.0.0.1:8080") + "/v1"
                )
                local_kwargs["api_key"] = "llama"
                resp = await litellm.acompletion(**local_kwargs)
                content = resp.choices[0].message.content or ""
                _model = "qwen3.5-4b"
                try:
                    from runtime_v2.services.usage_log import record_response

                    record_response(resp, local_kwargs["model"], source="api_generate_local_fallback")
                except Exception as usage_err:
                    log.debug("usage log skipped: %s", usage_err)
            except Exception:
                log.exception("Local fallback generation also failed")
                raise HTTPException(status_code=502, detail="LLM generation failed")
        else:
            log.exception("Generation failed")
            raise HTTPException(status_code=502, detail="LLM generation failed")

    return GenerateResponse(
        content=content,
        model=_model,
    )


@router.post("/assign", response_model=AssignResponse)
def assign(payload: AssignRequest, orch=Depends(get_orchestrator)):
    score = 100
    accepted = True
    return AssignResponse(
        accepted=accepted,
        node_id=payload.node.get("node_id", "default"),
        job_id=payload.job.get("job_id", "default"),
        score=score,
    )


@router.post("/models/autoassign", response_model=AutoAssignResponse)
async def autoassign():
    try:
        mapping = await ChatService.autoassign()
        return AutoAssignResponse(mapping=mapping)
    except Exception as e:
        log.error(f"AutoAssign failed: {e}")
        raise HTTPException(status_code=502, detail="Model auto-assignment failed")


def _classify_event_outcome(event: dict) -> str:
    """Classify an event's outcome for dashboard success/fail/partial stats.

    Outcomes live in several places depending on who wrote the event
    (agent_service_v2 envelopes, orchestrator envelopes, legacy flat events):
      - learning_outcome.result
      - payload.status / payload.outcome / payload.result
      - top-level status / outcome / success
      - event_type itself (generation_completed/stream_completed = success,
        generation_failed/AGENT_ERROR/record_failure = fail)
    """
    pl = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    raw = (
        ((event.get("learning_outcome") or {}).get("result"))
        or pl.get("status")
        or event.get("status")
        or pl.get("outcome")
        or event.get("outcome")
        or pl.get("result")
        or event.get("result")
        or ""
    )
    o = str(raw).lower()
    if o in ("success", "completed", "ok", "healthy", "succeeded"):
        return "success"
    if o in ("partial", "in_progress", "pending"):
        return "partial"
    if o in ("fail", "failure", "failed", "error", "unhealthy", "blocked", "aborted"):
        return "fail"
    # Fall back to event_type semantics for envelope events with no explicit status
    et = str(event.get("event_type") or "").lower()
    if "fail" in et or "error" in et:
        return "fail"
    if et in (
        "generation_completed",
        "stream_completed",
        "agent_action",
        "tool_result",
        "task_completed",
    ):
        return "success"
    if "partial" in et or "pending" in et:
        return "partial"
    return "unknown"


@router.get("/timeline", response_model=TimelineResponse)
async def timeline(
    window_minutes: int = Query(60, le=1440), runtime: Any = Depends(runtime_dep)
):
    events_path = Path("data/events/events.jsonl")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    buckets = defaultdict(
        lambda: {
            "event_count": 0,
            "success_count": 0,
            "partial_count": 0,
            "fail_count": 0,
        }
    )

    if events_path.exists():
        # BUG FIX: Read file in a thread using EventLogRepository to avoid blocking the async event loop.
        import asyncio
        from swarm_os.repositories.event_log_repo import EventLogRepository

        repo = EventLogRepository(event_log_path=events_path)
        try:
            # STA-2: bound the read to the most recent 500 events so a huge
            # events.jsonl is never materialized into a list on every /timeline
            # poll (the endpoint already filters by window_minutes).
            events, _ = await asyncio.to_thread(repo.read_events, 0, 500)
        except Exception as exc:
            log.warning(f"Failed to read events for timeline: {exc}")
            events = []

        for event in events:
            try:
                raw_ts = (
                    event.get("occurred_at")
                    or event.get("timestamp")
                    or event.get("ts")
                )
                if not raw_ts:
                    continue
                ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
                bucket = ts.replace(second=0, microsecond=0).isoformat(
                    timespec="minutes"
                )
                buckets[bucket]["event_count"] += 1
                outcome = _classify_event_outcome(event)
                if outcome == "success":
                    buckets[bucket]["success_count"] += 1
                elif outcome == "partial":
                    buckets[bucket]["partial_count"] += 1
                elif outcome == "fail":
                    buckets[bucket]["fail_count"] += 1
            except Exception as e:
                log.warning("Failed to fetch ollama models: %s", e)
                continue

    all_ev = await _safe_events(runtime)
    for ev in all_ev:
        try:
            event = ev.to_dict() if hasattr(ev, "to_dict") else ev
            raw_ts = (
                event.get("occurred_at") or event.get("timestamp") or event.get("ts")
            )
            if not raw_ts:
                continue
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            bucket = ts.replace(second=0, microsecond=0).isoformat(timespec="minutes")
            buckets[bucket]["event_count"] += 1
            outcome = _classify_event_outcome(event)
            if outcome == "success":
                buckets[bucket]["success_count"] += 1
            elif outcome == "partial":
                buckets[bucket]["partial_count"] += 1
            elif outcome == "fail":
                buckets[bucket]["fail_count"] += 1
        except Exception as e:
            log.warning("Failed to process timeline event: %s", e)
            continue

    points = [
        TimelinePointResponse(bucket=bucket, **values)
        for bucket, values in sorted(buckets.items())
    ]
    return TimelineResponse(window_minutes=window_minutes, points=points)


@router.get("/memory/search")
async def memory_search(q: str, limit: int = Query(8, le=100)):
    try:
        from runtime_v2.services.memory_core import (
            get_embedding,
            _get_shard_name,
            _moe_route_shards,
        )
        from swarm_os.services.vector_store import VectorStore

        # BUG FIX: Wrap synchronous get_embedding() in asyncio.to_thread to avoid
        # blocking the event loop during local model inference.
        import asyncio as _asyncio

        vector = await _asyncio.to_thread(get_embedding, q)
        if not vector:
            return {"results": []}

        active_shards = _moe_route_shards(q)
        results = []
        for shard in active_shards:
            collection = _get_shard_name(shard)
            try:
                vs = VectorStore(collection_name=collection)
                hits = await vs.search(query_vector=vector, limit=limit)
                for hit in hits:
                    results.append(
                        {
                            "id": hit.get("id", ""),
                            "score": hit.get("score", 0.0),
                            "text": hit.get("text", ""),
                            "sender": hit.get("sender", "system"),
                            "timestamp": hit.get("timestamp", ""),
                        }
                    )
            except Exception as e:
                import logging

                logging.getLogger(__name__).error(
                    f"Error querying shard {collection}: {e}"
                )

        results.sort(key=lambda x: x["score"], reverse=True)
        return {"results": results[:limit]}
    except HTTPException:
        raise
    except Exception:
        log.exception("Memory search failed")
        # BUG FIX: Raise HTTPException with 500 instead of returning {"error": ...} with 200 OK.
        # Clients cannot distinguish success from failure when status code is always 200.
        raise HTTPException(status_code=500, detail="Memory search failed")


@router.get("/traces/summary")
def trace_summary(
    orch: Any = Depends(get_orchestrator), limit: int = Query(50, le=1000)
) -> dict[str, Any]:
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
    except Exception:
        log.exception("Timeline fetch failed")
        return {
            "count": 0,
            "window": {"limit": limit},
            "status_counts": {},
            "phase_counts": {},
            "model_counts": {},
            "latency_ms": {"count": 0, "avg": 0.0, "max": 0.0, "min": 0.0},
            "error": "timeline unavailable",
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
async def get_router_stats(
    orch: Any = Depends(get_orchestrator),
    runtime: Any = Depends(runtime_dep),
    limit: int = Query(100, le=1000),
) -> dict[str, Any]:
    """Return router/model-selection statistics derived from recent traces."""

    # Normalize model names for accurate distribution reporting. Historical
    # traces may record RETIRED model aliases (qwen3.5-9b was pruned 2026-08-05;
    # qwen3:14b / qwen2.5:7b-instruct predate the qwen3.5-4b migration). Mapping
    # them to the current local generation model keeps the dashboard's model
    # distribution accurate instead of reporting models that no longer exist.
    def _norm_model(raw: str) -> str:
        m = str(raw).strip().lower()
        if not m or m == "unknown":
            return "unknown"
        if m in ("qwen3.5-4b", "deepseek-v4-flash") or "3.5-4b" in m:
            return m
        if any(
            x in m
            for x in (
                "qwen3.5-9b",
                "qwen3:14b",
                "14b",
                "12b",
                "7b-instruct",
                "qwen2.5",
                "qwen2",
                "qwen-tuned",
                "qwen3-vl",
                "3b-instruct",
            )
        ):
            return "qwen3.5-4b"
        return str(raw)

    try:
        items = orch.get_recent_traces(limit=limit) if orch is not None else []

        model_counts: Counter = Counter()
        status_counts: Counter = Counter()
        durations: list[float] = []
        total = len(items)

        for item in items:
            if not isinstance(item, dict):
                continue
            model = _norm_model(item.get("model") or item.get("model_id") or "unknown")
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
                pl = (
                    event.get("payload")
                    if isinstance(event.get("payload"), dict)
                    else {}
                )
                model = _norm_model(event.get("model") or pl.get("model") or "unknown")
                outcome = _classify_event_outcome(event)
                model_counts[str(model)] += 1
                if outcome == "success":
                    status_counts["success"] += 1
                elif outcome == "fail":
                    status_counts["fail"] += 1
                else:
                    status_counts["unknown"] += 1
                total += 1
            except Exception as e:
                log.warning("Failed to parse tool success response: %s", e)
                continue

        most_used = model_counts.most_common(1)[0][0] if model_counts else "none"
        success_count = status_counts.get("success", 0) + status_counts.get("ok", 0)
        success_rate = (
            round((success_count / max(1, total)) * 100) if total > 0 else 100
        )

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
            "error": "router stats unavailable",
        }


@router.get("/critic")
async def get_critic_stats(
    orch: Any = Depends(get_orchestrator),
    runtime: Any = Depends(runtime_dep),
    limit: int = Query(200, le=1000),
) -> dict[str, Any]:
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
            outcome = str(
                ((item.get("learning_outcome") or {}).get("result")) or ""
            ).lower()
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
                outcome = str(
                    ((event.get("learning_outcome") or {}).get("result")) or ""
                ).lower()
                if outcome == "success":
                    accepted += 1
                elif outcome == "fail":
                    rejected += 1
                elif outcome == "partial":
                    partial += 1
            except Exception as e:
                log.warning("Failed to format trace step: %s", e)
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
            "verdict": "healthy"
            if accept_rate >= 70
            else ("degraded" if accept_rate >= 40 else "critical"),
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
            "error": "critic stats unavailable",
        }


def _memory_timestamp(payload: dict) -> float:
    """Sortable float from a memory payload's timestamp, never raising.

    Memory writers are inconsistent: memory_core/reflection_loop store
    `time.time()` floats, api_features stores ISO-8601 strings, and payloads
    can carry an explicit null timestamp (e.g. `valid_until: None`). A bare
    `float(x.get("timestamp", 0))` crashed the /memories sort on any None or
    ISO string; degrade all unparseable shapes to 0.0 (sort to the bottom)."""
    raw = payload.get("timestamp")
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        text = raw.strip()
        try:
            return float(text)
        except (TypeError, ValueError):
            log.debug("Failed to parse timestamp as float, falling back to isoformat")
        try:
            from datetime import datetime

            return datetime.fromisoformat(text).timestamp()
        except (TypeError, ValueError):
            return 0.0
    return 0.0


@router.get("/memories")
async def get_memories():
    """Retrieve all learned memories from Qdrant, omitting the codebase chunks."""
    memories_by_category = {}
    try:
        from swarm_os.services.vector_store import VectorStore

        vs = VectorStore()
        collections = (await vs.client.get_collections()).collections

        for c in collections:
            name = c.name
            if name in ["codebase", "codebase_index"]:
                continue

            # Fetch points with payload
            response = await vs.client.scroll(
                collection_name=name, limit=10000, with_payload=True, with_vectors=False
            )

            points = response[0] if isinstance(response, tuple) else []
            if points:
                payloads = [p.payload for p in points if p.payload]
                # Sort descending by timestamp (newest first).
                # Use a default of 0 for payloads without a timestamp to keep them at the bottom.
                payloads.sort(key=_memory_timestamp, reverse=True)
                memories_by_category[name] = payloads

        return {"status": "success", "data": memories_by_category}
    except Exception:
        log.exception("Failed to fetch memories")
        raise HTTPException(status_code=500, detail="Failed to fetch memories")
