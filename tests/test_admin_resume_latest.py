from pathlib import Path

from fastapi.testclient import TestClient

from swarm_os.main import app


def test_admin_resume_latest_queued(monkeypatch):
    fake_snapshot = Path("swarm_os/data/snapshots/snapshot_0001.json")
    queued = {}

    monkeypatch.setattr(
        "swarm_os.api.admin.latest_snapshot",
        lambda: fake_snapshot,
    )

    async def fake_resume_task(service, path):
        queued["service"] = service
        queued["path"] = path

    monkeypatch.setattr(
        "swarm_os.api.admin._resume_task",
        fake_resume_task,
    )

    with TestClient(app) as client:
        runtime_service = app.state.runtime.simulation_service
        response = client.post("/api/admin/resume-latest")

    assert response.status_code == 200
    data = response.json()
    assert data["queued"] is True
    assert data["resume"] == str(fake_snapshot)
    assert queued["service"] is runtime_service
    assert queued["path"] == str(fake_snapshot)
