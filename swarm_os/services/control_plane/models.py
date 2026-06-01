from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class ModelProfile:
    name: str
    role: str
    max_tokens: int = 4096
    preferred_temp: float = 0.7
    cooldown_seconds: float = 5.0
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class ModelState:
    name: str
    role: str
    failures: int = 0
    successes: int = 0
    total_requests: int = 0
    total_latency_ms: float = 0.0
    cooldown_until: float = 0.0
    last_success_at: float = 0.0
    last_attempt_at: float = 0.0
    last_score: float = 0.0
    last_penalty: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class RouteDecision:
    model: str
    role: str
    reason: str
    fallback: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class PlanStep:
    step_id: str
    kind: str
    goal: str
    assigned_to: str = "none"
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class StepDecision:
    action: str
    reason: str
    target: str
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(slots=True)
class StepBudget:
    allowed: bool
    reason: str = "ok"

@dataclass(slots=True)
class CriticResult:
    accepted: bool = True
    score: float = 0.5
    reason: str = "ok"
    retryable: bool = True

@dataclass(slots=True)
class ImprovementProposal:
    proposal_id: str
    category: str
    severity: str
    title: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""
    requires_approval: bool = True
    created_at_ms: float = 0.0
    status: str = "pending"
