from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Callable


class HealthProbe:
    def __init__(self, check_fn: Callable | None = None) -> None:
        # check_fn(component) -> (bool, detail)
        self.check_fn = check_fn

    def probe(self, component: str) -> SimpleNamespace:
        start = time.time()
        if self.check_fn:
            ok, detail = self.check_fn(component)
            status = "healthy" if ok else "unhealthy"
        else:
            # default healthy
            ok = True
            detail = "ok"
            status = "healthy"
        latency_ms = max(0.0, (time.time() - start) * 1000.0)
        return SimpleNamespace(
            component=component, status=status, latency_ms=latency_ms, detail=detail
        )
