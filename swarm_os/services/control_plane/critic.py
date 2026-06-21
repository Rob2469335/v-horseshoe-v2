from __future__ import annotations

from typing import Any

from .models import CriticResult

class Critic:
    def evaluate_step(self, result: Any, expected_kind: str) -> CriticResult:
        """Evaluates execution outputs against structural contract rules."""
        if expected_kind == "tool":
            if isinstance(result, dict):
                if not result.get("ok", True) or "error" in result:
                    return CriticResult(
                        accepted=False,
                        score=0.1,
                        reason=f"Tool error: {result.get('error', 'unspecified error')}",
                        retryable=True,
                    )
                # If there are entries/matches and they are empty, or surgical errors
                if "entries" in result and not result["entries"] and result.get("path"):
                    return CriticResult(
                        accepted=False,
                        score=0.3,
                        reason="Directory listing returned no entries.",
                        retryable=True,
                    )
            return CriticResult(
                accepted=True,
                score=0.9,
                reason="Tool execution returned success.",
                retryable=False,
            )
        return CriticResult(
            accepted=True,
            score=0.6,
            reason="non-tool stub-eval",
            retryable=True,
        )


