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
_heal_cache_lock = asyncio.Lock()


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


class EmailListRequest(BaseModel):
    folder: str = "INBOX"
    limit: int = 20
    unread_only: bool = False
    account: str | None = None


class EmailReadRequest(BaseModel):
    uid: str
    folder: str = "INBOX"
    account: str | None = None


class EmailSearchRequest(BaseModel):
    query: str
    folder: str = "INBOX"
    limit: int = 20
    account: str | None = None


class EmailDraftRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str = ""
    attachments: list[str] = []
    account: str | None = None


class EmailSendRequest(BaseModel):
    send_token: str
    confirmed: bool = False


class EmailThreadRequest(BaseModel):
    uid: str
    folder: str = "INBOX"
    account: str | None = None


class EmailUnsubscribeScanRequest(BaseModel):
    folder: str = "INBOX"
    limit: int = 50
    account: str | None = None


class EmailManageRequest(BaseModel):
    op: str
    uid: str
    folder: str = "INBOX"
    target_folder: str | None = None
    account: str | None = None


class EmailSummarizeThreadRequest(BaseModel):
    uid: str
    folder: str = "INBOX"
    account: str | None = None


class EmailReplyDraftRequest(BaseModel):
    uid: str
    note: str = ""
    folder: str = "INBOX"
    account: str | None = None


class EmailDigestRequest(BaseModel):
    days: int = 7
    folder: str = "INBOX"
    account: str | None = None


class BrowserActionRequest(BaseModel):
    operation: str
    url: str = ""
    name: str = ""
    role: str = ""
    text: str = ""
    value: str = ""
    selector: str = ""


class FileReadRequest(BaseModel):
    path: str


class FileWriteRequest(BaseModel):
    path: str
    content: str
    approved: bool = False


class GrantRequest(BaseModel):
    target: str
    key: str
    tier: str


class TaskRequest(BaseModel):
    goal: str
    schedule: str = "daily 08:00"
    enabled: bool = True


class TaskToggleRequest(BaseModel):
    enabled: bool


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
            "foreground_window": fg.get("result", {}).get("title", "")
            if fg.get("ok")
            else "",
            "cursor": cur.get("result", {}) if cur.get("ok") else {},
            "windows": wins.get("result", {}).get("windows", [])
            if wins.get("ok")
            else [],
        }
    except Exception:
        log.exception("Screen state probe failed")
        return {"available": False, "error": "screen state unavailable"}


async def _heal_status() -> Dict[str, Any]:
    """Cached heal status — never blocks the 10s poll on the ~16s probe scan.

    The whole check -> probe -> write runs under ONE lock hold so a stale cache
    cannot stampede: a concurrent waiter blocks on the lock, then reads the
    fresh value written by the first caller instead of re-running the ~16s
    probe set."""
    async with _heal_cache_lock:
        now = asyncio.get_running_loop().time()
        if now - _heal_cache["ts"] < _HEAL_CACHE_TTL and _heal_cache["value"]:
            return _heal_cache["value"]
        try:
            from swarm_os.healing.healing_service import HealingService

            hs = HealingService()
            value = await hs.status()
        except Exception:
            log.exception("Heal status probe failed")
            value = {"available": False, "error": "heal status unavailable"}
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
                cooled.append(
                    {
                        "model": key,
                        "failures": entry.get("failures", 0),
                        "cooldown_remaining_s": round(max(0, remaining)),
                        "last_error": entry.get("last_error", "")[:120],
                    }
                )
        cooled.sort(key=lambda c: c["cooldown_remaining_s"], reverse=True)
        return {"models_in_cooldown": cooled, "fallback_stats": get_fallback_stats()}
    except Exception:
        log.exception("Model cooldown surface failed")
        return {
            "models_in_cooldown": [],
            "fallback_stats": {},
            "error": "model cooldown status unavailable",
        }


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
                memory_counts[c.name] = (
                    info.count if hasattr(info, "count") else int(info)
                )
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
        raise HTTPException(
            status_code=400,
            detail=f"Unknown issue '{issue}'. Known: {sorted(SYSTEM_RECOVERY_ACTIONS)}",
        )

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
    except Exception:
        log.exception("Recovery %s failed", issue)
        return {
            "status": "error",
            "issue": issue,
            "result": {"ok": False, "error": "recovery action failed"},
        }

    # Learn from the outcome — persist a grounded reflexion rule on success.
    if result.get("ok"):
        try:
            from swarm_os.services.reflection_loop import get_reflection_service

            corrections = {
                "memory_pressure": "Check memory pressure; empty working sets of non-critical processes to relieve RAM (free_memory) before escalating.",
                "disk_space": "Check disk usage; clean stale temp files (>24h) in the OS temp folder when a drive exceeds 90%.",
                "runaway_process": "Identify the runaway process by pid/name, confirm it is not system-critical, then terminate it gracefully.",
                "temp_growth": "Check temp folder growth; remove stale files older than 24h outside protected cache subdirs.",
                "stopped_service": "Restart the stopped Windows service by its exact service_name from the signal detail.",
            }
            correction = corrections.get(
                issue,
                f"Recurring system issue '{issue}' resolved via {result.get('action')}; re-check before proceeding.",
            )

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

            await _store()
        except Exception as exc:
            log.warning("Failed to store system lesson: %s", exc)

    return {
        "status": "executed",
        "issue": issue,
        "destructive": destructive,
        "result": result,
    }


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
    # SECURITY: `set_screen_autonomous` is a self-bypass primitive — flipping
    # autonomous ON via the HTTP API would let a loopback caller take over the
    # real mouse/keyboard without the operator enabling SWARM_SCREEN_AUTONOMOUS=1.
    # Match screen_handler's rule: autonomous can only be ENABLED when it is
    # already on (idempotent no-op), never as an escalation from human mode.
    from swarm_os.lib.mcp import screen as _screen

    if req.enabled and not _screen.SCREEN_AUTONOMOUS:
        return {
            "status": "blocked",
            "result": {
                "ok": False,
                "error": (
                    "HUMAN-CONTROL MODE: autonomous screen input cannot be enabled "
                    "over the API. An operator must set SWARM_SCREEN_AUTONOMOUS=1."
                ),
            },
        }
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
    root = os.getenv(
        "SWARM_SCREENSHOT_DIR", os.path.join(os.getcwd(), "logs", "screenshots")
    )
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
        return {
            "status": "ok",
            "agent_id": agent_id,
            "model": model_name,
            "backend": backend,
        }
    except Exception:
        log.exception("Failed to reassign model for %s", agent_id)
        raise HTTPException(status_code=500, detail="Failed to reassign model")


# ---------------------------------------------------------------------------
# Email — inbox as a tool (read ops free, send is human-approved)
# ---------------------------------------------------------------------------


@router.get("/email/status")
async def control_email_status() -> Dict[str, Any]:
    from swarm_os.services.email_service import email_config_status

    return email_config_status()


@router.post("/email/list")
async def control_email_list(req: EmailListRequest) -> Dict[str, Any]:
    from swarm_os.services.email_service import email_list

    return await asyncio.to_thread(
        email_list, req.folder, req.limit, req.unread_only, req.account
    )


@router.post("/email/read")
async def control_email_read(req: EmailReadRequest) -> Dict[str, Any]:
    from swarm_os.services.email_service import email_read

    return await asyncio.to_thread(email_read, req.uid, req.folder, req.account)


@router.post("/email/search")
async def control_email_search(req: EmailSearchRequest) -> Dict[str, Any]:
    from swarm_os.services.email_service import email_search

    return await asyncio.to_thread(
        email_search, req.query, req.folder, req.limit, req.account
    )


@router.post("/email/draft")
async def control_email_draft(req: EmailDraftRequest) -> Dict[str, Any]:
    """Stage a message and return a send_token — NOT sent. The UI must present
    the draft for human approval, then call /control/email/send with confirmed=true."""
    from swarm_os.services.email_service import email_draft

    return await asyncio.to_thread(
        email_draft, req.to, req.subject, req.body, req.cc, req.attachments, req.account
    )


@router.post("/email/send")
async def control_email_send(req: EmailSendRequest) -> Dict[str, Any]:
    """Human-approved send. Only proceeds with confirmed=true (the UI's approval
    confirm step); an unconfirmed token is refused."""
    from swarm_os.services.email_service import email_send

    return await asyncio.to_thread(email_send, req.send_token, req.confirmed)


@router.post("/email/thread")
async def control_email_thread(req: EmailThreadRequest) -> Dict[str, Any]:
    """Return all messages in the same thread as uid (grouped by normalized
    subject + sender domain, ordered by date)."""
    from swarm_os.services.email_service import email_thread

    return await asyncio.to_thread(email_thread, req.uid, req.folder, req.account)


@router.post("/email/summarize-thread")
async def control_email_summarize_thread(
    req: EmailSummarizeThreadRequest,
) -> Dict[str, Any]:
    """LLM summary of a whole thread: conversation, each participant's ask,
    action items, deadlines."""
    from swarm_os.services.email_service import email_summarize_thread

    return await email_summarize_thread(req.uid, folder=req.folder, account=req.account)


@router.post("/email/unsubscribe-scan")
async def control_email_unsubscribe_scan(
    req: EmailUnsubscribeScanRequest,
) -> Dict[str, Any]:
    """Scan recent mail for List-Unsubscribe headers and report the mechanisms
    (newsletter management parity)."""
    from swarm_os.services.email_service import email_unsubscribe_scan

    return await asyncio.to_thread(
        email_unsubscribe_scan, req.folder, req.limit, req.account
    )


@router.post("/email/manage")
async def control_email_manage(req: EmailManageRequest) -> Dict[str, Any]:
    """Inbox mutations: mark_read, mark_unread, archive, move, delete. Destructive
    ops (archive/move/delete) require approval upstream (the console gates them)."""
    from swarm_os.services.email_service import email_manage

    return await asyncio.to_thread(
        email_manage, req.op, req.uid, req.folder, req.target_folder, req.account
    )


@router.post("/email/reply-draft")
async def control_email_reply_draft(req: EmailReplyDraftRequest) -> Dict[str, Any]:
    """Draft a tone-matched reply to a message (reads the thread, matches the
    sender's tone). Returns a draft + send_token — sending still requires the
    human approval gate (email_send with confirmed=true)."""
    from swarm_os.services.email_service import email_reply_draft

    return await email_reply_draft(
        req.uid, note=req.note, folder=req.folder, account=req.account
    )


@router.post("/email/digest")
async def control_email_digest(req: EmailDigestRequest) -> Dict[str, Any]:
    """Weekly/daily inbox digest: summarize recent mail into action items,
    newsletters, FYIs, and urgent items. Runnable on a schedule."""
    from swarm_os.services.email_service import email_digest

    return await email_digest(days=req.days, folder=req.folder, account=req.account)


# ---------------------------------------------------------------------------
# Browser — persistent a11y-tree driven session
# ---------------------------------------------------------------------------


@router.post("/browser/action")
async def control_browser_action(req: BrowserActionRequest) -> Dict[str, Any]:
    from swarm_os.lib.mcp.playwright import playwright_handler

    payload = {k: v for k, v in req.dict().items() if v not in ("", None)}
    try:
        async with asyncio.timeout(120):
            result = await playwright_handler(payload)
    except TimeoutError:
        result = {"ok": False, "error": "browser operation timed out"}
    return result


@router.get("/browser/state")
async def control_browser_state() -> Dict[str, Any]:
    from swarm_os.lib.mcp.playwright import playwright_handler

    async with asyncio.timeout(30):
        return await playwright_handler({"operation": "browser_state"})


@router.get("/browser/image")
async def control_browser_image(name: str) -> FileResponse:
    """Serve a browser screenshot PNG from the project root (basename only)."""
    root = os.getcwd()
    safe = os.path.basename(str(name))
    path = os.path.join(root, safe)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"browser image '{safe}' not found")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Files — read free, write is human-approved
# ---------------------------------------------------------------------------


def _resolve_project_file(raw: str) -> str:
    """Resolve a relative path inside the project root; refuse traversal."""
    root = os.getcwd()
    joined = os.path.abspath(os.path.join(root, raw))
    # os.path.commonpath refuses path components that don't share a common root
    # (e.g. a sibling dir whose name merely starts with the project dir).
    try:
        common = os.path.commonpath([root, joined])
    except ValueError:
        common = ""
    if common != os.path.abspath(root):
        raise HTTPException(status_code=400, detail="path escapes project root")
    return joined


@router.get("/file/read")
async def control_file_read(path: str) -> Dict[str, Any]:
    """Read a project file (free — read is how the local model Q&A works)."""
    try:
        full = _resolve_project_file(path)
        if not os.path.isfile(full):
            return {"ok": False, "error": f"not a file: {path}"}
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(60000)
        return {"ok": True, "path": path, "content": content, "bytes": len(content)}
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("file read failed for %s: %s", path, exc)
        return {"ok": False, "error": str(exc)}


@router.post("/file/write")
async def control_file_write(req: FileWriteRequest) -> Dict[str, Any]:
    """Write a project file — human-approved only. Refuses without approved=true,
    matching the email-send + destructive-recovery approval pattern."""
    if not req.approved:
        return {
            "ok": False,
            "approved_required": True,
            "reason": f"Writing {req.path} requires human approval. Set approved=true to confirm.",
        }
    try:
        full = _resolve_project_file(req.path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"ok": True, "path": req.path, "bytes": len(req.content)}
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("file write failed for %s: %s", req.path, exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Permission tiers + per-site/per-app grants (2026 SOTA)
# ---------------------------------------------------------------------------


@router.get("/permissions")
async def control_permissions() -> Dict[str, Any]:
    from swarm_os.services import permission_tiers as pt

    return {"grants": pt.all_grants(), "tool_tiers": pt._TOOL_TIERS}


@router.post("/permissions/grant")
async def control_permissions_grant(req: GrantRequest) -> Dict[str, Any]:
    from swarm_os.services import permission_tiers as pt

    ok = pt.set_grant(req.target, req.key, req.tier)
    if not ok:
        raise HTTPException(
            status_code=400, detail="invalid tier (free/ask/important/approval)"
        )
    return {"ok": True, "target": req.target, "key": req.key, "tier": req.tier}


# ---------------------------------------------------------------------------
# Recurring agent tasks (2026 SOTA). Ceiling + fail-closed on unmapped goals
# enforced in task_scheduler.run_due_tasks.
# ---------------------------------------------------------------------------


@router.get("/tasks")
async def control_tasks() -> Dict[str, Any]:
    from swarm_os.services import task_scheduler as ts

    return {"tasks": ts.list_tasks()}


@router.post("/tasks")
async def control_tasks_create(req: TaskRequest) -> Dict[str, Any]:
    from swarm_os.services import task_scheduler as ts

    task = ts.create_task(req.goal, req.schedule, req.enabled)
    return {"ok": True, "task": task}


@router.delete("/tasks/{task_id}")
async def control_tasks_delete(task_id: str) -> Dict[str, Any]:
    from swarm_os.services import task_scheduler as ts

    return {"ok": ts.delete_task(task_id)}


@router.post("/tasks/{task_id}/toggle")
async def control_tasks_toggle(task_id: str, req: TaskToggleRequest) -> Dict[str, Any]:
    from swarm_os.services import task_scheduler as ts

    return {"ok": ts.set_task_enabled(task_id, req.enabled)}


@router.post("/tasks/run")
async def control_tasks_run() -> Dict[str, Any]:
    from swarm_os.services import task_scheduler as ts

    ran = await ts.run_due_tasks()
    return {"ok": True, "ran": ran}
