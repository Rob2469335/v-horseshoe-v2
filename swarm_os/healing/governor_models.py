from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
import time
import uuid


def _now_ts() -> float:
    return time.time()


@dataclass
class FailureRecord:
    incident_id: str
    symptom: Dict[str, Any]
    root_cause: Optional[str]
    hypotheses: List[Dict[str, Any]]
    repair_attempts: List[Dict[str, Any]]
    successful_fix: Optional[Dict[str, Any]]
    confidence: float
    outcome: str
    service: Optional[str]
    environment: Dict[str, Any]
    metrics_before: Dict[str, Any]
    metrics_after: Dict[str, Any]
    timestamp: float = field(default_factory=_now_ts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReflectionRecord:
    incident_id: str
    what_happened: str
    what_failed: str
    why: str
    what_worked: str
    what_to_change_next_time: str
    timestamp: float = field(default_factory=_now_ts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillRecord:
    skill_name: str
    trigger_conditions: List[Dict[str, Any]]
    repair_sequence: List[Dict[str, Any]]
    prerequisites: List[str]
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.0
    average_duration: float = 0.0
    last_used_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyStats:
    strategy_id: str
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.0
    average_duration: float = 0.0
    approval_required_threshold: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChangeRecord:
    change_id: str
    incident_id: str
    snapshot_ref: Optional[str]
    patch_ref: Optional[str]
    validation_result: Optional[Dict[str, Any]]
    deployed: bool = False
    rollback_ref: Optional[str] = None
    timestamp: float = field(default_factory=_now_ts)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# helper for generating ids


def gen_id(prefix: str = "inc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
