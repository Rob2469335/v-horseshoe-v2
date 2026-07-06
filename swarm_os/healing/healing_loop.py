from __future__ import annotations
import time
from dataclasses import dataclass
from .failure_detector import FailureDetector
from .governor import Governor


@dataclass
class HealingState:
    last_action: str | None = None
    last_heal_time: float | None = None
    last_incident_id: str | None = None


class HealingLoop:
    def __init__(self, cooldown_seconds: int = 30, detector=None, governor=None) -> None:
        self.detector = detector or FailureDetector()
        self.governor = governor or Governor()
        self.state = HealingState()
        self.cooldown_seconds = cooldown_seconds

    def tick(self) -> dict:
        now = time.time()
        if self.state.last_heal_time and now < (self.state.last_heal_time + self.cooldown_seconds):
            return {"status": "throttled", "cooldown_remaining": (self.state.last_heal_time + self.cooldown_seconds - now)}

        report = self.detector.check()
        signals = report.get("signals", [])
        if not signals:
            return {"status": "stable", "health_score": report.get("health_score", 100)}

        symptom = signals[0]
        decision = self.governor.decide(symptom)

        self.state.last_action = decision.get("mode", "unknown")
        self.state.last_heal_time = now
        self.state.last_incident_id = decision.get("incident_id")

        return {
            "status": "healing_decision",
            "component": symptom.get("component"),
            "decision": decision,
            "all_signals": signals,
        }