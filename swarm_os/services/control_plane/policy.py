from __future__ import annotations

from typing import Any, Dict, Iterable

from .models import PolicyDecision


class PolicyEngine:
    def __init__(
        self,
        *,
        allowed_tools: Iterable[str] | None = None,
        approval_tools: Iterable[str] | None = None,
        max_steps: int = 12,
    ) -> None:
        self.allowed_tools = set(allowed_tools or [])
        self.approval_tools = set(approval_tools or [])
        self.max_steps = max_steps

    def check_tool(self, tool_name: str) -> PolicyDecision:
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return PolicyDecision(False, f"tool_not_allowed:{tool_name}")
        if tool_name in self.approval_tools:
            return PolicyDecision(True, f"approval_required:{tool_name}", requires_approval=True)
        return PolicyDecision(True, "allowed")

    def check_step_budget(self, step_count: int) -> PolicyDecision:
        if step_count > self.max_steps:
            return PolicyDecision(False, "step_budget_exceeded")
        return PolicyDecision(True, "within_budget")

    def check_action(self, action: str, metadata: Dict[str, Any] | None = None) -> PolicyDecision:
        metadata = metadata or {}
        if metadata.get("irreversible", False):
            return PolicyDecision(True, "approval_required:irreversible_action", requires_approval=True)
        return PolicyDecision(True, f"allowed:{action}")
