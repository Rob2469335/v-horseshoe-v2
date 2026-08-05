from __future__ import annotations

from typing import Dict, Any, Optional


class Reviewer:
    """Reviewer runs validation after a repair: health checks and optional test suite.
    If validation fails, it triggers rollback via a rollback manager.
    """

    def __init__(self, rollback_manager: Optional[object] = None, detector: Optional[object] = None):
        self.rollback_manager = rollback_manager
        self.detector = detector

    def validate(self, before_metrics: Dict[str, Any], after_probe_callable=None) -> Dict[str, Any]:
        """Run validation checks. after_probe_callable should return metrics dict when called.
        Returns dict {ok: bool, details: {...}}
        """
        # run quick probe (use detector if provided)
        try:
            if after_probe_callable:
                after = after_probe_callable()
            elif self.detector:
                from .failure_detector import run_coro_sync
                after = run_coro_sync(self.detector.check())
            else:
                after = {}
        except Exception as exc:
            return {"ok": False, "reason": str(exc), "after": {}}

        # basic validation: ensure health_score improved or remained
        before_score = before_metrics.get("health_score") if isinstance(before_metrics, dict) else None
        after_score = after.get("health_score") if isinstance(after, dict) else None
        details = {"before": before_metrics, "after": after}

        if before_score is None or after_score is None:
            # unable to compare, assume failure to be safe
            return {"ok": False, "reason": "missing_metric", "details": details}

        # pass if after_score >= before_score
        if after_score >= before_score:
            return {"ok": True, "details": details}
        # otherwise attempt rollback
        rb = None
        if self.rollback_manager:
            try:
                snapshot = self.rollback_manager.latest_snapshot()
                if snapshot:
                    rb = self.rollback_manager.restore(snapshot)
            except Exception:
                rb = None
        return {"ok": False, "details": details, "rollback": rb}
