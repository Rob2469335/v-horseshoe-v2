# swarm_os/api/admin.py

from __future__ import annotations

import logging
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from swarm_os.healing.failure_detector import FailureDetector
from swarm_os.healing.healing_service import HealingService
from infrastructure.cache.cache_provider import get_cache_provider
from infrastructure.runtime.background_jobs import BackgroundJobRunner


from swarm_os.kernel.status import build_status
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.simulation_service import SimulationService
from swarm_os.services.health import backend_health
from swarm_os.app.main import RuntimeGraph




log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])






def get_runtime(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised")
    return runtime
def get_runtime(request: Request) -> RuntimeGraph:
    return _ensure_runtime(request)


def get_snapshot_repo(request: Request) -> FileSnapshotRepository:
    runtime = get_runtime(request)
    repo = getattr(runtime, "snapshot_repo", None)
    if repo is not None:
        return repo
    return FileSnapshotRepository(Path("swarm_os/data/snapshots"))


def get_simulation_service(request: Request) -> SimulationService:
    return get_runtime(request).simulation_service


def latest_snapshot() -> Path | None:
    return FileSnapshotRepository().latest()


async def _resume_task(path: str) -> None:
    try:
        service = SimulationService(snapshot_repo=FileSnapshotRepository())
        await service.run(resume_path=path)
    except Exception as e:
        log.exception("resume task failed: %s", e)


def _latest_snapshot_payload(request: Request) -> dict:
    repo = get_snapshot_repo(request)
    latest = repo.latest()
    snapshots = repo.list()

    base = build_status(None, None)
    return {
        **base,
        "latest_snapshot": str(latest) if latest else None,
        "snapshot_count": len(snapshots),
        "current_run": None,
        "queued": False,
        "running": False,
        "population": [],
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

    background_tasks.add_task(_resume_task, str(latest))
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
@router.get("/healing/status")
def healing_status(request: Request) -> dict:
    runtime = get_runtime(request)
    healing = getattr(runtime, "healing", None)

    backend = {
        "status": getattr(backend_health, "status", None),
        "health_score": getattr(backend_health, "health_score", None),
        "overall": getattr(backend_health, "overall", None),
    }

    return {
        "backend": backend,
        "healing": healing.status() if healing is not None else {"available": False},
    }












