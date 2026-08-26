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
    from swarm_os.repositories.event_log_repo import EventLogRepository

    events_path = Path("data/events/events.jsonl")
    events_path.parent.mkdir(parents=True, exist_ok=True)
    wrote = False
    if not events_path.exists() or events_path.stat().st_size == 0:
        with events_path.open("w", encoding="utf-8") as f:
            f.write(
                json.dumps({"occurred_at": "2026-08-10T00:00:00+00:00", "status": "ok"})
                + "\n"
            )
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


def test_safe_ollama_models_reuses_pooled_probe_client(monkeypatch):
    """The per-port model probes must share ONE pooled AsyncClient — a fresh
    client per port per /status poll wasted a TCP/TLS handshake every call
    (8 clients across two polls pre-fix) and its 1.0s timeout produced
    transient 'Failed checking port' warnings during busy startup windows.
    Revert-proof: on pre-fix source this test fails with 8 instantiations."""
    import asyncio

    import httpx as httpx_mod

    import swarm_os.api.routes as routes_mod

    created: list[object] = []

    class FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "qwen3.5-4b"}]}

    class FakeClient:
        def __init__(self, *, timeout=None, limits=None):
            created.append(self)
            self.seen_timeout = timeout

        @property
        def is_closed(self):
            return False

        async def get(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(routes_mod.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(routes_mod, "_PROBE_CLIENT", None)

    m1 = asyncio.run(routes_mod._safe_ollama_models(None))
    m2 = asyncio.run(routes_mod._safe_ollama_models(None))

    # 4 ports x 2 calls = 8 fresh clients pre-fix; exactly 1 post-fix
    assert len(created) == 1
    # probe budget widened from the old hard 1.0s so busy-startup false
    # positives stop warning
    assert isinstance(created[0].seen_timeout, httpx_mod.Timeout)
    assert created[0].seen_timeout.read == 3.0
    # behavior unchanged: model ids still normalized and reported
    assert "qwen3.5-4b" in m1 and m1 == m2


def test_probe_client_rebuilt_after_close():
    """If the pooled probe client was closed underneath us (lifespan teardown),
    the getter must self-heal with a fresh client instead of failing forever."""
    import asyncio

    import swarm_os.api.routes as routes_mod

    first = routes_mod._get_probe_client()
    second = routes_mod._get_probe_client()
    assert first is second
    asyncio.run(first.aclose())
    third = routes_mod._get_probe_client()
    assert third is not first
    asyncio.run(third.aclose())
