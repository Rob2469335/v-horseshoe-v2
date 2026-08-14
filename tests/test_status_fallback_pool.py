"""Tests that `/status` reports the LIVE cloud fallback chain in `fallback_pool`,
so the console banner's FALLBACKS row shows real readiness counts instead of
"Checking status..." forever."""
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from swarm_os.api import routes
from swarm_os.api.dependencies import runtime_dep


def _app():
    app = FastAPI()
    fake_runtime = MagicMock()
    fake_runtime.agent_runtime = None
    fake_runtime.cache = None
    app.state.runtime = fake_runtime
    app.dependency_overrides[runtime_dep] = lambda: fake_runtime
    app.include_router(routes.router)
    return app


def test_status_reports_fallback_pool(monkeypatch):
    """The live fallback chain must be bucketed into the fallback_pool dict the
    banner reads (total + per-provider counts)."""
    async def _fake_chain(mode="auto"):
        return [
            {"model": "deepseek/deepseek-v4-flash"},
            {"model": "openai/deepseek-v4-flash"},  # opencode go
            {"model": "openai/zen/deepseek-v4-flash"},
            {"model": "nvidia_nim/deepseek-ai/deepseek-v4-flash"},
            {"model": "openrouter/deepseek/deepseek-chat"},
            {"model": "qwen3.5-4b"},
        ]

    monkeypatch.setattr(
        "runtime_v2.services.fallback_manager.get_live_fallbacks", _fake_chain
    )
    app = _app()
    with TestClient(app) as c:
        resp = c.get("/status")
    assert resp.status_code == 200
    fp = resp.json()["fallback_pool"]
    assert fp["total"] == 6
    assert fp["deepseek"] == 1
    assert fp["opencode"] == 2
    assert fp["nvidia"] == 1
    assert fp["openrouter"] == 1
    assert fp["local"] == 1
    assert fp["groq"] == 0


def test_status_fallback_pool_empty_on_chain_failure(monkeypatch):
    """If the fallback chain is unavailable, /status must still succeed with an
    empty fallback_pool (the banner falls back to 'Checking status...') — never
    a 500."""
    async def _boom(_mode="auto"):
        raise RuntimeError("chain down")

    monkeypatch.setattr(
        "runtime_v2.services.fallback_manager.get_live_fallbacks", _boom
    )
    app = _app()
    with TestClient(app) as c:
        resp = c.get("/status")
    assert resp.status_code == 200
    assert resp.json()["fallback_pool"] == {}
