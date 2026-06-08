from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from swarm_os.app.main import app


class StubOrchestrator:
    def get_recent_traces(self, limit: int = 50):
        data = [
            {
                "trace_id": "t1",
                "step_id": "generate",
                "phase": "router",
                "actor": "orchestrator",
                "action": "route_model",
                "status": "selected",
                "timestamp_ms": 1.0,
                "duration_ms": 0.0,
                "model": "qwen2.5:7b-instruct",
                "tokens": 0,
                "cost": 0.0,
                "summary": "selected route",
                "metadata": {"target_role": "fast"},
            },
            {
                "trace_id": "t1",
                "step_id": "generate",
                "phase": "generator",
                "actor": "orchestrator",
                "action": "generate",
                "status": "completed",
                "timestamp_ms": 2.0,
                "duration_ms": 12.5,
                "model": "qwen2.5:7b-instruct",
                "tokens": 0,
                "cost": 0.0,
                "summary": "Generation completed",
                "metadata": {"result_chars": 42, "target_role": "fast"},
            },
        ]
        return data[-limit:]


def test_traces_endpoint_returns_recent_trace_items():
    original = getattr(app.state, "orchestrator", None)
    app.state.orchestrator = StubOrchestrator()

    try:
        client = TestClient(app)
        response = client.get("/traces?limit=10")
    finally:
        if original is None:
            delattr(app.state, "orchestrator")
        else:
            app.state.orchestrator = original

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["traces"]) == 2
    assert payload["traces"][1]["phase"] == "generator"
    assert payload["traces"][1]["status"] == "completed"

