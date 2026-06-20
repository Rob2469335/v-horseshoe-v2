"""
Module: outcome_record
Order: 2
Package: foundation.memory
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class OutcomeRecord:
    outcome_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    objective: str = ""
    status: str = "unknown"
    score: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id": self.outcome_id,
            "session_id": self.session_id,
            "objective": self.objective,
            "status": self.status,
            "score": self.score,
            "metrics": self.metrics,
            "evidence": self.evidence,
            "created_at": self.created_at.isoformat(),
        }
