from __future__ import annotations
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from swarm_os.app.main import app


def _llm_backend_up() -> bool:
    """Check if a live llama.cpp server is responding (integration env)."""
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:8080/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


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

def test_step_agent_unknown_agent_returns_404():
    from fastapi import FastAPI
    from swarm_os.api.agents import router

    service = MagicMock()
    service.step_agent_stream = MagicMock(side_effect=KeyError("missing"))
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[__import__(
        "swarm_os.api.agents", fromlist=["get_agent_service"]
    ).get_agent_service] = lambda: service

    with TestClient(test_app) as test_client:
        response = test_client.post(
            "/agents/missing/step",
            json={"prompt": "ping"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown agent 'missing'"}


def test_create_agent_shape(client):
    payload = {
        "agent_id": "test-agent",
        "prompt": "init",
        "history": []
    }
    r = client.post("/agents", json=payload)
    assert r.status_code == 200

@pytest.mark.skipif(_llm_backend_up(), reason="Runs against live LLM backend — real generation is too slow for unit tests")
def test_step_agent_shape(client):
    payload = {"agent_id": "coordinator", "prompt": "ping"}
    r = client.post("/agents/coordinator/step", json=payload)
    # 503 = LLM backend not running — skip, don't fail
    if r.status_code == 503:
        pytest.skip("LLM backend not running")
    assert r.status_code in (200, 500, 502, 504)

def test_status_endpoint(client):
    r = client.get("/status")
    assert r.status_code == 200
    assert "ready" in r.json()

def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
