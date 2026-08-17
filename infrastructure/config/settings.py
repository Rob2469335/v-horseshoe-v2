from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    app_env: str
    log_level: str

    sqlite_path: str

    qdrant_url: str
    qdrant_api_key: str | None
    qdrant_collection_traces: str
    qdrant_collection_memory: str
    embedding_dimension: int

    cache_backend: str
    cache_ttl_seconds: int
    cache_namespace: str

    jobs_enabled: bool
    jobs_poll_seconds: int

    feature_flags_path: str

    health_timeout_seconds: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        sqlite_path=os.getenv("SQLITE_PATH", "./data/horseshoe.db"),
        qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
        qdrant_collection_traces=os.getenv(
            "QDRANT_COLLECTION_TRACES", "horseshoe_traces"
        ),
        qdrant_collection_memory=os.getenv(
            "QDRANT_COLLECTION_MEMORY", "horseshoe_memory"
        ),
        embedding_dimension=_get_int("EMBEDDING_DIMENSION", 1024),
        cache_backend=os.getenv("CACHE_BACKEND", "memory"),
        cache_ttl_seconds=_get_int("CACHE_TTL_SECONDS", 900),
        cache_namespace=os.getenv("CACHE_NAMESPACE", "horseshoe"),
        jobs_enabled=_get_bool("JOBS_ENABLED", True),
        jobs_poll_seconds=_get_int("JOBS_POLL_SECONDS", 30),
        feature_flags_path=os.getenv(
            "FEATURE_FLAGS_PATH", "./config/feature_flags.json"
        ),
        health_timeout_seconds=_get_int("HEALTH_TIMEOUT_SECONDS", 5),
    )
