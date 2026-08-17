from dataclasses import dataclass, field
import time
from typing import Dict, Any


@dataclass
class PluginState:
    name: str
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    last_used: float = 0.0
    fitness: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def update_success(self, latency_ms: float, score: float = 1.0):
        self.usage_count += 1
        self.success_count += 1
        self.total_latency_ms += latency_ms
        self.last_used = time.time()
        self.fitness = (self.fitness * 0.85) + (score * 0.15)

    def update_failure(self):
        self.usage_count += 1
        self.failure_count += 1
        self.last_used = time.time()
        self.fitness *= 0.9
