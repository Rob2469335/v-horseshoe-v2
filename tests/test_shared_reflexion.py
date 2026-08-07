"""Tests for cross-agent shared reflexion rules (Task 3).

Only confident (>= 0.7) rules whose failure reason matches an explicit generic
allowlist may be stored with scope='shared'. Retrieval of shared rules is
env-gated (SWARM_SHARED_REFLEXION=1, off by default) so existing behavior is
preserved exactly when the variable is unset.
"""
from __future__ import annotations
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
async def test_store_deterministic_id_overwrites_duplicate_failure():
    service, client = _make_service()
    await service.store_reflexion(
        "agent:code_analyzer audit", "read", "File not found: x.py",
        "list the parent dir first", component="code_analyzer", confidence=0.7,
    )
    first_id = client.upsert.await_args.kwargs["points"][0].id
    await service.store_reflexion(
        "agent:code_analyzer audit", "read", "File not found: x.py",
        "list the parent dir first", component="code_analyzer", confidence=0.7,
    )
    second_id = client.upsert.await_args.kwargs["points"][0].id
    assert first_id == second_id, "repeated identical failure must reuse the same point id"
    assert client.upsert.await_count == 2
    # different failure_reason -> different point id
    await service.store_reflexion(
        "agent:code_analyzer audit", "read", "File not found: y.py",
        "list the parent dir first", component="code_analyzer", confidence=0.7,
    )
    third_id = client.upsert.await_args.kwargs["points"][0].id
    assert third_id != first_id, "different failure must map to a distinct point id"


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

def test_corrections_similar_content_based():
    from swarm_os.services.reflection_loop import _corrections_similar
    assert _corrections_similar("list the parent dir first", "list the parent directory first")
    assert _corrections_similar("use filesystem read before patch", "use the filesystem read operation before patching a file")
    assert not _corrections_similar("list the parent dir first", "reset the model cooldowns")
    assert not _corrections_similar("call web_search for every goal", "never call web_search, use semantic_search instead")
    # Either side empty -> ambiguous -> treated as same (reinforce)
    assert _corrections_similar("advice", "")
    assert _corrections_similar("", "advice")


def test_classify_rule_same_vs_conflict():
    from swarm_os.services.reflection_loop import (_classify_rule, _RULE_SAME, _RULE_CONFLICT)
    assert _classify_rule(None, "anything") == _RULE_SAME          # first write
    assert _classify_rule({"correction": "list parent dir"}, "list the parent directory first") == _RULE_SAME
    assert _classify_rule({"correction": "list parent dir"}, "reset the model cooldowns") == _RULE_CONFLICT


def _make_service_with_retrieve(existing_payload):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from swarm_os.services.reflection_loop import ReflectionService
    service = ReflectionService.__new__(ReflectionService)
    service.collection = "ReflexionMemory"
    service._init_task = None
    service._ensured = True
    service.embedder = MagicMock()
    service.embedder.embed = AsyncMock(return_value=[0.1] * 768)
    service.client = MagicMock()
    service.client.query_points = AsyncMock()
    service.client.upsert = AsyncMock()
    service.client.retrieve = AsyncMock()
    if existing_payload is None:
        service.client.retrieve.return_value = []
    else:
        service.client.retrieve.return_value = [SimpleNamespace(payload=existing_payload)]
    return service


@pytest.mark.asyncio
async def test_store_reinforces_same_correction():
    """L5: writing the SAME (rephrased) correction twice must REINFORCE — count
    increments and confidence rises, not a flat reset."""
    service = _make_service_with_retrieve(None)
    await service.store_reflexion("task", "x", "File not found: x.py", "list the parent dir first", component="c", confidence=0.7)
    # Seed the existing point from the first write, then write the rephrased same.
    prev = service.client.upsert.await_args.kwargs["points"][0].payload
    service.client.retrieve.return_value = [SimpleNamespace(payload=prev)]
    await service.store_reflexion("task", "x", "File not found: x.py", "list the parent directory first", component="c", confidence=0.7)
    last = service.client.upsert.await_args.kwargs["points"][0].payload
    assert last["count"] == 2
    assert last["confidence"] > 0.7


@pytest.mark.asyncio
async def test_store_conflict_overwrites_and_logs():
    """L5: a genuinely DIFFERENT correction on the same failure must OVERWRITE the
    old content (supersede, not silently lost) with the new correction; count
    carries as recurrence evidence; confidence is not inflated from old content."""
    service = _make_service_with_retrieve({"correction": "list the parent dir first", "count": 3, "confidence": 0.8})
    await service.store_reflexion("task", "x", "File not found: x.py", "reset the model cooldowns then retry", component="c", confidence=0.7)
    last = service.client.upsert.await_args.kwargs["points"][0].payload
    assert last["correction"] == "reset the model cooldowns then retry"  # new content wins
    assert last["count"] >= 3  # recurrence evidence carried, not reset to 1
    assert last["confidence"] < 0.8  # new content gets modest confidence, not inflated


@pytest.mark.asyncio
async def test_store_repeated_identical_write_reinforces():
    """L5: identical repeat writes must monotonically raise confidence and count."""
    service = _make_service_with_retrieve(None)
    for i in range(3):
        if i > 0:
            prev = service.client.upsert.await_args.kwargs["points"][0].payload
            service.client.retrieve.return_value = [SimpleNamespace(payload=prev)]
        await service.store_reflexion("t", "x", "File not found: q.py", "always verify first", component="c", confidence=0.7)
    last = service.client.upsert.await_args.kwargs["points"][0].payload
    assert last["count"] == 3
    assert last["confidence"] > 0.7

@pytest.mark.asyncio
async def test_store_retrieve_failure_flagged_distinctly_from_first_write(tmp_path, monkeypatch, caplog):
    """L5 (trust-gated consolidation): a GENUINE retrieve failure (raise) must be
    distinguishable from a legitimate first write — it must (a) log a distinct
    warning and (b) flag the stored payload `retrieve_failed=True` so history-not-
    consulted is observable. It must NOT silently fold into the happy-path default
    (which would misclassify a real conflict as brand-new)."""
    import logging
    from unittest.mock import AsyncMock, MagicMock
    from swarm_os.services.reflection_loop import ReflectionService

    service = ReflectionService.__new__(ReflectionService)
    service.collection = "ReflexionMemory"
    service._init_task = None
    service._ensured = True
    service.embedder = MagicMock()
    service.embedder.embed = AsyncMock(return_value=[0.1] * 768)
    service.client = MagicMock()
    service.client.query_points = AsyncMock()
    service.client.upsert = AsyncMock()
    # retrieve RAISES a real exception (simulating a DB/vector-store failure)
    async def _boom(*a, **k):
        raise RuntimeError("qdrant timeout")
    service.client.retrieve = _boom

    with caplog.at_level(logging.WARNING, logger="ReflectionLoop"):
        await service.store_reflexion("t", "x", "File not found: z.py", "list the parent dir first", component="c", confidence=0.7)

    last = service.client.upsert.await_args.kwargs["points"][0].payload
    # Flagged as unclassified, NOT silently presented as a normal first-write.
    assert last["retrieve_failed"] is True
    assert last["count"] == 1
    # Distinct log line so operators can see the failure, unlike a legit first write.
    assert any("retrieve FAILED" in r.getMessage() for r in caplog.records)

    # Contrast: a service whose retrieve returns [] (no prior record) must NOT
    # set the flag.
    service2 = _make_service_with_retrieve(None)
    await service2.store_reflexion("t", "x", "File not found: zz.py", "list parent dir", component="c", confidence=0.7)
    last2 = service2.client.upsert.await_args.kwargs["points"][0].payload
    assert last2["retrieve_failed"] is False
