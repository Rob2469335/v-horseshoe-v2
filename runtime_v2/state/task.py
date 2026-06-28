from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class Task:
    task_id: str
    run_id: str
    type: str
    payload: Dict[str, Any]
    parent_task_id: Optional[str] = None
