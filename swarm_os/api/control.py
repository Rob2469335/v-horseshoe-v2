# swarm_os/api/control.py
"""Command-center control plane — one place to SEE and CONTROL the whole machine.

Aggregates the whole-computer tiers (system probes + recovery + screen control)
plus health, models, and agent routing into a single `/control/*` surface for
the web command center. Wraps the existing healing pipeline (FailureDetector ->
Governor -> RecoveryEngine) and the screen-control module behind a REST API.

Safety model mirrors the CLI watchman:
  - safe system issues (memory pressure) auto-run under governor approval;
  - destructive issues (kill/clean/restart) require `approved: true` in the body
    (the UI surfaces an approval dialog before calling).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/control", tags=["control"])

# Short-TTL caches so the command center's 10s poll never re-runs heavy work
# (probe scan ~16s, heal status ~18s when infra is down). Warmed at startup.
_HEAL_CACHE_TTL = 8.0
_heal_cache: Dict[str, Any] = {"ts": 0.0, "value": {}}
_heal_cache_lock = threading.Lock()


class RecoverRequest(BaseModel):
    issue: str
    approved: bool = False


class ScreenActionRequest(BaseModel):
    action: str
    kwargs: Dict[str, Any] = {}


class AutonomousRequest(BaseModel):
    enabled: bool


class HealRunRequest(BaseModel):
    force: bool = False


# ---------------------------------------------------------------------------
# Overview — everything in one fetch
# ---------------------------------------------------------------------------

async def _screen_state() -> Dict[str, Any]:
    """Read-only screen state (no input actions)."""
    try:
        from swarm_os.lib.mcp.screen import (
            SCREEN_AUTONOMOUS,
            _SCREEN_MAX_ACTIONS,
            _screen_action_count,
            cursor_position,
            foreground_window,
            list_windows,
        )
        fg = await asyncio.to_thread(foreground_window)
        cur = await asyncio.to_thread(cursor_position)
        wins = await asyncio.to_thread(list_windows, 8)
        return {
            "autonomous": bool(SCREEN_AUTONOMOUS),
            "action_count": int(_screen_action_count),
            "max_actions": int(_SCREEN_MAX_ACTIONS),
            "foreground_window": fg.get("result", {}).get("title", "") if fg.get("ok") else "",
            "cursor": cur.get("result", {}) if cur.get("ok") else {},
            "windows": wins.get("result", {}).get("windows", []) if wins.get("ok") else [],
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


async def _heal_status() -> Dict[str, Any]:
    """Cached heal status — never blocks the 10s poll on the ~16s probe scan."""
    now = asyncio.get_event_loop().time()
    with _heal_cache_lock:
        if now - _heal_cache["ts"] < _HEAL_CACHE_TTL and _heal_cache["value"]:
            return _heal_cache["value"]
    try:
        from swarm_os.healing.healing_service import HealingService
        hs = HealingService()
        value = await hs.status()
    except Exception as exc:
        value = {"available": False, "error": str(exc)}
    with _heal_cache_lock:
        _heal_cache["ts"] = now
        _heal_cache["value"] = value
    return value


async def _model_surface(runtime: Any) -> Dict[str, Any]:
    """Installed models + per-agent model mapping."""
    installed = []
    try:
        from swarm_os.api.routes import _safe_ollama_models
        installed = await _safe_ollama_models(runtime)
    except Exception as exc:
        log.warning("Could not list installed models: %s", exc)
    agents = {}
    try:
        from runtime_v2.services.model_registry import AGENT_MODELS
        agents = {k: {"model": v[0], "backend": v[1]} for k, v in AGENT_MODELS.items()}
    except Exception as exc:
        log.warning("Could not read agent model mapping: %s", exc)
    return {"installed_models": installed, "agent_models": agents}


async def _resilience() -> Dict[str, Any]:
    """Gateway observability: which models are in cooldown and the fallback pool
    breakdown (Datadog/OpenLegion gateway best practice — surface retry/fallback
    state so silent recovery doesn't mask a degrading provider)."""
    try:
        from runtime_v2.services.fallback_manager import _cooldowns, get_fallback_stats
        import time as _time
        now = _time.time()
        cooled = []
        with _cooldowns_lock_sync():
            entries = list(_cooldowns.items())
        for key, entry in entries:
            remaining = entry.get("until", 0) - now
            if remaining > 0:
                cooled.append({
                    "model": key,
                    "failures": entry.get("failures", 0),
                    "cooldown_remaining_s": round(max(0, remaining)),
                    "last_error": entry.get("last_error", "")[:120],
                })
        cooled.sort(key=lambda c: c["cooldown_remaining_s"], reverse=True)
        return {"models_in_cooldown": cooled, "fallback_stats": get_fallback_stats()}
    except Exception as exc:
        return {"models_in_cooldown": [], "fallback_stats": {}, "error": str(exc)}


def _cooldowns_lock_sync():
    from runtime_v2.services.fallback_manager import _cooldown_sync_lock
    return _cooldown_sync_lock


@router.get("/overview")
async def control_overview(request: Request) -> Dict[str, Any]:
    """One-shot snapshot: heal status (incl. system probes), screen state,
    installed models, agent routing, and memory counts."""
    runtime = getattr(request.app.state, "runtime", None)
    heal = await _heal_status()
    screen = await _screen_state()
    models = await _model_surface(runtime)
    resilience = await _resilience()

    # Probe classification (destructive vs safe) for the UI
    probes: Dict[str, Any] = {}
    checks = heal.get("checks", {})
    for name, res in checks.items():
        if not isinstance(res, dict):
            continue
        detail = res.get("detail", {})
        if isinstance(detail, dict) and detail.get("issue"):
            probes[name] = {
                "ok": res.get("ok", True),
                "issue": detail.get("issue", name),
                "destructive": bool(detail.get("destructive", False)),
                "detail": detail,
            }

    memory_counts: Dict[str, int] = {}
    try:
        from swarm_os.services.vector_store import VectorStore
        vs = VectorStore()
        collections = (await vs.client.get_collections()).collections
        for c in collections:
            if c.name in ("codebase", "codebase_index"):
                continue
            try:
                info = await vs.client.count(collection_name=c.name)
                memory_counts[c.name] = info.count if hasattr(info, "count") else int(info)
            except Exception:
                continue
    except Exception as exc:
        log.warning("Could not count memories: %s", exc)

    return {
        "health": {
            "health_score": heal.get("health_score", 0),
            "recovery_readiness": heal.get("recovery_readiness", 0),
            "active_anomalies": heal.get("active_anomalies", 0),
            "heals_total": heal.get("heals_total", 0),
            "heals_success": heal.get("heals_success", 0),
            "last_heal_success": heal.get("last_heal_success"),
            "signals": heal.get("signals", []),
        },
        "probes": probes,
        "screen": screen,
        "models": models,
        "memory_counts": memory_counts,
        "resilience": resilience,
        "available": True,
    }


# ---------------------------------------------------------------------------
# Recovery — run a specific system recovery action (approval-gated)
# ---------------------------------------------------------------------------

@router.post("/recover")
async def control_recover(req: RecoverRequest) -> Dict[str, Any]:
    from swarm_os.healing.system_probes import run_system_probes
    from swarm_os.healing.system_recovery import (
        SYSTEM_RECOVERY_ACTIONS,
        DESTRUCTIVE_SYSTEM_ACTIONS,
    )

    issue = req.issue.strip()
    if issue not in SYSTEM_RECOVERY_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown issue '{issue}'. Known: {sorted(SYSTEM_RECOVERY_ACTIONS)}")

    destructive = issue in DESTRUCTIVE_SYSTEM_ACTIONS

    # Approval gate FIRST — an unapproved destructive request must return
    # immediately (no 16s probe scan), so the UI's two-click confirm is instant.
    if destructive and not req.approved:
        return {
            "status": "approval_required",
            "issue": issue,
            "destructive": True,
            "reason": f"Destructive system action '{issue}' requires human approval. Set approved=true to execute.",
            "detail": {},
        }

    # Grab the live probe detail to feed the action (never fabricate targets)
    probe_result = {}
    try:
        probes = await asyncio.to_thread(run_system_probes)
        probe_result = probes.get(issue, {}).get("detail", {})
    except Exception as exc:
        log.warning("Probe unavailable for %s: %s", issue, exc)

    anomaly = {"component": issue, "detail": probe_result}
    try:
        result = await asyncio.to_thread(SYSTEM_RECOVERY_ACTIONS[issue], anomaly)
    except Exception as exc:
        log.exception("Recovery %s failed", issue)
        return {"status": "error", "issue": issue, "result": {"ok": False, "error": str(exc)}}

    # Learn from the outcome — persist a grounded reflexion rule on success.
    if result.get("ok"):
        try:
            from swarm_os.healing.failure_detector import run_coro_sync
            from swarm_os.services.reflection_loop import get_reflection_service

            corrections = {
                "memory_pressure": "Check memory pressure; empty working sets of non-critical processes to relieve RAM (free_memory) before escalating.",
                "disk_space": "Check disk usage; clean stale temp files (>24h) in the OS temp folder when a drive exceeds 90%.",
                "runaway_process": "Identify the runaway process by pid/name, confirm it is not system-critical, then terminate it gracefully.",
                "temp_growth": "Check temp folder growth; remove stale files older than 24h outside protected cache subdirs.",
                "stopped_service": "Restart the stopped Windows service by its exact service_name from the signal detail.",
            }
            correction = corrections.get(issue, f"Recurring system issue '{issue}' resolved via {result.get('action')}; re-check before proceeding.")

            async def _store():
                await get_reflection_service().store_reflexion(
                    task=f"agent:healing system {issue}",
                    action=f"system:{result.get('action')}",
                    failure_reason=f"system {issue} detected via probe",
                    correction=correction,
                    do_not_repeat=f"Do NOT ignore repeated '{issue}' signals — a prior recovery used {result.get('action')}.",
                    component=f"system:{issue}",
                    confidence=0.75,
                )
            run_coro_sync(_store(), timeout=30.0)
        except Exception as exc:
            log.warning("Failed to store system lesson: %s", exc)

    return {"status": "executed", "issue": issue, "destructive": destructive, "result": result}


# ---------------------------------------------------------------------------
# Screen control
# ---------------------------------------------------------------------------

@router.get("/screen")
async def control_screen_state() -> Dict[str, Any]:
    return await _screen_state()


@router.post("/screen/action")
async def control_screen_action(req: ScreenActionRequest) -> Dict[str, Any]:
    from swarm_os.lib.mcp.screen import screen_handler
    payload = {"action": req.action, **req.kwargs}
    result = await asyncio.to_thread(screen_handler, payload)
    return {"status": "executed" if result.get("ok") else "blocked", "result": result}


@router.post("/screen/autonomous")
async def control_screen_autonomous(req: AutonomousRequest) -> Dict[str, Any]:
    from swarm_os.lib.mcp.screen import set_screen_autonomous
    result = await asyncio.to_thread(set_screen_autonomous, req.enabled)
    return {"status": "executed", "result": result}


@router.post("/screen/reset")
async def control_screen_reset() -> Dict[str, Any]:
    from swarm_os.lib.mcp.screen import reset_screen_action_count
    result = await asyncio.to_thread(reset_screen_action_count)
    return {"status": "executed", "result": result}


@router.get("/screen/image")
async def control_screen_image(name: str) -> FileResponse:
    """Serve a screenshot PNG from logs/screenshots (basename only)."""
    root = os.getenv("SWARM_SCREENSHOT_DIR", os.path.join(os.getcwd(), "logs", "screenshots"))
    safe = os.path.basename(str(name))
    path = os.path.join(root, safe)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"screenshot '{safe}' not found")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Healing cycle
# ---------------------------------------------------------------------------

@router.get("/heal")
async def control_heal_status() -> Dict[str, Any]:
    return await _heal_status()


@router.post("/heal/run")
async def control_heal_run(req: HealRunRequest) -> Dict[str, Any]:
    from swarm_os.healing.healing_service import HealingService
    hs = HealingService()
    result = await hs.run_once()
    result["force"] = req.force
    return result


@router.post("/agents/{agent_id}/model")
async def control_agent_model(agent_id: str, req: Dict[str, Any]) -> Dict[str, Any]:
    model_name = req.get("model_name")
    backend = req.get("backend", "local")
    if not model_name:
        raise HTTPException(status_code=400, detail="model_name is required")
    try:
        from runtime_v2.services.model_registry import AGENT_MODELS, save_overrides
        AGENT_MODELS[agent_id] = (model_name, backend)
        save_overrides()
        return {"status": "ok", "agent_id": agent_id, "model": model_name, "backend": backend}
    except Exception as exc:
        log.exception("Failed to reassign model for %s", agent_id)
        raise HTTPException(status_code=500, detail=str(exc))
