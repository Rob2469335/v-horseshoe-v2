# swarm_os/api/admin.py

from __future__ import annotations

import logging
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from infrastructure.cache.cache_provider import get_cache_provider
from infrastructure.runtime.background_jobs import BackgroundJobRunner

from swarm_os.app.main import RuntimeGraph
from swarm_os.kernel.status import build_status
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.simulation_service import SimulationService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _ensure_runtime(request: Request) -> RuntimeGraph:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        snapshot_repo = FileSnapshotRepository()
        simulation_service = SimulationService(snapshot_repo=snapshot_repo)
        runtime = RuntimeGraph(
            orchestrator=Orchestrator(),
            cache=get_cache_provider(),
            runner=BackgroundJobRunner(),
            snapshot_repo=snapshot_repo,
            simulation_service=simulation_service,
        )
        request.app.state.runtime = runtime
        request.app.state.orchestrator = runtime.orchestrator
    return runtime


def get_runtime(request: Request) -> RuntimeGraph:
    return _ensure_runtime(request)


def get_snapshot_repo(request: Request) -> FileSnapshotRepository:
    return get_runtime(request).snapshot_repo


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
