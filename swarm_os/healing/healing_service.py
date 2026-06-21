import logging
from .anomaly_tracker import AnomalyTracker
from .failure_detector import FailureDetector
from .recovery_engine import RecoveryEngine
from .rollback_manager import RollbackManager
from .healing_events import HealingEvent

logger = logging.getLogger(__name__)

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

    async def run_once(self):
        logger.info("Running routine healing check.")
        checks = self.detector.check()
        failed = [name for name, r in checks.items() if not r.get('ok', False)]
        if not failed:
            logger.debug("All systems healthy. No healing needed.")
            return {'status': 'healthy', 'message': 'All systems healthy. No healing needed.', 'checks': checks, 'healed': []}
        
        healed = []
        for name in failed:
            try:
                logger.warning(f"Failure detected in {name}. Attempting recovery.")
                anomaly = self.tracker.record(name, 'warning', f'{name} check failed', {})
                result = await self.engine.recover(anomaly)
                healed.append({'service': name, 'ok': result.get('ok', False), 'detail': result.get('message', '')})
            except Exception as exc:
                logger.error(f"Recovery failed for {name}: {exc}", exc_info=True)
                healed.append({'service': name, 'ok': False, 'detail': str(exc)})
        
        return {'status': 'healed' if all(h['ok'] for h in healed) else 'partial', 'message': f'Attempted recovery on {len(healed)} service(s).', 'checks': checks, 'healed': healed}

    async def heal(self, source, action="recover", **payload):
        logger.info(f"Initiating manual heal for {source} (Action: {action})")
        anomaly = self.tracker.record(source, "warning", payload.get("reason", "failure detected"), payload)
        result = await self.engine.recover(anomaly)
        
        success = bool(result.get("ok", False))
        if success:
            logger.info(f"Successfully healed {source}.")
        else:
            logger.error(f"Manual heal failed for {source}.")

        event = HealingEvent.build(
            "healing_attempt",
            source,
            action,
            success,
            int(result.get("duration_ms", 0) or 0),
            anomaly=anomaly,
            result=result
        )
        return {"anomaly": anomaly, "result": result, "event": event}
