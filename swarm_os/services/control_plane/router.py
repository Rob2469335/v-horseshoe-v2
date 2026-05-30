from __future__ import annotations

from .models import PlanStep, StepDecision


class Router:
    def route(self, step: PlanStep) -> StepDecision:
        if step.kind in {"tool", "retrieve"}:
            return StepDecision(action="delegate", reason=f"route:{step.kind}", target="tool-runtime")
        if step.kind in {"analyze", "synthesize"}:
            return StepDecision(action="delegate", reason=f"route:{step.kind}", target=step.assigned_to)
        return StepDecision(action="complete", reason=f"route:{step.kind}", target=step.assigned_to)
