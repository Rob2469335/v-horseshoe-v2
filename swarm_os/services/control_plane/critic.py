from __future__ import annotations

from typing import Any, Dict

from .models import CriticResult


class Critic:
    def evaluate_step(self, *, result: Dict[str, Any], expected_kind: str = "") -> CriticResult:
        content = str(result.get("content", "") or "")
        finish_reason = str(result.get("finish_reason", "") or "")
        error = result.get("error")

        if error:
            return CriticResult(False, 0.0, f"error:{error}", retryable=True)

        if not content.strip():
            return CriticResult(False, 0.1, "empty_content", retryable=True)

        if finish_reason in {"timeout", "error"}:
            return CriticResult(False, 0.2, f"bad_finish:{finish_reason}", retryable=True)

        score = 0.8
        if len(content.strip()) > 80:
            score = 0.9

        return CriticResult(True, score, "accepted", retryable=False)
