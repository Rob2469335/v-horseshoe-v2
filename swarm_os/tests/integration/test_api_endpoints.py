import pytest
from fastapi.testclient import TestClient
from swarm_os.app.main import create_app

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)

def test_list_tools(client):
    response = client.get("/tools")
    assert response.status_code == 200
    data = response.json()
    assert "capabilities" in data
    assert len(data["capabilities"]) > 0

def test_execute_chat_search(client):
    payload = {
        "capability": "chat_search",
        "payload": {"query": "test query"}
    }
    response = client.post("/tools/execute", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

def test_cache_status(client):
    response = client.get("/tools/cache")
    assert response.status_code == 200
    assert "cache_size" in response.json()
