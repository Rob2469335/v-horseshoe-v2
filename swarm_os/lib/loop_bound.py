"""Loop-bound pooled async clients.

A module-level pooled async client (httpx.AsyncClient / AsyncQdrantClient) is
bound to the event loop that created it; reusing it from a second or closed loop
fails with "Event loop is closed" / "got Future attached to a different loop".
This wraps the lazy-create-and-reuse pattern with owner-loop tracking so the
client is rebuilt when the owning loop is closed or differs — the same fix used
in routes.py (_PROBE_CLIENT_LOOP), vector_store.py (d94e177), api_client,
fallback_manager, orchestrator and qdrant_store.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional


class LoopBoundAsyncClient:
    """Lazily create one async client and rebuild it across event loops."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._client: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    @staticmethod
    def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    @staticmethod
    def _closed(client: Any) -> bool:
        return bool(getattr(client, "is_closed", False))

    def _stale(self, loop: Optional[asyncio.AbstractEventLoop]) -> bool:
        if self._client is None or self._closed(self._client):
            return True
        if self._loop is not None and self._loop.is_closed():
            return True
        if self._loop is not None and loop is not None and self._loop is not loop:
            return True
        return False

    def get(self) -> Any:
        """Return the pooled client, rebuilding it if it is loop-stale."""
        loop = self._running_loop()
        if self._stale(loop):
            with self._lock:
                if self._stale(loop):
                    self._client = self._factory()
                    self._loop = loop
        return self._client

    def reset(self) -> None:
        """Drop the cached client reference (e.g. after an explicit close)."""
        with self._lock:
            self._client = None
            self._loop = None
