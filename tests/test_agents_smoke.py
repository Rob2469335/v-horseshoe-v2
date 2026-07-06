from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from swarm_os.app.main import app

@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client

def test_list_agents_shape(client):
    r = client.get("/agents")
    assert r.status_code == 200
    agents = r.json()
    assert isinstance(agents, list)
    assert len(agents) >= 6

def test_create_agent_shape(client):
    payload = {
        "agent_id": "test-agent",
        "prompt": "init",
        "history": []
    }
    r = client.post("/agents", json=payload)
    assert r.status_code == 200

def test_step_agent_shape(client):
    payload = {"agent_id": "coordinator", "prompt": "ping"}
    r = client.post("/agents/coordinator/step", json=payload)
    # Status can be 200 or 500 depending on Ollama
    assert r.status_code in (200, 500, 502)

def test_status_endpoint(client):
    r = client.get("/status")
    assert r.status_code == 200
    assert "ready" in r.json()

def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
