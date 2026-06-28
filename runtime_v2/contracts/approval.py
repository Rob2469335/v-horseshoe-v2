from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class ApprovalRequest:
    approval_id: str
    run_id: str
    task_id: str
    action: str
    payload: Dict[str, Any]
