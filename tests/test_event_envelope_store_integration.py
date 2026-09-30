"""Integration test: EventEnvelope → EventStore round-trip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm_os.events.envelope import EventEnvelope
from swarm_os.events.store import EventStore


@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events")


class TestEnvelopeStoreRoundTrip:
    def test_create_append_tail_matches(self, store: EventStore):
        """EventEnvelope.create() → EventStore.append() → EventStore.tail()
        returns data matching the original envelope."""
        env = EventEnvelope.create(
            event_type="tool_result",
            source="coder",
            payload={"tool": "filesystem", "ok": True, "error": None},
        )
        store.append(env)
        items = store.tail(limit=1)
        assert len(items) == 1
        item = items[0]
        assert item["event_id"] == env.event_id
        assert item["event_type"] == "tool_result"
        assert item["source"] == "coder"
        assert item["payload"]["tool"] == "filesystem"
        assert item["payload"]["ok"] is True

    def test_multiple_envelopes_preserve_order(self, store: EventStore):
        """Multiple append/tail preserves insertion order."""
        types = ["agent_action", "tool_trace", "tool_result", "generation_completed"]
        for t in types:
            store.append(EventEnvelope.create(t, "coder", {"type": t}))

        items = store.tail(limit=10)
        assert [i["event_type"] for i in items] == types

    def test_read_all_after_multiple_appends(self, store: EventStore):
        """read_all() returns all envelopes in order."""
        for i in range(5):
            store.append(EventEnvelope.create("t", "s", {"i": i}))

        items = store.read_all()
        assert len(items) == 5
        assert all(isinstance(i["event_id"], str) for i in items)
        assert all(isinstance(i["occurred_at"], str) for i in items)

    def test_json_serialization_round_trip(self, store: EventStore):
        """Envelope data survives JSON serialization in the JSONL file."""
        env = EventEnvelope.create(
            "test",
            "test",
            {"nested": {"a": [1, 2]}, "unicode": "\u00e9"},
        )
        store.append(env)

        # Read raw file, parse JSON, verify data integrity
        raw = store.path.read_text(encoding="utf-8").strip()
        parsed = json.loads(raw)
        assert parsed["event_id"] == env.event_id
        assert parsed["payload"]["nested"]["a"] == [1, 2]
        assert parsed["payload"]["unicode"] == "\u00e9"

    def test_tail_limit_with_envelope_data(self, store: EventStore):
        """tail() correctly bounds results with real envelope data."""
        for i in range(20):
            store.append(EventEnvelope.create("t", "s", {"i": i}))

        tail_5 = store.tail(limit=5)
        assert len(tail_5) == 5
        assert tail_5[0]["payload"]["i"] == 15
        assert tail_5[4]["payload"]["i"] == 19

    def test_empty_payload_round_trip(self, store: EventStore):
        """Empty payload survives the round trip."""
        env = EventEnvelope.create("t", "s", {})
        store.append(env)
        items = store.read_all()
        assert len(items) == 1
        assert items[0]["payload"] == {}
