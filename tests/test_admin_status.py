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


def test_status_primary_vision_model_none_when_no_vision_model(monkeypatch, client):
    """primary_vision_model must never mislabel a generation model as the vision
    model. When no model matches the vision markers, it must be null — falling
    back to installed_models[0] (e.g. qwen3.5-4b) reported a generation model as
    the vision path."""
    import swarm_os.api.routes as routes_mod

    async def _fake_safe_ollama_models(_runtime):
        return ["qwen3.5-4b"]

    monkeypatch.setattr(routes_mod, "_safe_ollama_models", _fake_safe_ollama_models)
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json()["primary_vision_model"] is None


def test_status_primary_vision_model_real_vision_model(monkeypatch, client):
    """A real vision model (moondream/vl) must be reported as primary."""
    import swarm_os.api.routes as routes_mod

    async def _fake_safe_ollama_models(_runtime):
        return ["qwen3.5-4b", "moondream-latest"]

    monkeypatch.setattr(routes_mod, "_safe_ollama_models", _fake_safe_ollama_models)
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json()["primary_vision_model"] == "moondream-latest"


