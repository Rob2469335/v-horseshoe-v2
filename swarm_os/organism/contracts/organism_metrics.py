"""
Module: organism_metrics
Order: 23
Package: organism.contracts
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class OrganismMetrics:
    throughput: float = 0.0
    success_rate: float = 0.0
    error_rate: float = 0.0
    latency_ms: float = 0.0
    extra: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, float]:
        base = {
            "throughput": self.throughput,
            "success_rate": self.success_rate,
            "error_rate": self.error_rate,
            "latency_ms": self.latency_ms,
        }
        base.update(self.extra)
        return base
