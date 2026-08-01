import asyncio
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
        # BUG FIX: Lock to prevent concurrent run_once() and heal() racing on the same component
        self._recovery_lock = asyncio.Lock()
        # BUG FIX: Track real heal outcomes instead of fabricating metrics
        self._heals_total = 0
        self._heals_success = 0
        self._last_heal_success = None

    async def status(self):
        report = await self.detector.check()
        checks = report.get("raw", {})
        active = [name for name, result in checks.items() if isinstance(result, dict) and not result.get("ok", False)]
        return {
            "recovery_readiness": 100 if not active else max(0, 100 - (25 * len(active))),
            "active_anomalies": len(self.tracker.list()),
            "rollback_paths": 1 if self.rollback.latest_snapshot() is not None else 0,
            "last_heal_success": self._last_heal_success,
            "heals_today": self._heals_success,
            "heals_total": self._heals_total,
            "checks": checks,
            "health_score": report.get("health_score", 100),
            "signals": report.get("signals", []),
            "anomalies": self.tracker.list(),
        }

    def _record_heal(self, success: bool):
        self._heals_total += 1
        if success:
            self._heals_success += 1
        self._last_heal_success = success

    async def run_once(self):
        logger.info("Running routine healing check.")
        report = await self.detector.check()
        checks = report.get("raw", {})
        failed = [name for name, r in checks.items() if isinstance(r, dict) and not r.get("ok", False)]
        if not failed:
            logger.debug("All systems healthy. No healing needed.")
            return {
                "status": "healthy",
                "message": "All systems healthy. No healing needed.",
                "checks": checks,
                "health_score": report.get("health_score", 100),
                "signals": report.get("signals", []),
                "healed": []
            }

        healed = []
        for name in failed:
            try:
                logger.warning(f"Failure detected in {name}. Attempting recovery.")
                anomaly = self.tracker.record(name, 'warning', f'{name} check failed', {})
                async with self._recovery_lock:
                    result = await self.engine.recover(anomaly)
                ok = result.get('ok', False)
                self._record_heal(ok)
                healed.append({'service': name, 'ok': ok, 'detail': result.get('message', result.get('error', ''))})
            except Exception as exc:
                logger.error(f"Recovery failed for {name}: {exc}", exc_info=True)
                self._record_heal(False)
                healed.append({'service': name, 'ok': False, 'detail': str(exc)})

        return {
            'status': 'healed' if all(h['ok'] for h in healed) else 'partial',
            'message': f'Attempted recovery on {len(healed)} service(s).',
            'checks': checks,
            'health_score': report.get("health_score", 100),
            'signals': report.get("signals", []),
            'healed': healed
        }

    async def heal(self, source, action="recover", **payload):
        logger.info(f"Initiating manual heal for {source} (Action: {action})")
        anomaly = self.tracker.record(source, "warning", payload.get("reason", "failure detected"), payload)
        async with self._recovery_lock:
            result = await self.engine.recover(anomaly)

        success = bool(result.get("ok", False))
        self._record_heal(success)
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