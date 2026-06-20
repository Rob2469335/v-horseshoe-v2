"""
Module: organism_snapshot
Order: 24
Package: organism.contracts
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from swarm_os.organism.contracts.organism_metrics import OrganismMetrics


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class OrganismSnapshot:
    status: str = "bootstrapping"
    metrics: OrganismMetrics = field(default_factory=OrganismMetrics)
    captured_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "metrics": self.metrics.to_dict(),
            "captured_at": self.captured_at.isoformat(),
        }
