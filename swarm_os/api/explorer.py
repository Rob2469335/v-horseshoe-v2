# swarm_os/api/explorer.py

from __future__ import annotations

from fastapi import APIRouter, Request

from swarm_os.kernel.status import build_status
from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository

router = APIRouter(prefix="/admin", tags=["admin"])


def get_snapshot_repo(request: Request) -> FileSnapshotRepository:
    return request.app.state.runtime.snapshot_repo


@router.get("/generation")
def get_generation(request: Request) -> dict:
    repo = get_snapshot_repo(request)
    latest = repo.latest()

    return {
        **build_status(None, None),
        "latest_snapshot": str(latest) if latest else None,
        "current_run": None,
        "population": [],
    }
