"""Direct tests for EventEnvelope (swarm_os/events/envelope.py)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from swarm_os.events.envelope import EventEnvelope


class TestEventEnvelopeCreate:
    def test_create_sets_all_fields(self):
        env = EventEnvelope.create(
            event_type="tool_result",
            source="coder",
            payload={"tool": "filesystem", "ok": True},
        )
        assert env.event_type == "tool_result"
        assert env.source == "coder"
        assert isinstance(env.event_id, str)
        assert len(env.event_id) == 36  # uuid4 format
        assert isinstance(env.payload, dict)
        assert env.payload["tool"] == "filesystem"

    def test_create_generates_unique_ids(self):
        a = EventEnvelope.create("x", "s", {})
        b = EventEnvelope.create("x", "s", {})
        assert a.event_id != b.event_id

    def test_create_timestamp_is_iso8601_utc(self):
        env = EventEnvelope.create("x", "s", {})
        dt = datetime.fromisoformat(env.occurred_at)
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc

    def test_create_preserves_empty_payload(self):
        env = EventEnvelope.create("x", "s", {})
        assert env.payload == {}


class TestEventEnvelopeToDict:
    def test_to_dict_contains_all_fields(self):
        env = EventEnvelope.create("test_type", "test_source", {"key": "val"})
        d = env.to_dict()
        assert set(d.keys()) == {
            "event_id",
            "event_type",
            "occurred_at",
            "source",
            "payload",
        }

    def test_to_dict_matches_fields(self):
        env = EventEnvelope.create("t", "s", {"a": 1})
        d = env.to_dict()
        assert d["event_type"] == "t"
        assert d["source"] == "s"
        assert d["payload"] == {"a": 1}

    def test_to_dict_is_json_serializable(self):
        import json
        env = EventEnvelope.create("t", "s", {"x": [1, 2, 3]})
        serialized = json.dumps(env.to_dict())
        parsed = json.loads(serialized)
        assert parsed["payload"]["x"] == [1, 2, 3]


class TestEventEnvelopeFrozen:
    def test_immutable_event_type(self):
        env = EventEnvelope.create("t", "s", {})
        with pytest.raises(AttributeError):
            env.event_type = "changed"

    def test_immutable_payload(self):
        env = EventEnvelope.create("t", "s", {"a": 1})
        with pytest.raises(AttributeError):
            env.payload = {}

    def test_immutable_event_id(self):
        env = EventEnvelope.create("t", "s", {})
        with pytest.raises(AttributeError):
            env.event_id = "changed"


class TestEventEnvelopeFieldTypes:
    def test_event_id_is_string(self):
        env = EventEnvelope.create("t", "s", {})
        assert isinstance(env.event_id, str)

    def test_event_type_is_string(self):
        env = EventEnvelope.create("t", "s", {})
        assert isinstance(env.event_type, str)

    def test_occurred_at_is_string(self):
        env = EventEnvelope.create("t", "s", {})
        assert isinstance(env.occurred_at, str)

    def test_source_is_string(self):
        env = EventEnvelope.create("t", "s", {})
        assert isinstance(env.source, str)

    def test_payload_is_dict(self):
        env = EventEnvelope.create("t", "s", {})
        assert isinstance(env.payload, dict)


class TestEventEnvelopeSerialization:
    def test_round_trip_json(self):
        import json
        env = EventEnvelope.create(
            "tool_result",
            "coder",
            {"tool": "filesystem", "ok": True, "error": None},
        )
        d = env.to_dict()
        s = json.dumps(d)
        d2 = json.loads(s)
        assert d2["event_type"] == env.event_type
        assert d2["source"] == env.source
        assert d2["payload"] == env.payload

    def test_round_trip_complex_payload(self):
        import json
        payload = {
            "nested": {"a": [1, 2, {"b": 3}]},
            "unicode": "\u00e9\u00e8\u00ea",
            "none_val": None,
        }
        env = EventEnvelope.create("t", "s", payload)
        d = json.loads(json.dumps(env.to_dict()))
        assert d["payload"] == payload
