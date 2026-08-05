from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

@dataclass
class HealingEvent:
    event_type: str
    target: str
    action: str
    success: bool
    duration_ms: int
    details: dict[str, Any]
    created_at: str

    @staticmethod
    def build(event_type: str, target: str, action: str, success: bool, duration_ms: int, **details: Any) -> dict[str, Any]:
        return asdict(
            HealingEvent(
                event_type=event_type,
                target=target,
                action=action,
                success=success,
                duration_ms=duration_ms,
                details=details,
                created_at=datetime.now(timezone.utc).isoformat()
            )
        )
