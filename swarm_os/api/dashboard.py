# swarm_os/api/dashboard.py
"""
Dashboard endpoint.
Fixed: imports from kernel.snapshot and kernel.status not top-level.
"""
from __future__ import annotations

from fastapi import APIRouter

from swarm_os.kernel.snapshot_index import latest_snapshot
from swarm_os.kernel.status import build_status
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository

router = APIRouter(prefix="/admin", tags=["admin"])
_repo  = FileSnapshotRepository()


@router.get("/dashboard")
def get_dashboard() -> dict:
    latest = _repo.latest()
    status = build_status(None, None)
    return {
        **status,
        "snapshot_count":   len(_repo.list()),
        "latest_snapshot":  str(latest) if latest else None,
    }

