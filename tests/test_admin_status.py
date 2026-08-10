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


def test_timeline_reads_bounded_events(monkeypatch, client):
    """STA-2: /timeline must call EventLogRepository.read_events with a hard
    max (500) so a huge events.jsonl is never materialized on every poll."""
    import json
    from pathlib import Path
    import swarm_os.api.routes as routes_mod
    from swarm_os.repositories.event_log_repo import EventLogRepository

    events_path = Path("data/events/events.jsonl")
    events_path.parent.mkdir(parents=True, exist_ok=True)
    wrote = False
    if not events_path.exists() or events_path.stat().st_size == 0:
        with events_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"occurred_at": "2026-08-10T00:00:00+00:00", "status": "ok"}) + "\n")
        wrote = True
    try:
        captured = {}

        def _fake_read_events(self, current_offset, max_events=0):
            captured["args"] = (current_offset, max_events)
            return [], 0

        monkeypatch.setattr(EventLogRepository, "read_events", _fake_read_events)
        r = client.get("/timeline")
        assert r.status_code == 200
        assert captured["args"] == (0, 500), (
            f"read_events must be called with a bounded max, got {captured['args']}"
        )
    finally:
        if wrote:
            events_path.unlink(missing_ok=True)


