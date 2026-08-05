from dataclasses import dataclass
from typing import Any, Dict
from datetime import datetime

@dataclass
class RepairCompletedEvent:
    id: str
    success: bool
    tool: str
    signature: str
    input: str = ""
    output: str = ""
    metadata: Dict[str, Any] = None
    timestamp: str = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()

@dataclass
class ReviewCompletedEvent:
    repair_id: str
    is_success: bool
    confidence: float

@dataclass
class SkillLearnedEvent:
    pattern: str
    action: str
    initial_confidence: float
    source_repair_id: str
