from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ExecutionResult:
    task_id: str
    success: bool
    output: Optional[Any] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
