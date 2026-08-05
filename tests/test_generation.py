import pytest
from fastapi.testclient import TestClient

from swarm_os.main import app

client = TestClient(app)

def test_generation():
    response = client.get("/api/admin/generation")
    # 503 means backend isn't running — accept it as offline
    if response.status_code == 503:
        pytest.skip("LLM backend not running")
    assert response.status_code == 200
    data = response.json()
    assert "scenario" in data
    assert "latest_snapshot" in data
    assert "current_run" in data
    assert "population" in data
    assert isinstance(data["population"], list)


