from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock

from swarm_os.api.routes import get_memories


class _FakePoint:
    def __init__(self, payload):
        self.payload = payload


def _fake_vs(points_by_collection):
    vs = MagicMock()
    colls = [SimpleNamespace(name=name) for name in points_by_collection]
    client = AsyncMock()
    client.get_collections = AsyncMock(return_value=SimpleNamespace(collections=colls))

    async def scroll(collection_name=None, **kwargs):
        return ([_FakePoint(p) for p in points_by_collection[collection_name]], None)

    client.scroll = scroll
    vs.client = client
    return vs


async def test_memories_returns_sane_response_when_timestamp_none():
    """A payload with timestamp: None must NOT 500 — the /memories sort key
    crashes on float(None). The endpoint returns a sane response with the
    None-timestamped memory at the bottom."""
    vs = _fake_vs({"agent_memory": [{"fact": "old", "timestamp": None}]})
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    assert result["status"] == "success"
    assert result["data"]["agent_memory"] == [{"fact": "old", "timestamp": None}]


async def test_memories_sorts_newest_first_mixed_timestamp_types():
    """Timestamps arrive as floats AND ISO strings across memory writers; the
    sort must handle both without crashing, newest first."""
    vs = _fake_vs({"agent_memory": [
        {"fact": "iso_old", "timestamp": "2026-08-01T12:00:00+00:00"},
        {"fact": "float_new", "timestamp": 1786300000.0},
        {"fact": "none", "timestamp": None},
    ]})
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    order = [p["fact"] for p in result["data"]["agent_memory"]]
    assert order == ["float_new", "iso_old", "none"]


async def test_memories_handles_missing_and_garbage_timestamps():
    """Missing timestamp (default 0) and non-numeric garbage must not raise —
    both degrade to the bottom of the sort."""
    vs = _fake_vs({"agent_memory": [
        {"fact": "garbage", "timestamp": "not-a-date"},
        {"fact": "missing"},
        {"fact": "num_new", "timestamp": 5000.0},
    ]})
    with patch("swarm_os.services.vector_store.VectorStore", return_value=vs):
        result = await get_memories()
    order = [p["fact"] for p in result["data"]["agent_memory"]]
    assert order == ["num_new", "garbage", "missing"]
