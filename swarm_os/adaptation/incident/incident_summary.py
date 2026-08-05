from __future__ import annotations

from typing import Dict, Any


class IncidentSummaryService:
    def __init__(self, metrics=None, audit=None, escalation=None) -> None:
        self.metrics = metrics
        self.audit = audit
        self.escalation = escalation

    def build_summary(self) -> Dict[str, Any]:
        totals = self.metrics.snapshot().get('totals', {}) if self.metrics else {}
        recent_escalations = len(self.escalation.recent()) if self.escalation else 0
        recent_failed = totals.get('verified_failure', 0)
        top_components = []
        # derive top failing components from audit events
        if self.audit:
            events = self.audit.recent(50)
            comp_counts: dict[str, int] = {}
            for e in events:
                if not e.get('verified', True):
                    comp_counts[e.get('component')] = comp_counts.get(e.get('component'), 0) + 1
            top_components = sorted(
                [{'component': k, 'count': v} for k, v in comp_counts.items()],
                key=lambda x: x['count'],
                reverse=True,
            )
        return {
            'status': 'ok',
            'recent_escalation_count': recent_escalations,
            'recent_failed_verification_count': recent_failed,
            'top_failing_components': top_components,
        }

