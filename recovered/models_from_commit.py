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
    timestamp_ms: float = 0.0
    model: str = ""
    tokens: int = 0
    cost: float = 0.0
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelProfile:
    name: str
    role: str
    cost_per_1m: float = 0.0
    max_tokens: int = 8192
    preferred_temp: float = 0.7
    cooldown_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelState:
    name: str
    role: str = "fast"
    failures: int = 0
    successes: int = 0
    total_latency_ms: float = 0.0
    total_requests: int = 0
    total_critic_score: float = 0.0
    critic_samples: int = 0
    rejections: int = 0
    cooldown_until: float = 0.0
    last_success_at: float = 0.0
    last_attempt_at: float = 0.0
    last_critic_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RoutingPolicy:
    default_role: str = "fast"
    minimum_score: float = 0.0
    allow_fallback: bool = True
    cooldown_multiplier: float = 2.0
    max_cooldown_factor: float = 3.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RouteOutcome:
    model: str
    role: str
    reason: str
    fallback: bool = False
    score: float = 0.0
    candidates: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RouteFeedback:
    model: str
    accepted: bool
    latency_ms: float = 0.0
    critic_score: float = 0.0
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RouteDecision:
    model: str
    role: str
    reason: str
    fallback: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
