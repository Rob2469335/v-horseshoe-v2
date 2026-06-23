from __future__ import annotations

from .models import PlanStep


class Planner:
    def make_plan(self, task: str, context: dict | None = None) -> list[PlanStep]:
        context = dict(context or {})
        goal = (task or "").strip()
        lowered = goal.lower()

        def step(step_id: str, kind: str, assigned_to: str, goal_text: str, **meta) -> PlanStep:
            merged = dict(context)
            merged.update(meta)
            return PlanStep(
                step_id=step_id,
                kind=kind,
                goal=goal_text,
                assigned_to=assigned_to,
                metadata=merged,
            )

        is_code = any(k in lowered for k in (
            "code", "bug", "fix", "patch", "refactor", "implement", "function",
            "class", "api", "endpoint", "python", ".py", "javascript", "typescript"
        ))
        is_research = any(k in lowered for k in (
            "research", "investigate", "find", "compare", "docs", "documentation",
            "qdrant", "api", "library", "package", "web"
        ))
        is_review = any(k in lowered for k in (
            "review", "audit", "validate", "check", "verify", "critic", "quality", "test"
        ))
        is_plan = any(k in lowered for k in (
            "plan", "strategy", "design", "architecture", "roadmap"
        ))

        steps: list[PlanStep] = []

        steps.append(step(
            "step-1",
            "plan",
            "planner",
            goal,
            phase="planning",
            intent="decompose-task",
        ))

        if is_research or not is_code:
            steps.append(step(
                "step-2",
                "analyze",
                "researcher",
                f"Research relevant context for: {goal}",
                phase="research",
                intent="gather-context",
            ))

        if is_code:
            steps.append(step(
                "step-3",
                "analyze",
                "coder",
                f"Produce or patch code for: {goal}",
                phase="implementation",
                intent="write-code",
            ))
        else:
            steps.append(step(
                "step-3",
                "tool",
                "tool-runner",
                f"Execute required tools for: {goal}",
                phase="execution",
                intent="run-tools",
            ))

        steps.append(step(
            "step-4",
            "synthesize",
            "reviewer",
            f"Review outputs for: {goal}",
            phase="review",
            intent="quality-check",
        ))

        if is_plan and not is_code and not is_research:
            steps.append(step(
                "step-5",
                "synthesize",
                "coordinator",
                f"Produce final coordinated response for: {goal}",
                phase="handoff",
                intent="final-coordination",
            ))
        else:
            steps.append(step(
                "step-5",
                "synthesize",
                "executor",
                f"Finalize execution result for: {goal}",
                phase="finalize",
                intent="deliver-result",
            ))

        if is_review and is_code:
            steps.insert(3, step(
                "step-3b",
                "tool",
                "tool-runner",
                f"Run validation tools for: {goal}",
                phase="validation",
                intent="run-checks",
            ))

        return steps
