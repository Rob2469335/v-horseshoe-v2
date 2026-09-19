"""Pin-gate + inference-attribution tests for model_router.py (Checkpoints 1-2).

SWARM_ROUTER_PINNED=1 makes the router forward-only: it must never spawn a
local model (start_daily_models) or kill processes. The inference-status
endpoint provides per-request (model, system_fingerprint) attribution and a
boot_id for restart detection.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json as _json
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


def _fresh_stats(mod):
    with mod._INFERENCE_LOCK:
        mod._INFERENCE_STATS["completion_requests"] = 0
        mod._INFERENCE_STATS["pairs"] = {}
        mod._INFERENCE_STATS["pairless"] = 0
        mod._INFERENCE_STATS["errors"] = 0
        mod._INFERENCE_STATS["aborted"] = 0
        mod._INFERENCE_STATS["timeouts"] = 0


def _body(resp):
    """Parse JSONResponse.body -> dict."""
    return _json.loads(resp.body)


# ---- Checkpoint 1: pin gate ----

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
    await mod.switch_mode_if_needed("some-14b-model")
    assert calls["heavy"] == 1


@pytest.mark.asyncio
async def test_pinned_upstream_down_returns_502_and_spawns_nothing(router, monkeypatch):
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
    assert resp.status_code == 502
    assert calls["start"] == 0
    assert calls["kill"] == 0


# ---- Checkpoint 2: inference attribution + credential gate ----

def test_wrong_fingerprint_does_not_match(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    mod._record_stream_start(b'data: {"model":"robs4b","system_fingerprint":"WRONG"}\n\n')
    r = asyncio.run(mod.inference_status("k"))
    pairs = _body(r)["pairs"]
    assert "robs4b|WRONG" in pairs
    assert "robs4b|b1-60081bb" not in pairs


def test_pairless_completed_request_recorded(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    mod._record_stream_start(b'data: {"model":"robs4b"}\n\n')
    body = _body(asyncio.run(mod.inference_status("k")))
    assert body["pairless"] == 1
    assert body["completion_requests"] == 1
    assert body["pairs"] == {}


def test_stream_ending_before_first_chunk(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    mod._record_stream_error()
    body = _body(asyncio.run(mod.inference_status("k")))
    assert body["errors"] == 1


def test_timeout_string_in_output_does_not_increment_timeout(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    mod._record_stream_start(
        b'data: {"model":"robs4b","system_fingerprint":"b1-60081bb",'
        b'"choices":[{"delta":{"content":"timeout after 300s"}}]}\n\n'
    )
    body = _body(asyncio.run(mod.inference_status("k")))
    assert body["timeouts"] == 0
    assert body["pairs"]["robs4b|b1-60081bb"] == 1


def test_abort_counter_increments(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    mod._record_abort()
    body = _body(asyncio.run(mod.inference_status("k")))
    assert body["aborted"] == 1


def test_boot_id_present_and_stable(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    r1 = _body(asyncio.run(mod.inference_status("k")))
    r2 = _body(asyncio.run(mod.inference_status("k")))
    assert r1["boot_id"]
    assert r1["boot_id"] == r2["boot_id"]


def test_pinned_false_when_unset(router, monkeypatch):
    mod, _ = router
    monkeypatch.delenv("SWARM_ROUTER_PINNED", raising=False)
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    body = _body(asyncio.run(mod.inference_status("k")))
    assert body["pinned"] is False


def test_non_streaming_pair_from_json_body(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    mod._record_stream_start(
        b'{"model":"robs4b","system_fingerprint":"b1-60081bb","choices":[]}'
    )
    body = _body(asyncio.run(mod.inference_status("k")))
    assert body["pairs"].get("robs4b|b1-60081bb") == 1
    assert body["pairless"] == 0


def test_completion_count_only_on_chat(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "k")
    _fresh_stats(mod)
    body = _body(asyncio.run(mod.inference_status("k")))
    assert body["completion_requests"] == 0


def test_unset_key_rejects_every_request(router, monkeypatch):
    mod, _ = router
    monkeypatch.delenv("SWARM_HARNESS_KEY", raising=False)
    _fresh_stats(mod)
    assert asyncio.run(mod.inference_status("")).status_code == 401
    assert asyncio.run(mod.inference_status("some-key")).status_code == 401


def test_wrong_key_rejected(router, monkeypatch):
    mod, _ = router
    monkeypatch.setenv("SWARM_HARNESS_KEY", "correct")
    _fresh_stats(mod)
    assert asyncio.run(mod.inference_status("wrong")).status_code == 401


def test_status_not_on_main_app(router, monkeypatch):
    mod, _ = router
    main_routes = [r.path for r in mod.app.routes]
    status_routes = [r.path for r in mod.status_app.routes]
    assert "/inference/status" in status_routes
    assert "/inference/status" not in main_routes
