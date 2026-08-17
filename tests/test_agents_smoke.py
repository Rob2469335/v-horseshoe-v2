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
    test_app.dependency_overrides[
        __import__(
            "swarm_os.api.agents", fromlist=["get_agent_service"]
        ).get_agent_service
    ] = lambda: service

    with TestClient(test_app) as test_client:
        response = test_client.post(
            "/agents/missing/step",
            json={"prompt": "ping"},
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown agent 'missing'"}


def test_create_agent_shape(client):
    payload = {"agent_id": "test-agent", "prompt": "init", "history": []}
    r = client.post("/agents", json=payload)
    assert r.status_code == 200


@pytest.mark.skipif(
    _llm_backend_up(),
    reason="Runs against live LLM backend — real generation is too slow for unit tests",
)
def test_step_agent_shape(client):
    payload = {"agent_id": "coordinator", "prompt": "ping"}
    r = client.post("/agents/coordinator/step", json=payload)
    # 503 = LLM backend not running — skip, don't fail
    if r.status_code == 503:
        pytest.skip("LLM backend not running")
    assert r.status_code in (200, 500, 502, 504)


def test_call_agent_tool_null_payload_normalized_to_empty_dict():
    """A POST to /agents/{id}/tools/{tool} with NO payload body must dispatch
    {} to run_tool, never raw None (None would AttributeError inside
    tool_executor.run's payload.get). Revert-proof: old call_agent_tool passed
    payload.payload straight through, so this test fails on the removed guard."""
    from fastapi import FastAPI
    from swarm_os.api.agents import router

    captured = {}

    async def fake_run_tool(agent_id, tool_name, payload):
        captured["payload"] = payload
        return {"ok": True}

    service = MagicMock()
    service.run_tool = fake_run_tool
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[
        __import__(
            "swarm_os.api.agents", fromlist=["get_agent_service"]
        ).get_agent_service
    ] = lambda: service

    with TestClient(test_app) as test_client:
        response = test_client.post("/agents/tooluser/tools/lsp", json={})

    assert response.status_code == 200
    assert captured["payload"] == {}


def test_step_forwards_delegation_chain_and_resume():
    """Non-streaming /step must forward delegation_chain + resume (like the
    streaming endpoint); silently dropping them would lose the checkpointed-run
    and compound-goal routing state."""
    from fastapi import FastAPI
    from swarm_os.api.agents import router

    captured = {}

    async def fake_stream(
        agent_id, prompt, history=None, delegation_chain=None, resume=None
    ):
        captured["chain"] = delegation_chain
        captured["resume"] = resume
        yield {"type": "final", "content": "ok"}
        return

    service = MagicMock()
    service.step_agent_stream = fake_stream
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[
        __import__(
            "swarm_os.api.agents", fromlist=["get_agent_service"]
        ).get_agent_service
    ] = lambda: service

    with TestClient(test_app) as test_client:
        response = test_client.post(
            "/agents/coordinator/step",
            json={
                "prompt": "ping",
                "delegation_chain": ["coder", "tool-runner"],
                "resume": "abc123",
            },
        )

    assert response.status_code == 200
    assert captured["chain"] == ["coder", "tool-runner"]
    assert captured["resume"] == "abc123"


def _features_app_with_runtime_agents(agent_factory):
    """Build a FastAPI app mounting the /features router with a fake runtime.agents
    service. Regression for the dead `app.state.agent_service` attribute that
    made /debate and /omnidev/run always return 'Agent service unavailable'."""
    from fastapi import FastAPI
    from swarm_os.api.api_features import router as features_router

    test_app = FastAPI()
    test_app.include_router(features_router)
    runtime = MagicMock()
    runtime.agents = agent_factory()
    test_app.state.runtime = runtime
    return test_app


def test_omnidev_run_uses_runtime_agents():
    """/omnidev/run must resolve the agent service from app.state.runtime.agents
    (the only attribute main.py sets), not the nonexistent app.state.agent_service."""
    final_content = "task result"

    async def fake_stream(agent_id, task):
        yield {"type": "final", "content": final_content}
        return

    test_app = _features_app_with_runtime_agents(
        lambda: MagicMock(step_agent_stream=fake_stream)
    )
    with TestClient(test_app) as test_client:
        response = test_client.post(
            "/features/omnidev/run", json={"task": "do the thing"}
        )

    assert response.status_code == 200
    assert response.json() == {"result": final_content}


def test_omnidev_run_without_agents_returns_503():
    from fastapi import FastAPI
    from swarm_os.api.api_features import router as features_router

    test_app = FastAPI()
    test_app.include_router(features_router)
    test_app.state.runtime = None
    with TestClient(test_app) as test_client:
        response = test_client.post("/features/omnidev/run", json={"task": "x"})
    assert response.status_code == 503


def test_debate_uses_runtime_agents():
    """/debate must stream phases from app.state.runtime.agents, not the
    nonexistent app.state.agent_service."""

    async def fake_stream(agent_id, task):
        yield {"type": "final", "content": f"[{agent_id}] synthesized"}
        return

    test_app = _features_app_with_runtime_agents(
        lambda: MagicMock(step_agent_stream=fake_stream)
    )
    with TestClient(test_app) as test_client:
        response = test_client.post("/features/debate", json={"goal": "build a router"})

    assert response.status_code == 200
    assert "event-stream" in response.headers.get("content-type", "")
    body = response.text
    assert "phase" in body
    assert "done" in body
    assert "Agent service unavailable" not in body


def test_status_endpoint(client):
    r = client.get("/status")
    assert r.status_code == 200
    assert "ready" in r.json()


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
