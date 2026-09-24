"""Regression tests for the transient-transport retry in MemoryBridge._store().

These tests deliberately mock ``self.vs.upsert`` to simulate the exception
shapes that actually escape qdrant-client in production. The production
exception is ``ResponseHandlingException`` (qdrant_client wraps httpx transport
errors), not raw httpx exceptions — raw-httpx tests verify the defensive path;
the ResponseHandlingException tests verify the production path.
"""
from __future__ import annotations

import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock
from qdrant_client.http.exceptions import ResponseHandlingException

from swarm_os.memory.memory_bridge import MemoryBridge


@pytest.fixture
def bridge_stub():
    """Minimal MemoryBridge with a mock VectorStore."""
    bridge = MemoryBridge.__new__(MemoryBridge)
    bridge.lock_vector = asyncio.Lock()
    bridge.vs = MagicMock()
    return bridge


class TestStoreRetry:
    """Transport-retry tests — production path uses ResponseHandlingException."""

    @pytest.mark.asyncio
    async def test_readerror_retry_succeeds(self, bridge_stub):
        """ConnectTimeout wrapped in ResponseHandlingException → retry → success."""
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ResponseHandlingException(httpx.ReadError("connection reset"))
            return "doc_id"

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_connect_timeout_retry_succeeds(self, bridge_stub):
        """ConnectTimeout wrapped in ResponseHandlingException → retry → success."""
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ResponseHandlingException(httpx.ConnectTimeout("localhost:6333"))
            return "doc_id"

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_two_consecutive_transport_errors_returns_false(self, bridge_stub):
        """Two ResponseHandlingException transport errors → False, 2 attempts."""
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            raise ResponseHandlingException(httpx.ReadError("connection reset"))

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is False
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_non_transport_response_handling_no_retry(self, bridge_stub):
        """ResponseHandlingException wrapping a non-transport error (e.g.
        ValidationError) must NOT be retried — only the transport tuple."""
        from pydantic import ValidationError
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            raise ResponseHandlingException(ValidationError("bad schema"))

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is False
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_non_transport_exception_no_retry(self, bridge_stub):
        """A plain ValueError (not from qdrant-client) → immediate False."""
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            raise ValueError("schema mismatch")

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is False
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_normal_success_single_attempt(self, bridge_stub):
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            return "doc_id"

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is True
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_connect_error_retry_succeeds(self, bridge_stub):
        """ConnectError wrapped in ResponseHandlingException → retry → success."""
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ResponseHandlingException(httpx.ConnectError("refused"))
            return "doc_id"

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_two_transport_errors_then_success_not_reached(self, bridge_stub):
        """After retry exhaustion: 2 attempts, third NOT reached."""
        call_count = 0

        async def upsert_fn(*a, **k):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ResponseHandlingException(httpx.ReadError("reset"))
            return "doc_id"

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is False
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_uses_same_doc_id(self, bridge_stub):
        """Both attempts must use the identical doc_id (idempotency)."""
        captured_ids = []

        async def upsert_fn(*args, **kwargs):
            captured_ids.append(kwargs.get("doc_id") or args[0])
            if len(captured_ids) == 1:
                raise ResponseHandlingException(httpx.ReadError("reset"))
            return "ok"

        bridge_stub.vs.upsert = AsyncMock(side_effect=upsert_fn)
        assert await bridge_stub._store([0.0] * 768, {"key": "val"}) is True
        assert len(captured_ids) == 2
        assert captured_ids[0] == captured_ids[1], (
            f"Doc IDs differ: {captured_ids[0]} != {captured_ids[1]}"
        )
