from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class PlanStep:
    step_id: str
    kind: str
    goal: str
    assigned_to: str = "orchestrator"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StepDecision:
    action: str
    reason: str
    target: str = "orchestrator"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str
    requires_approval: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CriticResult:
    accepted: bool
    score: float
    reason: str
    retryable: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StepTrace:
    trace_id: str
    step_id: str
    phase: str
    actor: str
    action: str
    status: str
    duration_ms: float = 0.0
    model: str = ""
    tokens: int = 0
    cost: float = 0.0
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
