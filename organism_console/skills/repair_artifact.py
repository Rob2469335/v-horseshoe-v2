from dataclasses import dataclass
from typing import List

@dataclass
class RepairArtifact:
    skill_id: str
    pattern: str
    diagnosis: str
    patch: str
    strategy: str
    examples: List[str] = None
    
    def __post_init__(self):
        if self.examples is None:
            self.examples = []
