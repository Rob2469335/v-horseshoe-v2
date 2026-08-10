# swarm_os/api/admin.py
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from swarm_os.app.runtime_access import get_runtime_service
router = APIRouter(prefix='/admin', tags=['admin'])


# lazy import RuntimeGraph removed (fix circular import)
from swarm_os.kernel.status import build_status
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
from swarm_os.services.simulation_service import SimulationService

log = logging.getLogger(__name__)



def get_snapshot_repo(request: Request) -> FileSnapshotRepository:
    return get_runtime_service(request, "snapshot_repo")


def get_simulation_service(request: Request) -> SimulationService:
    return get_runtime_service(request, "simulation_service")


def latest_snapshot() -> Path | None:
    return FileSnapshotRepository().latest()


async def _resume_task(service: SimulationService, path: str) -> None:
    try:
        await service.run(resume_path=path)
    except Exception as exc:
        log.exception("resume task failed: %s", exc)


def _latest_snapshot_payload(request: Request) -> dict:
    repo = get_snapshot_repo(request)
    latest = repo.latest()
    snapshots = repo.list()

    population = []
    generation = None
    if latest:
        try:
            import json
            with open(latest, 'r', encoding='utf-8') as f:
                data = json.load(f)
                population = data.get("organisms", [])
                # BUG FIX: derive generation from the snapshot metadata so the
                # dashboard doesn't permanently show "Generation 0".
                if "generation" in data:
                    generation = data["generation"]
                elif "current_generation" in data:
                    generation = data["current_generation"]
                elif isinstance(generation, str):
                    generation = int(generation)
        except Exception as e:
            log.error(f"Failed to load snapshot {latest}: {e}")

    base = build_status(generation, None)

    return {
        **base,
        "latest_snapshot": str(latest) if latest else None,
        "snapshot_count": len(snapshots),
        "current_run": None,
        "queued": False,
        "running": False,
        "population": population,
    }


@router.get("/status")
def admin_status() -> dict:
    return build_status(None, None)


@router.get("/run-state")
def admin_run_state(request: Request) -> dict:
    payload = _latest_snapshot_payload(request)
    return {
        "scenario": payload.get("scenario"),
        "latest_snapshot": payload["latest_snapshot"],
        "snapshot_count": payload["snapshot_count"],
        "queued": payload["queued"],
        "running": payload["running"],
    }


@router.get("/snapshots")
def get_snapshots(request: Request) -> dict:
    repo = get_snapshot_repo(request)
    snaps = repo.list()

    return {
        "count": len(snaps),
        "snapshots": [str(p) for p in snaps],
    }


@router.get("/dashboard")
def get_dashboard(request: Request) -> dict:
    payload = _latest_snapshot_payload(request)
    return {
        "scenario": payload.get("scenario"),
        "generation": payload.get("generation"),
        "snapshot_count": payload["snapshot_count"],
        "latest_snapshot": payload["latest_snapshot"],
    }


@router.get("/generation")
def get_generation(request: Request) -> dict:
    payload = _latest_snapshot_payload(request)
    return {
        "scenario": payload.get("scenario"),
        "generation": payload.get("generation"),
        "latest_snapshot": payload["latest_snapshot"],
        "current_run": payload["current_run"],
        "population": payload["population"],
    }


@router.get("/explorer")
def get_explorer(request: Request) -> dict:
    payload = _latest_snapshot_payload(request)
    return {
        "scenario": payload.get("scenario"),
        "latest_snapshot": payload["latest_snapshot"],
        "current_run": payload["current_run"],
    }


@router.post("/resume-latest")
def resume_latest(request: Request, background_tasks: BackgroundTasks) -> dict:
    latest = latest_snapshot()

    if latest is None:
        raise HTTPException(status_code=404, detail="No snapshots found")

    background_tasks.add_task(_resume_task, get_simulation_service(request), str(latest))
    log.info("queued resume from %s", latest)

    return {"queued": True, "resume": str(latest)}


@router.post("/run")
def run_simulation(
    request: Request,
    background_tasks: BackgroundTasks,
    steps: int = 15,
    scenario: str = "default",
) -> dict:
    service = get_simulation_service(request)
    background_tasks.add_task(service.run, steps=steps, scenario=scenario)

    return {
        "queued": True,
        "steps": steps,
        "scenario": scenario,
    }


# ── Replay dashboard endpoint ──────────────────────────────────────────────
@router.get("/replay")
async def admin_replay(request: Request) -> dict:
    """Event replay dashboard data."""
    from swarm_os.api.dependencies import _safe_events
    runtime = getattr(request.app.state, "runtime", None)
    events = []
    healing_attempts = 0
    latest_health_score = None
    last_action = None
    if runtime:
        try:
            events = await _safe_events(runtime)
        except Exception:
            pass
        try:
            healing = getattr(runtime, "healing", None)
            if healing:
                detector = getattr(healing, "detector", None)
                if detector:
                    # BUG FIX: FailureDetector has no .status() — use check_sync() instead
                    report = detector.check_sync() if hasattr(detector, "check_sync") else (detector.status() if hasattr(detector, "status") else {})
                    latest_health_score = report.get("health_score", report.get("recovery_readiness"))
                    healing_attempts = report.get("healing_attempts", 0)
                    last_action = report.get("last_action")
        except Exception:
            pass
    return {
        "event_count": len(events),
        "healing_attempts": healing_attempts,
        "latest_health_score": latest_health_score,
        "last_action": last_action,
        "components": {},
    }


# ── Healing endpoints ───────────────────────────────────────────────────────
@router.post("/healing/run")
async def run_heal_cycle(request: Request) -> dict:
    """Trigger a full self-heal cycle."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised")
    try:
        healing = getattr(runtime, "healing", None)
        if healing is None:
            return {"recovery_readiness": 0, "active_anomalies": 0, "last_heal_success": False,
                    "checks": {}, "error": "healing service not available"}
        result = await healing.run_once()
        checks = result.get("checks", {"orchestrator": {"ok": True}, "qdrant": {"ok": True}, "llamacpp": {"ok": True}, "api": {"ok": True}})
        return {
            "recovery_readiness": result.get("recovery_readiness", result.get("health_score", 100)),
            "active_anomalies": result.get("active_anomalies", 0),
            "last_heal_success": result.get("success", True),
            "checks": checks,
        }
    except Exception:
        log.exception("Heal cycle evaluation failed")
        return {"recovery_readiness": 0, "active_anomalies": 1, "last_heal_success": False,
                "checks": {}, "error": "heal cycle evaluation failed"}


@router.get("/changes")
async def workspace_changes(max_diff_chars: int = 6000) -> dict:
    """Live diff review of uncommitted workspace changes (drives the web console panel).

    Returns {stat: [{path, added, deleted}], diff: <unified text, capped>}. No
    user-controlled input reaches the subprocess (fixed args only), so it is
    safe to expose; bounded timeout; degrades to an empty payload outside a
    git work tree.
    """
    import asyncio
    root = Path(__file__).resolve().parent.parent.parent
    try:
        stat_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD", "--stat",
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(10.0):
                stat_out, stat_err = await stat_proc.communicate()
        except TimeoutError:
            stat_proc.kill()
            await stat_proc.wait()
            return {"stat": [], "diff": "", "truncated": False, "is_git": False, "error": "git stat timeout"}
            
        if stat_proc.returncode != 0:
            err_text = stat_err.decode("utf-8", errors="replace").strip() if stat_err else "not a git work tree"
            return {"stat": [], "diff": "", "truncated": False, "is_git": False,
                    "error": err_text}
                    
        stat_lines = []
        for line in (stat_out.decode("utf-8", errors="replace") or "").splitlines():
            m = re.match(r"\s*(.+?)\s*\|\s*(\d+)\s+[+-]+", line)
            if m:
                stat_lines.append({"path": m.group(1).strip(), "lines": int(m.group(2))})
            elif line.strip():
                continue
                
        detail_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD",
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(10.0):
                detail_out, _ = await detail_proc.communicate()
        except TimeoutError:
            detail_proc.kill()
            await detail_proc.wait()
            return {"stat": stat_lines, "diff": "", "truncated": False, "is_git": True, "error": "git diff timeout"}
            
        diff_text = detail_out.decode("utf-8", errors="replace") or ""
        truncated = len(diff_text) > max_diff_chars
        return {
            "stat": stat_lines,
            "diff": diff_text[:max_diff_chars],
            "truncated": truncated,
            "is_git": True,
        }
    except Exception as exc:
        log.warning("workspace changes unavailable: %s", exc)
        return {"stat": [], "diff": "", "truncated": False, "is_git": False, "error": str(exc)}


@router.get("/healing/evaluate")
async def evaluate_health(request: Request) -> dict:
    """Check current health status without running a heal cycle."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised")
    try:
        healing = getattr(runtime, "healing", None)
        checks = {"orchestrator": {"ok": True}, "qdrant": {"ok": True}, "llamacpp": {"ok": True}, "api": {"ok": True}}
        readiness = 100
        anomalies = 0
        if healing:
            detector = getattr(healing, "detector", None)
            if detector:
                if hasattr(detector, "status"):
                    report = detector.status()
                elif hasattr(detector, "check"):
                    report = await detector.check()
                else:
                    report = {}
                readiness = report.get("recovery_readiness", report.get("health_score", 100))
                anomalies = report.get("active_anomalies", 0)
        return {
            "recovery_readiness": readiness,
            "active_anomalies": anomalies,
            "last_heal_success": anomalies == 0,
            "checks": checks,
        }
    except Exception:
        log.exception("Health evaluation failed")
        return {"recovery_readiness": 0, "active_anomalies": 1, "last_heal_success": False,
                "checks": {}, "error": "health evaluation failed"}








