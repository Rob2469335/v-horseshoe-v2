from __future__ import annotations

from .models import StepBudget

class PolicyEngine:
    def __init__(self, max_steps: int = 12) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self.max_steps = max_steps
        self.steps_used = 0

    def check_step_budget(self, steps: int = 1) -> StepBudget:
        if steps < 1:
            return StepBudget(allowed=False, reason="invalid_step_request")
        if self.steps_used + steps > self.max_steps:
            return StepBudget(allowed=False, reason="step_budget_exceeded")
        self.steps_used += steps
        return StepBudget(allowed=True, reason="ok")

    def modify_budget(self, new_max_steps: int) -> None:
        if new_max_steps < 1:
            raise ValueError("new_max_steps must be >= 1")
        if new_max_steps < self.steps_used:
            raise ValueError("new_max_steps cannot be lower than steps_used")
        self.max_steps = new_max_steps

    def reset(self) -> None:
        self.steps_used = 0
