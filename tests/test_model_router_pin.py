"""Pin-gate behavioural tests for model_router.py (Checkpoint 1).

SWARM_ROUTER_PINNED=1 makes the router forward-only: it must never spawn a
local model (start_daily_models) or kill processes -- on startup, on shutdown,
or on a mode-transition request. Default (unset/0) preserves old behaviour.

These are behavioural, not flag-reader tests: they drive the real lifespan and
switch_mode_if_needed with the spawn/kill seams replaced by counters.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_router():
    spec = importlib.util.spec_from_file_location("model_router", _ROOT / "model_router.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def router(monkeypatch):
    mod = _load_router()
    calls = {"start": 0, "kill": 0, "heavy": 0}

    async def _start():
        calls["start"] += 1

    async def _kill():
        calls["kill"] += 1

    async def _heavy():
        calls["heavy"] += 1

    monkeypatch.setattr(mod, "start_daily_models", _start)
    monkeypatch.setattr(mod, "kill_active_processes", _kill)
    monkeypatch.setattr(mod, "start_heavy_model", _heavy)
    return mod, calls


@pytest.mark.asyncio
async def test_pinned_lifespan_startup_skips_start_daily_models(router, monkeypatch):
    mod, calls = router
    monkeypatch.setenv("SWARM_ROUTER_PINNED", "1")
    async with mod.lifespan(mod.app):
        pass
    assert calls["start"] == 0, "pinned router must not start daily models"


@pytest.mark.asyncio
async def test_pinned_lifespan_shutdown_skips_kill_active_processes(router, monkeypatch):
    mod, calls = router
    monkeypatch.setenv("SWARM_ROUTER_PINNED", "1")
    async with mod.lifespan(mod.app):
        pass
    assert calls["kill"] == 0, "pinned router must not kill processes on shutdown"


@pytest.mark.asyncio
async def test_pinned_14b_request_does_not_spawn(router, monkeypatch):
    mod, calls = router
    monkeypatch.setenv("SWARM_ROUTER_PINNED", "1")
    # mode_switch_lock is only set in lifespan; pinned path must return before it.
    await mod.switch_mode_if_needed("some-14b-model")
    assert calls["heavy"] == 0
    assert calls["kill"] == 0
    assert calls["start"] == 0


@pytest.mark.asyncio
async def test_unpinned_behaviour_unchanged(router, monkeypatch):
    mod, calls = router
    monkeypatch.setenv("SWARM_ROUTER_PINNED", "0")
    async with mod.lifespan(mod.app):
        pass
    assert calls["start"] == 1, "unpinned startup still starts daily models"
    assert calls["kill"] == 1, "unpinned shutdown still kills active processes"
    # A 14b request on an unpinned router still takes the heavy transition path.
    await mod.switch_mode_if_needed("some-14b-model")
    assert calls["heavy"] == 1


@pytest.mark.asyncio
async def test_pinned_upstream_down_returns_502_and_spawns_nothing(router, monkeypatch):
    """The tunnel-drop scenario: pinned router, upstream unreachable -> 502,
    and neither start_daily_models nor kill_active_processes is called."""
    mod, calls = router
    monkeypatch.setenv("SWARM_ROUTER_PINNED", "1")

    class _DeadClient:
        def build_request(self, *a, **k):
            return object()

        async def send(self, *a, **k):
            raise RuntimeError("upstream down")

    mod.client = _DeadClient()

    class _Req:
        headers = {}

        async def body(self):
            return b'{"model":"robs4b","messages":[]}'

    resp = await mod.proxy_chat(_Req())
    assert resp.status_code == 502, "pinned router must 502 when upstream is down"
    assert calls["start"] == 0
    assert calls["kill"] == 0
