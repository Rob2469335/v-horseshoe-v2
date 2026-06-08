from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BackendHealth:
    ollama_ok: bool = False
    consecutive_failures: int = 0
    failure_count: int = 0
    last_error_message: Optional[str] = None
    latency_history_ms: List[float] = field(default_factory=list)
    last_check_time: Optional[str] = None


backend_health = BackendHealth()

