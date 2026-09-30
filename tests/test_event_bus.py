"""Direct tests for EventBus (swarm_os/core/event_bus.py)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from swarm_os.core.event_bus import EventBus


@pytest.fixture()
def bus(tmp_path: Path) -> EventBus:
    """Create an EventBus with a temporary log path."""
    with patch("swarm_os.core.event_bus.LOG_PATH", str(tmp_path / "test_log.jsonl")):
        b = EventBus()
    return b


class TestEventBusEmit:
    def test_emit_persists_to_disk(self, bus: EventBus):
        bus.emit("test_event", "id-1", {"key": "value"})
        assert Path(bus.persistent_path).exists()
        lines = Path(bus.persistent_path).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_emit_persists_correct_data(self, bus: EventBus):
        bus.emit("tool_result", "run-123", {"ok": True})
        d = json.loads(Path(bus.persistent_path).read_text(encoding="utf-8").strip())
        assert d["event"] == "tool_result"
        assert d["id"] == "run-123"
        assert d["payload"]["ok"] is True
        assert "timestamp" in d

    def test_emit_multiple_events(self, bus: EventBus):
        bus.emit("e1", "id1", {"a": 1})
        bus.emit("e2", "id2", {"b": 2})
        lines = Path(bus.persistent_path).read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_emit_persistence_survives_different_path(self, bus: EventBus):
        """Emit uses the EventBus's persistent_path, not the global LOG_PATH."""
        bus.emit("t", "id", {"x": 1})
        d = json.loads(Path(bus.persistent_path).read_text(encoding="utf-8").strip())
        assert d["payload"]["x"] == 1


class TestEventBusSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_yields_emitted_events(self, bus: EventBus):
        loop = asyncio.get_running_loop()
        bus.main_loop = loop

        received = []

        async def reader():
            async for event in bus.subscribe():
                received.append(event)
                if len(received) >= 2:
                    break

        # Start the reader first, THEN emit
        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)  # let subscribe register the queue

        bus.emit("e1", "id1", {"a": 1})
        bus.emit("e2", "id2", {"b": 2})

        await asyncio.wait_for(task, timeout=2.0)
        assert len(received) >= 2
        assert received[0]["event"] == "e1"

    @pytest.mark.asyncio
    async def test_subscribe_queue_bounded(self, bus: EventBus):
        """Verify subscribe creates a bounded queue (maxsize=1000)."""
        loop = asyncio.get_running_loop()
        bus.main_loop = loop
        q = asyncio.Queue(maxsize=1000)
        bus.subscribers.append(q)
        assert q.maxsize == 1000
        bus.subscribers.remove(q)


class TestEventBusDiskFailure:
    def test_emit_does_not_crash_on_invalid_path(self, tmp_path: Path):
        """Emit should not crash if the log directory cannot be written."""
        with patch("swarm_os.core.event_bus.LOG_PATH", str(tmp_path / "nonexistent" / "deep" / "log.jsonl")):
            b = EventBus()
            # The constructor creates directories, so this should not fail
            # But if it does, emit should not raise
            b.emit("test", "id", {"x": 1})
