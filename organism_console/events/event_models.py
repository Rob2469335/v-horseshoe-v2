from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field


class EventStatus(str, Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    partial = "partial"


class EventType(str, Enum):
    request_received = "request_received"
    plan_created = "plan_created"
    tool_called = "tool_called"
    tool_result = "tool_result"
    trace_written = "trace_written"
    memory_written = "memory_written"
    evaluation_completed = "evaluation_completed"
    healing_triggered = "healing_triggered"


class RuntimeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str
    parent_event_id: str | None = None
    event_type: EventType
    source: str
    target: str | None = None
    status: EventStatus = EventStatus.pending
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp_ms: int


class ToolCall(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 30.0
    retry_count: int = 0


class ToolResult(BaseModel):
    tool_name: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0


class PlanStep(BaseModel):
    step_id: str
    tool_name: str
    purpose: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    trace_id: str
    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    planner: Literal["static", "llm", "hybrid"] = "static"


class EvaluationResult(BaseModel):
    trace_id: str
    score: float
    verdict: Literal["healthy", "needs_healing", "failed"]
    reasons: list[str] = Field(default_factory=list)
    suggested_actions: list[dict[str, Any]] = Field(default_factory=list)
