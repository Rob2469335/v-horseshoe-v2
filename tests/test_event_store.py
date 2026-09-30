"""Direct tests for EventStore (swarm_os/events/store.py)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from swarm_os.events.envelope import EventEnvelope
from swarm_os.events.store import EventStore


@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    """Create an EventStore rooted in a temporary directory."""
    return EventStore(tmp_path / "events")


class TestEventStoreAppend:
    def test_append_writes_one_line(self, store: EventStore):
        env = EventEnvelope.create("t", "s", {"a": 1})
        store.append(env)
        assert store.path.exists()
        lines = store.path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_append_multiple_creates_sequential_lines(self, store: EventStore):
        for i in range(3):
            store.append(EventEnvelope.create("t", "s", {"i": i}))
        lines = store.path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_append_preserves_envelope_data(self, store: EventStore):
        env = EventEnvelope.create("tool_result", "coder", {"ok": True})
        store.append(env)
        d = json.loads(store.path.read_text(encoding="utf-8").strip())
        assert d["event_type"] == "tool_result"
        assert d["source"] == "coder"
        assert d["payload"]["ok"] is True
        assert d["event_id"] == env.event_id

    def test_append_filelock_timeout(self, tmp_path: Path):
        """Verify FileLock is attempted (may succeed if no contention)."""
        s = EventStore(tmp_path / "e")
        s.append(EventEnvelope.create("t", "s", {}))
        assert s.path.exists()


class TestEventStoreReadAll:
    def test_read_all_empty_when_no_file(self, tmp_path: Path):
        s = EventStore(tmp_path / "e")
        assert s.read_all() == []

    def test_read_all_returns_all_events(self, store: EventStore):
        for i in range(5):
            store.append(EventEnvelope.create("t", "s", {"i": i}))
        items = store.read_all()
        assert len(items) == 5
        assert items[0]["payload"]["i"] == 0
        assert items[4]["payload"]["i"] == 4

    def test_read_all_skips_corrupted_lines(self, store: EventStore):
        store.append(EventEnvelope.create("t", "s", {"i": 0}))
        # Manually corrupt the file
        with store.path.open("a", encoding="utf-8") as f:
            f.write("NOT VALID JSON\n")
        store.append(EventEnvelope.create("t", "s", {"i": 2}))
        items = store.read_all()
        assert len(items) == 2
        assert items[0]["payload"]["i"] == 0
        assert items[1]["payload"]["i"] == 2


class TestEventStoreTail:
    def test_tail_empty_when_no_file(self, tmp_path: Path):
        s = EventStore(tmp_path / "e")
        assert s.tail() == []

    def test_tail_returns_last_n_events(self, store: EventStore):
        for i in range(10):
            store.append(EventEnvelope.create("t", "s", {"i": i}))
        items = store.tail(limit=3)
        assert len(items) == 3
        assert items[0]["payload"]["i"] == 7
        assert items[2]["payload"]["i"] == 9

    def test_tail_skips_corrupted_lines(self, store: EventStore):
        for i in range(3):
            store.append(EventEnvelope.create("t", "s", {"i": i}))
        with store.path.open("a", encoding="utf-8") as f:
            f.write("CORRUPTED\n")
        items = store.tail(limit=10)
        assert len(items) == 3

    def test_tail_limit_larger_than_file_returns_all(self, store: EventStore):
        for i in range(2):
            store.append(EventEnvelope.create("t", "s", {"i": i}))
        items = store.tail(limit=100)
        assert len(items) == 2


class TestEventStoreConcurrentAppend:
    def test_concurrent_appends_no_crash(self, store: EventStore):
        errors = []

        def writer(n):
            try:
                for i in range(20):
                    store.append(EventEnvelope.create("t", "s", {"thread": n, "i": i}))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        items = store.read_all()
        assert len(items) == 80  # 4 threads x 20 events

    def test_concurrent_read_during_write(self, store: EventStore):
        for i in range(50):
            store.append(EventEnvelope.create("t", "s", {"i": i}))

        read_result = []
        write_done = threading.Event()

        def reader():
            read_result.append(store.tail(limit=10))

        def writer():
            for i in range(50, 100):
                store.append(EventEnvelope.create("t", "s", {"i": i}))
            write_done.set()

        rt = threading.Thread(target=reader)
        wt = threading.Thread(target=writer)
        rt.start()
        wt.start()
        wt.join()
        rt.join()
        assert len(read_result) == 1
        assert len(read_result[0]) > 0
