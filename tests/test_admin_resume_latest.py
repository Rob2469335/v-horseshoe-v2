from fastapi.testclient import TestClient
from pathlib import Path

from swarm_os.main import app

client = TestClient(app)

def test_admin_resume_latest_queued(monkeypatch):
    fake = Path("swarm_os/data/snapshots/snapshot_0001.json")
    monkeypatch.setattr("swarm_os.api.admin.latest_snapshot", lambda: fake)

    response = client.post("/admin/resume-latest")
    assert response.status_code == 200
    data = response.json()
    assert data["queued"] is True
    assert data["resume"] == str(fake)
