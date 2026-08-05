from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class TaskState(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class RunContext:
    run_id: str
    trace_id: Optional[str] = None


@dataclass
class ExecutionResult:
    task_id: str
    success: bool
    output: Any = None
    error: str = None
    metadata: Dict[str, Any] = None


@dataclass
class RuntimeEvent:
    run_id: str
    task_id: str
    event_type: str
    payload: Dict[str, Any]
