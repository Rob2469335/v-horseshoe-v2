"""Regression: /generate must degrade to the local llama.cpp model when the
default cloud model fails.

REAL OUTAGE (2026-08-26): /generate defaults to openai/deepseek-v4-flash via
OpenCode Go whenever OPENAI_API_KEY is set. When that account runs dry
('Insufficient balance'), litellm raised instantly and the route 502'd —
taking every prompt-only client down with it, even though the local qwen
server was healthy and idle. Unlike stream_runner's tool-decision loop, this
route had NO fallback. Pre-fix source: test fails with 502."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from swarm_os.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _orch(monkeypatch):
    """The route requires an orchestrator via DI; tests don't boot the runtime."""
    monkeypatch.setattr(app.state, "orchestrator", object(), raising=False)


@pytest.fixture()
def _cloud_down_local_up(monkeypatch):
    """litellm.acompletion raises for the cloud model, succeeds for local."""
    calls = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs.get("model"))
        if "deepseek" in str(kwargs.get("model")):
            raise RuntimeError("Insufficient balance")
        resp = type(
            "R",
            (),
            {
                "choices": [
                    type("C", (), {"message": type("M", (), {"content": "OK"})()})()
                ]
            },
        )()
        return resp

    import litellm

    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=fake_acompletion))
    # force the cloud-default branch deterministically
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return calls


def test_generate_falls_back_to_local_when_cloud_fails(_cloud_down_local_up):
    r = client.post("/generate", json={"prompt": "Reply OK"})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "OK"
    # first attempt was the cloud default; second was the local model
    models = _cloud_down_local_up
    assert len(models) == 2
    assert "deepseek" in models[0]
    assert "qwen3.5-4b" in models[1]


def test_generate_still_502_when_local_also_fails(monkeypatch):
    async def always_fail(**kwargs):
        raise RuntimeError("down")

    import litellm

    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=always_fail))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/generate", json={"prompt": "Reply OK"})
    assert r.status_code == 502
