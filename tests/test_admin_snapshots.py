from fastapi.testclient import TestClient

from swarm_os.main import app

client = TestClient(app)

def test_admin_snapshots():
    response = client.get("/admin/snapshots")
    assert response.status_code == 200
    data = response.json()
    assert "snapshots" in data
    assert isinstance(data["snapshots"], list)
