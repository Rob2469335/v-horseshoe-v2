from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    event_type: str
    occurred_at: str
    source: str
    payload: dict[str, Any]

    @staticmethod
    def create(event_type: str, source: str, payload: dict[str, Any]) -> EventEnvelope:
        return EventEnvelope(
            event_id=str(uuid4()),
            event_type=event_type,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            source=source,
            payload=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
