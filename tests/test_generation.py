from fastapi.testclient import TestClient

from swarm_os.main import app

client = TestClient(app)

def test_generation():
    response = client.get("/admin/generation")
    assert response.status_code == 200
    data = response.json()
    assert "scenario" in data
    assert "latest_snapshot" in data
    assert "current_run" in data
    assert "population" in data
    assert isinstance(data["population"], list)
