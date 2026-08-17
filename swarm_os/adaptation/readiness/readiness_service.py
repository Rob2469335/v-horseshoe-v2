from __future__ import annotations


class HealingReadinessService:
    def __init__(
        self, metrics=None, audit=None, escalation=None, runbooks=None
    ) -> None:
        self.metrics = metrics
        self.audit = audit
        self.escalation = escalation
        self.runbooks = runbooks

    def calculate(self) -> dict:
        # Simple readiness score derived from metrics and escalations
        metrics_snapshot = self.metrics.snapshot() if self.metrics else {"totals": {}}
        totals = metrics_snapshot.get("totals", {})
        verified_failures = totals.get("verified_failure", 0)
        escalations = totals.get("escalations", 0)

        # gentle weighting so small issues don't drop below ok
        score = 100 - (verified_failures * 2) - (escalations * 1)
        score = max(0, min(100, int(score)))

        status = "ok" if score >= 50 else "degraded"
        if score >= 80:
            rating = "high"
        elif score >= 50:
            rating = "moderate"
        else:
            rating = "low"

        return {
            "status": status,
            "score": score,
            "rating": rating,
            "factors": {
                "verified_failures": verified_failures,
                "escalations": escalations,
            },
            "details": {
                "metrics": metrics_snapshot,
                "audit_count": len(self.audit.recent()) if self.audit else 0,
                "escalations": len(self.escalation.recent()) if self.escalation else 0,
            },
        }
