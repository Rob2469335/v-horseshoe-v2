"""
Module: decision_gate
Order: 17
Package: governance.constraints
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class GateDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class DecisionGate:
    def evaluate(self, action: str, context: dict) -> GateDecision:
        requires_approval = bool(context.get("requires_human_approval", False))
        if requires_approval:
            return GateDecision(allowed=False, reasons=[f"action {action} requires human approval"])
        return GateDecision(allowed=True, reasons=[])
