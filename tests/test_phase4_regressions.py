"""Regression tests for Phase 4 fixes:

- event_log_repo.save_state exists as a class method (was nested inside
  _atomic_write_text -> AttributeError on MemoryBridge state saves)
- event_log_repo.read_events bounded tail (OOM guard on offset-0 files)
- anomaly_tracker prunes least-recently-UPDATED sources, not first-inserted
- orchestrator stream_generate dedups concurrent identical streams (parity
  with generate)
"""
from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_event_log_repo_save_state_is_class_method(tmp_path):
    """save_state must be a method on EventLogRepository (it was accidentally
    nested inside _atomic_write_text -> AttributeError)."""
    from swarm_os.repositories.event_log_repo import EventLogRepository
    assert callable(EventLogRepository.save_state)
    repo = EventLogRepository(
        event_log_path=tmp_path / "events.jsonl",
        watermark_path=tmp_path / "wm.json",
        state_path=tmp_path / "state.json",
    )
    repo.save_state({"session": "s1", "model": "x"})
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved == {"session": "s1", "model": "x"}


def test_event_log_repo_read_events_bounded_tail(tmp_path):
    """read_events with max_events keeps only the most recent N while still
    advancing the offset past the whole file (no OOM on an offset-0 fresh file)."""
    from swarm_os.repositories.event_log_repo import EventLogRepository
    lines = "\n".join(json.dumps({"i": i}) for i in range(10)) + "\n"
    path = tmp_path / "events.jsonl"
    path.write_text(lines, encoding="utf-8")
    repo = EventLogRepository(
        event_log_path=path,
        watermark_path=tmp_path / "wm.json",
        state_path=tmp_path / "state.json",
    )
    events, new_offset = repo.read_events(0, max_events=3)
    assert len(events) == 3
    assert [e["i"] for e in events] == [7, 8, 9]
    # Offset advanced past the whole file, so nothing is re-read next time.
    events2, _ = repo.read_events(new_offset, max_events=3)
    assert events2 == []


def test_anomaly_tracker_prunes_least_recently_updated():
    """_prune_if_needed must evict the least-recently-UPDATED sources, not the
    first-inserted ones (a plain dict keeps insertion order, so the old code
    arbitrarily deleted long-running ACTIVE components)."""
    import time
    from swarm_os.healing.anomaly_tracker import AnomalyTracker, MAX_TRACKED_SOURCES
    tracker = AnomalyTracker()
    # Insert more than the cap so pruning kicks in.
    for i in range(MAX_TRACKED_SOURCES + 10):
        tracker.ema_freq[f"src_{i}"] = 0.1
        tracker.last_time[f"src_{i}"] = 1000.0 + i
    # Make src_0 the most-recently-updated (it should be KEPT).
    tracker.last_time["src_0"] = time.time() + 100000
    tracker._prune_if_needed()
    assert "src_0" in tracker.ema_freq, "active component must survive pruning"
    assert len(tracker.ema_freq) <= MAX_TRACKED_SOURCES


@pytest.mark.asyncio
async def test_stream_generate_dedups_concurrent_identical():
    """stream_generate must consult the shared generation slot and suppress an
    identical in-flight stream (parity with generate)."""
    from swarm_os.core import orchestrator as orch_mod

    async def fake_stream(model, messages):
        yield "hello"
        yield " world"

    orch = MagicMock()
    orch.llm = MagicMock()
    orch.llm.stream_generate = fake_stream
    orch.token_manager = AsyncMock()
    orch.token_manager.is_exhausted = AsyncMock(return_value=False)
    orch.token_manager.add_usage = AsyncMock()
    orch.token_manager.get_usage = AsyncMock(return_value=0)
    orch._get_memory_context = AsyncMock(return_value="")
    orch.mcp = MagicMock()
    orch.mcp.get_tools_schema = MagicMock(return_value=[])
    orch.trace = MagicMock()
    orch.trace.new_trace_id = MagicMock(return_value="t1")
    orch.router = MagicMock()
    orch.router.route_model = AsyncMock(return_value=MagicMock(model="qwen3.5-4b", reason="r"))
    orch._fetch_installed_models = AsyncMock(return_value=["qwen3.5-4b"])
    orch._detect_provider = MagicMock(return_value="llama")
    orch.events = MagicMock()
    from swarm_os.core.orchestrator import Orchestrator as RealOrchestrator
    real = RealOrchestrator.__new__(RealOrchestrator)
    real.__dict__.update(orch.__dict__)
    real._infer_task_role = MagicMock(return_value="reasoning")
    real._parse_tool_call = MagicMock(return_value=None)
    real.trace.add = MagicMock()

    # Simulate an identical stream already running: acquire returns True.
    with patch.object(orch_mod, "_acquire_generation_slot", AsyncMock(return_value=True)) as acq:
        chunks = [c async for c in real.stream_generate(None, messages=[{"role": "user", "content": "same prompt"}])]
        assert chunks and "Duplicate generation suppressed" in chunks[0][0]
        # The slot was NOT released for the suppressed (never-started) run.
        acq.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_generate_releases_slot_when_abandoned():
    """An abandoned stream (client disconnect mid-iteration -> GeneratorExit at
    a yield) must release the shared generation slot. Before the try/finally
    wrap, every yield in stream_generate skipped the trailing release, so the
    dedup hash stayed in _active_generations for the full 300s TTL and any
    identical re-request was wrongly suppressed as a duplicate."""
    import swarm_os.core.orchestrator as orch_mod

    async def fake_stream(model, messages):
        yield "hello"
        yield " world"
        yield " done"

    orch = MagicMock()
    orch.llm = MagicMock()
    orch.llm.stream_generate = fake_stream
    orch.token_manager = AsyncMock()
    orch.token_manager.is_exhausted = AsyncMock(return_value=False)
    orch.token_manager.add_usage = AsyncMock()
    orch.token_manager.get_usage = AsyncMock(return_value=0)
    orch._get_memory_context = AsyncMock(return_value="")
    orch.mcp = MagicMock()
    orch.mcp.get_tools_schema = MagicMock(return_value=[])
    orch.trace = MagicMock()
    orch.trace.new_trace_id = MagicMock(return_value="t2")
    orch.router = MagicMock()
    orch.router.route_model = AsyncMock(return_value=MagicMock(model="qwen3.5-4b", reason="r"))
    orch._fetch_installed_models = AsyncMock(return_value=["qwen3.5-4b"])
    orch._detect_provider = MagicMock(return_value="llama")
    orch.events = MagicMock()
    from swarm_os.core.orchestrator import Orchestrator as RealOrchestrator
    real = RealOrchestrator.__new__(RealOrchestrator)
    real.__dict__.update(orch.__dict__)
    real._infer_task_role = MagicMock(return_value="reasoning")
    real._parse_tool_call = MagicMock(return_value=None)
    real.trace.add = MagicMock()

    # Use the REAL acquire/release (not mocked) so the leak is observable.
    assert not orch_mod._active_generations, "suite must start with empty slot registry"
    gen = real.stream_generate(None, messages=[{"role": "user", "content": "abandon me"}])
    agen = gen.__aiter__()
    await agen.__anext__()
    # The stream IS in-flight and holding a slot now.
    assert len(orch_mod._active_generations) == 1, "stream should hold the generation slot"
    stored_hash = next(iter(orch_mod._active_generations))
    # Client disconnects: abandon the stream without draining it.
    await gen.aclose()
    assert stored_hash not in orch_mod._active_generations, (
        "generation slot leaked: hash still registered after the stream was abandoned"
    )
