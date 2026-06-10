# swarm_os/api/dashboard.py

from __future__ import annotations

from fastapi import APIRouter, Request

from swarm_os.kernel.status import build_status
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository

router = APIRouter(prefix="/admin", tags=["admin"])


def get_snapshot_repo(request: Request) -> FileSnapshotRepository:
    return request.app.state.runtime.snapshot_repo


@router.get("/dashboard")
def get_dashboard(request: Request) -> dict:
    repo = get_snapshot_repo(request)
    latest = repo.latest()

    status = build_status(None, None)

    return {
        **status,
        "snapshot_count": len(repo.list()),
        "latest_snapshot": str(latest) if latest else None,
    }
