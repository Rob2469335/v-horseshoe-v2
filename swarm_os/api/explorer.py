# swarm_os/api/explorer.py
"""
Explorer endpoint.
Fixed: imports from kernel.snapshot and kernel.status not top-level.
"""
from __future__ import annotations

from fastapi import APIRouter

from swarm_os.kernel.status import build_status
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository

router = APIRouter(prefix="/admin", tags=["admin"])
_repo  = FileSnapshotRepository()


@router.get("/generation")
def get_generation() -> dict:
    latest = _repo.latest()
    return {
        **build_status(None, None),
        "latest_snapshot": str(latest) if latest else None,
        "current_run":     None,
        "population":      [],
    }
