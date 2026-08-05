"""Tests for the /features/search vector-search wiring (P4).

The endpoint previously returned 503 "Vector search not yet configured" because
`swarm_os/lib/vector/reranker.py` was an EMPTY stub — `from ..lib.vector.reranker
import rerank` raised ImportError. This verifies the modules are importable and
degrade gracefully (never raise) when the embedding/rerank servers are offline.
"""
from __future__ import annotations
import asyncio
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
