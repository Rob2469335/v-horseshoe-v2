"""Tests for cross-agent shared reflexion rules (Task 3).

Only confident (>= 0.7) rules whose failure reason matches an explicit generic
allowlist may be stored with scope='shared'. Retrieval of shared rules is
env-gated (SWARM_SHARED_REFLEXION=1, off by default) so existing behavior is
preserved exactly when the variable is unset.
"""
from __future__ import annotations
import asyncio
import time
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from swarm_os.services.reflection_loop import (
    ReflectionService,
    _auto_scope,
    SHARED_SCOPE_MIN_CONFIDENCE,
)


def _make_service() -> tuple[ReflectionService, MagicMock]:
    service = ReflectionService.__new__(ReflectionService)
    service.collection = "ReflexionMemory"
    service._init_task = None
    service._ensured = True
    service.embedder = MagicMock()
    service.embedder.embed = AsyncMock(return_value=[0.1] * 768)
    service.client = MagicMock()
    service.client.query_points = AsyncMock()
    service.client.upsert = AsyncMock()
    return service, service.client


def _point(pid: str, payload: dict, score: float):
    payload = {**payload, "timestamp": payload.get("timestamp", time.time())}
    return SimpleNamespace(id=pid, payload=payload, score=score)


def test_auto_scope_requires_confidence_floor():
    assert _auto_scope(SHARED_SCOPE_MIN_CONFIDENCE - 0.01, "File not found: x.py") == "agent"
    assert _auto_scope(0.3, "timeout") == "agent"


def test_auto_scope_requires_generic_failure_marker():
    assert _auto_scope(0.9, "File not found: x.py") == "shared"
    assert _auto_scope(0.9, "tool decision timed out") == "shared"
    assert _auto_scope(0.9, "malformed JSON from tool decision") == "shared"
    assert _auto_scope(0.9, "agent-specific hallucination about user intent") == "agent"


@pytest.mark.asyncio
async def test_store_auto_assigns_scope():
    service, client = _make_service()
    await service.store_reflexion(
        "agent:code_analyzer audit", "read", "File not found: x.py",
        "list the parent dir first", component="code_analyzer", confidence=0.75,
    )
    kwargs = client.upsert.await_args.kwargs
    payload = kwargs["points"][0].payload
    assert payload["scope"] == "shared"

    await service.store_reflexion(
        "agent:code_analyzer audit", "read", "User is asking for a vague summary",
        "ask for clarification", component="code_analyzer", confidence=0.9,
    )
    kwargs = client.upsert.await_args.kwargs
    payload = kwargs["points"][0].payload
    assert payload["scope"] == "agent"


@pytest.mark.asyncio
async def test_store_respects_explicit_scope():
    service, client = _make_service()
    await service.store_reflexion(
        "agent:a task", "x", "some agent-specific failure", "advice",
        component="a", confidence=0.9, scope="shared",
    )
    kwargs = client.upsert.await_args.kwargs
    assert kwargs["points"][0].payload["scope"] == "shared"


@pytest.mark.asyncio
async def test_shared_retrieval_merged_when_env_enabled(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_REFLEXION", "1")
    service, client = _make_service()
    own = _point("own-1", {"correction": "own rule", "confidence": 1.0}, 0.5)
    shared = _point("shared-1", {
        "correction": "list parent dir before reading (learned by debugger)",
        "do_not_repeat": "never guess paths", "confidence": 1.0,
    }, 0.9)
    client.query_points.side_effect = [
        SimpleNamespace(points=[own]),
        SimpleNamespace(points=[shared]),
    ]
    hint = await service.check_for_past_mistakes("agent:coder fix the analyzer")
    assert client.query_points.await_count == 2
    filter_kwargs = client.query_points.await_args.kwargs
    assert filter_kwargs["query_filter"] is not None
    assert "debugger" in hint
    assert "own rule" not in hint


@pytest.mark.asyncio
async def test_shared_retrieval_not_enabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("SWARM_SHARED_REFLEXION", raising=False)
    service, client = _make_service()
    own = _point("own-1", {"correction": "own rule only", "confidence": 1.0}, 0.9)
    client.query_points.side_effect = [SimpleNamespace(points=[own])]
    hint = await service.check_for_past_mistakes("agent:coder fix the analyzer")
    assert client.query_points.await_count == 1
    assert client.query_points.await_args.kwargs.get("query_filter") is None
    assert "own rule only" in hint
    assert "debugger" not in hint


@pytest.mark.asyncio
async def test_shared_retrieval_dedupes_overlap(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_REFLEXION", "1")
    service, client = _make_service()
    dup = _point("dup-1", {"correction": "same rule", "confidence": 1.0}, 0.9)
    client.query_points.side_effect = [
        SimpleNamespace(points=[dup]),
        SimpleNamespace(points=[dup]),
    ]
    hint = await service.check_for_past_mistakes("agent:b some task")
    assert client.query_points.await_count == 2
    assert "same rule" in hint


@pytest.mark.asyncio
async def test_shared_retrieval_respects_max_chars_cap(monkeypatch):
    monkeypatch.setenv("SWARM_SHARED_REFLEXION", "1")
    service, client = _make_service()
    long_correction = "long " * 500
    shared = _point("shared-1", {"correction": long_correction, "confidence": 1.0}, 0.9)
    client.query_points.side_effect = [SimpleNamespace(points=[_point("own", {"correction": "x", "confidence": 1.0}, 0.1)]), SimpleNamespace(points=[shared])]
    hint = await service.check_for_past_mistakes("agent:b some task", max_chars=700)
    assert len(hint) <= 700
