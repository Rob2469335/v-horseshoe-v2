"""Tests for the /features/search vector-search wiring (P4).

The endpoint previously returned 503 "Vector search not yet configured" because
`swarm_os/lib/vector/reranker.py` was an EMPTY stub — `from ..lib.vector.reranker
import rerank` raised ImportError. This verifies the modules are importable and
degrade gracefully (never raise) when the embedding/rerank servers are offline.
"""

from __future__ import annotations
import pytest


def test_features_search_imports_resolve():
    """The exact import chain the endpoint uses must not raise ImportError."""
    from swarm_os.lib.vector.qdrant_store import search
    from swarm_os.lib.vector.reranker import rerank
    from swarm_os.core.settings import get_settings

    assert callable(search)
    assert callable(rerank)
    assert callable(get_settings)


@pytest.mark.asyncio
async def test_rerank_degrades_gracefully_when_offline():
    """Rerank must not raise when the :8082 reranker is unreachable — it returns
    the original candidates unchanged."""
    from swarm_os.lib.vector.reranker import rerank

    candidates = [
        {"id": "a", "score": 0.5, "payload": {"fact": "python is a language"}},
        {"id": "b", "score": 0.4, "payload": {"fact": "qdrant stores vectors"}},
    ]
    # Patch the semaphore path: rerank uses _get_client().post; simulate failure.
    import swarm_os.lib.vector.reranker as rr
    import httpx

    async def _fail(*a, **k):
        raise httpx.ConnectError("connection refused")

    orig = rr._get_client
    rr._get_client = lambda: type("C", (), {"post": _fail})()
    try:
        result = await rerank("python", candidates, top_k=2)
    finally:
        rr._get_client = orig
    assert len(result) == 2
    assert result[0]["id"] == "a"


@pytest.mark.asyncio
async def test_rerank_empty_candidates_returns_empty():
    from swarm_os.lib.vector.reranker import rerank

    assert await rerank("q", [], top_k=5) == []


def test_candidate_text_extracts_fact_content_and_falls_back():
    from swarm_os.lib.vector.reranker import _candidate_text

    assert _candidate_text({"payload": {"fact": "hello"}}) == "hello"
    assert _candidate_text({"payload": {"content": "world"}}) == "world"
    assert _candidate_text({"payload": {}}) == str({"payload": {}})


@pytest.mark.asyncio
async def test_rerank_semaphore_suspends_never_blocks_event_loop():
    """A saturated rerank burst must not block the event loop. The pre-fix
    semaphore was a threading.BoundedSemaphore acquired with a SYNC `with` —
    when both slots were held, a third concurrent rerank's acquire blocked the
    whole loop until a slot freed. asyncio.BoundedSemaphore + `async with`
    suspends the waiting task instead, so the loop keeps serving other work."""
    import asyncio

    import swarm_os.lib.vector.reranker as rr

    # Type pin: the semaphore must be asyncio-native so `async with` suspends.
    assert isinstance(rr._RERANK_SEM, asyncio.BoundedSemaphore)

    gate = asyncio.Event()
    release = asyncio.Event()

    async def _post(*a, **k):
        gate.set()
        await release.wait()
        return type("R", (), {"status_code": 200, "json": lambda: {"results": []}})()

    orig_client = rr._get_client
    rr._get_client = lambda: type("C", (), {"post": _post})()
    candidates = [
        {"id": str(i), "score": 0.5, "payload": {"fact": "x"}} for i in range(5)
    ]
    try:
        t1 = asyncio.create_task(rr.rerank("q", candidates, top_k=2))
        t2 = asyncio.create_task(rr.rerank("q", candidates, top_k=2))
        await asyncio.wait_for(gate.wait(), timeout=5.0)  # both slots held

        ticks = {"n": 0}

        async def _tick():
            while True:
                ticks["n"] += 1
                await asyncio.sleep(0.01)

        hb = asyncio.create_task(_tick())
        await asyncio.sleep(0.05)

        t3 = asyncio.create_task(rr.rerank("q", candidates, top_k=2))
        await asyncio.sleep(0.05)  # let t3 hit the semaphore acquire
        # The loop must keep ticking. A sync `with` acquire on a saturated
        # threading semaphore would have frozen it until a slot released.
        assert ticks["n"] >= 3

        release.set()
        await asyncio.wait_for(asyncio.gather(t1, t2, t3), timeout=5.0)
        hb.cancel()
    finally:
        rr._get_client = orig_client
