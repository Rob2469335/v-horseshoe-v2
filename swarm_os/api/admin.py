# swarm_os/api/admin.py
"""
Admin endpoints — snapshot management and simulation control.
Fixed: SimulationService now constructed with FileSnapshotRepository
Fixed: imports from kernel.snapshot not top-level snapshot_index
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from swarm_os.kernel.snapshot_index import latest_snapshot
from swarm_os.kernel.snapshot import save_snapshot
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
from swarm_os.services.simulation_service import SimulationService

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

_repo    = FileSnapshotRepository()
_service = SimulationService(snapshot_repo=_repo)


def _resume_task(path: str) -> None:
    try:
        _service.run(resume_path=path)
    except Exception as e:
        log.exception("resume task failed: %s", e)


@router.get("/snapshots")
def get_snapshots() -> dict:
    snaps = _repo.list()
    return {"count": len(snaps), "snapshots": [str(p) for p in snaps]}


@router.post("/resume-latest")
def resume_latest(background_tasks: BackgroundTasks) -> dict:
    latest = _repo.latest()
    if latest is None:
        raise HTTPException(status_code=404, detail="No snapshots found")
    background_tasks.add_task(_resume_task, str(latest))
    log.info("queued resume from %s", latest)
    return {"queued": True, "resume": str(latest)}


@router.post("/run")
def run_simulation(
    background_tasks: BackgroundTasks,
    steps: int = 15,
    scenario: str = "default",
) -> dict:
    background_tasks.add_task(_service.run, steps=steps, scenario=scenario)
    return {"queued": True, "steps": steps, "scenario": scenario}

