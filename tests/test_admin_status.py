from fastapi.testclient import TestClient

from swarm_os.main import app

client = TestClient(app)

def test_admin_status():
    response = client.get("/api/admin/status")
    assert response.status_code == 200
    data = response.json()
    assert "scenario" in data
    assert "generation" in data
    assert "snapshot_count" in data

