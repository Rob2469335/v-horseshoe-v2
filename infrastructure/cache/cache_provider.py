from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from infrastructure.config.settings import get_settings
from infrastructure.runtime.feature_flags import get_feature_flags


class CacheProvider(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


class InMemoryCacheProvider:
    def __init__(self, namespace: str, default_ttl_seconds: int) -> None:
        self.namespace = namespace
        self.default_ttl_seconds = default_ttl_seconds
        self._items: dict[str, CacheEntry] = {}
        self._lock = RLock()

    def _full_key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def get(self, key: str) -> Any | None:
        full_key = self._full_key(key)
        with self._lock:
            entry = self._items.get(full_key)
            if entry is None:
                return None
            if entry.expires_at < time.time():
                self._items.pop(full_key, None)
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        full_key = self._full_key(key)
        with self._lock:
            self._items[full_key] = CacheEntry(
                value=value,
                expires_at=time.time() + ttl,
            )

    def delete(self, key: str) -> None:
        full_key = self._full_key(key)
        with self._lock:
            self._items.pop(full_key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class NullCacheProvider:
    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        return None

    def delete(self, key: str) -> None:
        return None

    def clear(self) -> None:
        return None


def get_cache_provider() -> CacheProvider:
    settings = get_settings()
    flags = get_feature_flags()

    if not flags.cache_enabled:
        return NullCacheProvider()

    if settings.cache_backend == "memory":
        return InMemoryCacheProvider(
            namespace=settings.cache_namespace,
            default_ttl_seconds=settings.cache_ttl_seconds,
        )

    return NullCacheProvider()
