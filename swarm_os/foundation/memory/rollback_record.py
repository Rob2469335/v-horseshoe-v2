"""
Module: rollback_record
Order: 6
Package: foundation.memory
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class RollbackRecord:
    rollback_id: str = field(default_factory=lambda: str(uuid4()))
    target_id: str = ""
    from_version: str = ""
    to_version: str = ""
    reason: str = ""
    triggered_by: str = "system"
    rolled_back_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, str]:
        return {
            "rollback_id": self.rollback_id,
            "target_id": self.target_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "reason": self.reason,
            "triggered_by": self.triggered_by,
            "rolled_back_at": self.rolled_back_at.isoformat(),
        }
