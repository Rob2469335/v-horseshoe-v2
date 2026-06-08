"""
Module: promotion_record
Order: 5
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
class PromotionRecord:
    promotion_id: str = field(default_factory=lambda: str(uuid4()))
    experiment_id: str = ""
    promoted_from: str = ""
    promoted_to: str = ""
    rationale: str = ""
    approved_by: str = "system"
    promoted_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, str]:
        return {
            "promotion_id": self.promotion_id,
            "experiment_id": self.experiment_id,
            "promoted_from": self.promoted_from,
            "promoted_to": self.promoted_to,
            "rationale": self.rationale,
            "approved_by": self.approved_by,
            "promoted_at": self.promoted_at.isoformat(),
        }