from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class DelegationRequest:
    from_agent: str
    to_agent: str
    task_payload: Dict[str, Any]
