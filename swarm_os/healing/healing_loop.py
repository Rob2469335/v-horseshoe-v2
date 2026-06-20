from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class HealingState:
    last_action: str | None = None
    last_heal_time: float | None = None


class DefaultDetector:
    def check(self):
        return {"health_score": 100, "signals": []}


class HealingLoop:
    def __init__(self, cooldown_seconds: int = 5) -> None:
        self.detector = DefaultDetector()
        self.state = HealingState()
        self.cooldown_seconds = cooldown_seconds

    def tick(self) -> dict:
        now = time.time()
        if self.state.last_heal_time and now < (self.state.last_heal_time + self.cooldown_seconds):
            return {"status": "throttled", "cooldown_remaining": (self.state.last_heal_time + self.cooldown_seconds - now)}

        report = self.detector.check()
        signals = report.get('signals', [])
        if not signals:
            return {"status": "stable"}

        # find first unhealthy signal
        for s in signals:
            if not s.get('ok', True):
                comp = s.get('component')
                # choose action based on component
                action = 'restart_vector_layer' if comp == 'qdrant' else 'restart_component'
                # simulate healing
                self.state.last_action = action
                self.state.last_heal_time = now
                return {"status": "healing_executed", "result": {"action": action}}

        return {"status": "stable"}
