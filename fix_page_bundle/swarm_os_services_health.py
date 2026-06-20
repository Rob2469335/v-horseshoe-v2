from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import httpx


OLLAMA_URL = "http://127.0.0.1:11434/api/tags"


@dataclass
class BackendHealth:
    ollama_ok: bool = False
    consecutive_failures: int = 0
    failure_count: int = 0
    last_error_message: Optional[str] = None
    latency_history_ms: List[float] = field(default_factory=list)
    last_check_time: Optional[str] = None


backend_health = BackendHealth()


def refresh_backend_health(timeout_seconds: float = 2.0) -> BackendHealth:
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.get(OLLAMA_URL)
            response.raise_for_status()

        backend_health.ollama_ok = True
        backend_health.consecutive_failures = 0
        backend_health.last_error_message = None

        elapsed_ms = response.elapsed.total_seconds() * 1000.0
        backend_health.latency_history_ms.append(elapsed_ms)
        backend_health.latency_history_ms = backend_health.latency_history_ms[-20:]
    except Exception as exc:
        backend_health.ollama_ok = False
        backend_health.consecutive_failures += 1
        backend_health.failure_count += 1
        backend_health.last_error_message = str(exc)

    backend_health.last_check_time = datetime.now(timezone.utc).isoformat()
    return backend_health
