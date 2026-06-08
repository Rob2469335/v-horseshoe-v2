"""
Module: evaluator
Order: 11
Package: cognition.evaluation
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class EvaluationResult:
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)


class Evaluator:
    def evaluate(self, metrics: dict[str, float], threshold: float = 0.5) -> EvaluationResult:
        score = float(metrics.get("score", 0.0))
        passed = score >= threshold
        reasons = [] if passed else [f"score {score:.3f} below threshold {threshold:.3f}"]
        return EvaluationResult(passed=passed, score=score, reasons=reasons)