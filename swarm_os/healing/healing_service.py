from .anomaly_tracker import AnomalyTracker
from .failure_detector import FailureDetector
from .recovery_engine import RecoveryEngine
from .rollback_manager import RollbackManager
from .healing_events import HealingEvent

class HealingService:
    def __init__(self, detector=None, tracker=None, engine=None, rollback=None):
        self.detector = detector or FailureDetector()
        self.tracker = tracker or AnomalyTracker()
        self.engine = engine or RecoveryEngine()
        self.rollback = rollback or RollbackManager()

    def status(self):
        checks = self.detector.check()
        active = [name for name, result in checks.items() if not result.get("ok", False)]
        return {
            "recovery_readiness": 100 if not active else max(0, 100 - (25 * len(active))),
            "active_anomalies": len(self.tracker.list()),
            "rollback_paths": 1 if self.rollback.latest_snapshot() is not None else 0,
            "last_heal_success": True,
            "heals_today": 0,
            "checks": checks,
            "anomalies": self.tracker.list(),
        }

    async def heal(self, source, action="recover", **payload):
        anomaly = self.tracker.record(source, "warning", payload.get("reason", "failure detected"), payload)
        result = await self.engine.recover(anomaly)
        event = HealingEvent.build(
            "healing_attempt",
            source,
            action,
            bool(result.get("ok", False)),
            int(result.get("duration_ms", 0) or 0),
            anomaly=anomaly,
            result=result
        )
        return {"anomaly": anomaly, "result": result, "event": event}
