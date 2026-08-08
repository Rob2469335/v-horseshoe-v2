from __future__ import annotations
import logging
import time
from dataclasses import dataclass
from .failure_detector import FailureDetector
from .governor import Governor

log = logging.getLogger(__name__)


@dataclass
class HealingState:
    last_action: str | None = None
    last_heal_time: float | None = None
    last_incident_id: str | None = None
    consecutive_failures: int = 0


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

        from .failure_detector import run_coro_sync
        report = run_coro_sync(self.detector.check())
        signals = report.get("signals", [])
        if not signals:
            self.state.consecutive_failures = 0
            return {"status": "stable", "health_score": report.get("health_score", 100)}

        self.state.consecutive_failures += 1
        # BUG FIX: `if < 1` after += 1 was always False, making the escalation
        # branch dead. Warn once (count == 1), then decide on the second sighting.
        if self.state.consecutive_failures == 1:
            return {"status": "transient_warning", "signals": signals, "health_score": report.get("health_score", 100)}

        symptom = signals[0]
        decision = self.governor.decide(symptom)

        self.state.last_action = decision.get("mode", "unknown")
        self.state.last_heal_time = now
        self.state.last_incident_id = decision.get("incident_id")
        # BUG FIX: do NOT reset consecutive_failures here. The counter is only
        # reset when the component explicitly reports healthy (no signals above).
        # Resetting after a decision made a persistent failure re-enter the
        # "transient_warning" state on every cycle, so repeated/healing loops
        # never escalated past "warn once, decide on the second sighting" — the
        # failure counter must keep climbing until the component recovers.

        return {
            "status": "healing_decision",
            "component": symptom.get("component"),
            "decision": decision,
            "all_signals": signals,
        }

    def finalize(self, decision: dict | None, result: dict | None) -> None:
        """Close the incident loop by feeding the real recovery outcome back
        through the governor so the learner records the result and strategy
        stats are updated. Previously Governor.finalize() had no callers."""
        if not decision:
            return
        outcome = "SUCCESS" if result and result.get("ok") else "FAILURE"
        try:
            self.governor.finalize(decision.get("incident_id"), {
                "outcome": outcome,
                "repair": {
                    "action": (result or {}).get("action"),
                    "component": decision.get("component"),
                },
                "metrics_after": {},
            })
        except Exception as e:
            log.warning("Governor finalize failed: %s", e)
            pass