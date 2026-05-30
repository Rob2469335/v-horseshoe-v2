from __future__ import annotations

from typing import Any, Dict, List

from .models import PlanStep


class Planner:
    def make_plan(self, task: str, context: Dict[str, Any] | None = None) -> List[PlanStep]:
        context = context or {}
        if not task.strip():
            return [PlanStep(step_id="step-1", kind="noop", goal="no task provided")]

        return [
            PlanStep(
                step_id="step-1",
                kind="analyze",
                goal=task,
                assigned_to="orchestrator",
                metadata={"context_keys": sorted(context.keys())},
            )
        ]
