from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List

from .models import StepTrace


class TraceCollector:
    def __init__(self) -> None:
        self._events: List[StepTrace] = []

    def new_trace_id(self) -> str:
        return uuid.uuid4().hex

    def add(
        self,
        *,
        trace_id: str,
        step_id: str,
        phase: str,
        actor: str,
        action: str,
        status: str,
        duration_ms: float = 0.0,
        model: str = "",
        tokens: int = 0,
        cost: float = 0.0,
        summary: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> None:
        self._events.append(
            StepTrace(
                trace_id=trace_id,
                step_id=step_id,
                phase=phase,
                actor=actor,
                action=action,
                status=status,
                duration_ms=duration_ms,
                model=model,
                tokens=tokens,
                cost=cost,
                summary=summary,
                metadata=metadata or {},
            )
        )

    def events(self) -> List[StepTrace]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()

    def now_ms(self) -> float:
        return time.perf_counter() * 1000.0
