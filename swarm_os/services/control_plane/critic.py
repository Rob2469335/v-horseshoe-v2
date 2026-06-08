from __future__ import annotations

from typing import Any

from .models import CriticResult

class Critic:
    def evaluate_step(self, result: Any, expected_kind: str) -> CriticResult:
        """Evaluates execution outputs against structural contract rules."""
        return CriticResult(
            accepted=True,
            score=0.6,
            reason="stub-eval",
            retryable=True,
        )

