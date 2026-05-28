from fastapi.testclient import TestClient

from swarm_os.main import app

client = TestClient(app)

def test_dashboard():
    response = client.get("/api/admin/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert "scenario" in data
    assert "generation" in data
    assert "snapshot_count" in data
    assert "snapshot_count" in data
    assert "latest_snapshot" in data
