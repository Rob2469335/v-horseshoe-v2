from __future__ import annotations

from typing import Any, Dict, List

from .models import PlanStep


class Planner:
    def make_plan(self, task: str, context: Dict[str, Any] | None = None) -> List[PlanStep]:
        context = context or {}
        text = (task or "").strip()
        lower = text.lower()
        context_keys = sorted(context.keys())

        if not text:
            return [
                PlanStep(
                    step_id="step-1",
                    kind="noop",
                    goal="no task provided",
                    assigned_to="orchestrator",
                    metadata={"context_keys": context_keys},
                )
            ]

        tool_terms = [
            "run", "execute", "call", "fetch", "download", "open", "invoke"
        ]
        coding_terms = [
            "code", "python", "powershell", "script", "function", "class",
            "bug", "fix", "refactor", "implement", "build", "fastapi"
        ]
        research_terms = [
            "research", "find", "search", "look up", "investigate", "compare",
            "analyze", "explain", "summarize", "review"
        ]

        def has_any(terms: List[str]) -> bool:
            return any(term in lower for term in terms)

        if has_any(tool_terms):
            return [
                PlanStep(
                    step_id="step-1",
                    kind="tool",
                    goal=f"Execute tool action for: {text}",
                    assigned_to="tool-runtime",
                    metadata={"context_keys": context_keys},
                ),
                PlanStep(
                    step_id="step-2",
                    kind="analyze",
                    goal=f"Review tool results for: {text}",
                    assigned_to="orchestrator",
                    metadata={"context_keys": context_keys},
                ),
                PlanStep(
                    step_id="step-3",
                    kind="complete",
                    goal=f"Finalize task: {text}",
                    assigned_to="orchestrator",
                    metadata={"context_keys": context_keys},
                ),
            ]

        if has_any(coding_terms):
            return [
                PlanStep(
                    step_id="step-1",
                    kind="analyze",
                    goal=f"Analyze implementation task: {text}",
                    assigned_to="orchestrator",
                    metadata={"context_keys": context_keys},
                ),
                PlanStep(
                    step_id="step-2",
                    kind="synthesize",
                    goal=f"Produce implementation for: {text}",
                    assigned_to="orchestrator",
                    metadata={"context_keys": context_keys},
                ),
            ]

        if has_any(research_terms):
            return [
                PlanStep(
                    step_id="step-1",
                    kind="retrieve",
                    goal=f"Gather relevant information for: {text}",
                    assigned_to="tool-runtime",
                    metadata={"context_keys": context_keys},
                ),
                PlanStep(
                    step_id="step-2",
                    kind="analyze",
                    goal=f"Analyze findings for: {text}",
                    assigned_to="orchestrator",
                    metadata={"context_keys": context_keys},
                ),
                PlanStep(
                    step_id="step-3",
                    kind="synthesize",
                    goal=f"Synthesize response for: {text}",
                    assigned_to="orchestrator",
                    metadata={"context_keys": context_keys},
                ),
            ]

        return [
            PlanStep(
                step_id="step-1",
                kind="analyze",
                goal=f"Analyze task: {text}",
                assigned_to="orchestrator",
                metadata={"context_keys": context_keys},
            ),
            PlanStep(
                step_id="step-2",
                kind="synthesize",
                goal=f"Synthesize response for: {text}",
                assigned_to="orchestrator",
                metadata={"context_keys": context_keys},
            ),
        ]
