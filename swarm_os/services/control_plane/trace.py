from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass(slots=True)
class TraceEvent:
    trace_id: str
    step_id: str
    phase: str
    actor: str
    action: str
    status: str
    timestamp_ms: float
    duration_ms: float = 0.0
    model: str = ""
    tokens: int = 0
    cost: float = 0.0
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

class TraceCollector:
    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

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
        metadata: dict[str, Any] | None = None
    ) -> None:
        event = TraceEvent(
            trace_id=trace_id,
            step_id=step_id,
            phase=phase,
            actor=actor,
            action=action,
            status=status,
            timestamp_ms=time.time() * 1000.0,
            duration_ms=duration_ms,
            model=model,
            tokens=tokens,
            cost=cost,
            summary=summary,
            metadata=dict(metadata or {}),
        )
        self._events.append(event)

    def events(self) -> list[dict[str, Any]]:
        """Returns events serialized as dictionaries."""
        return [asdict(event) for event in self._events]

    def get_raw_events(self) -> list[TraceEvent]:
        """Fixes contract mismatch by exposing the raw objects for attribute-based access."""
        return self._events

    def clear(self) -> None:
        self._events.clear()

