"""
Module: promotion_engine
Order: 13
Package: adaptation.promotion
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from swarm_os.cognition.evaluation.evaluator import EvaluationResult
from swarm_os.foundation.memory.promotion_record import PromotionRecord


class PromotionEngine:
    def consider(self, experiment_id: str, candidate: str, baseline: str, result: EvaluationResult) -> PromotionRecord | None:
        if not result.passed:
            return None

        return PromotionRecord(
            experiment_id=experiment_id,
            promoted_from=baseline,
            promoted_to=candidate,
            rationale=f"candidate passed with score {result.score:.3f}",
        )