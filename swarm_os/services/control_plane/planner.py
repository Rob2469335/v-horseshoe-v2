from __future__ import annotations

from .models import PlanStep

class Planner:
    def make_plan(self, task: str, context: dict | None = None) -> list[PlanStep]:
        """Generates a structured execution plan containing PlanStep objects."""
        return [
            PlanStep(
                step_id="step-1",
                kind="analyze",
                goal=task,
                assigned_to="none",
                metadata=dict(context or {}),
            )
        ]

