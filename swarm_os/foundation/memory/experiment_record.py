"""
Module: experiment_record
Order: 3
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
class ExperimentRecord:
    experiment_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    baseline: str = ""
    candidate: str = ""
    hypothesis: str = ""
    status: str = "planned"
    parameters: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "hypothesis": self.hypothesis,
            "status": self.status,
            "parameters": self.parameters,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
