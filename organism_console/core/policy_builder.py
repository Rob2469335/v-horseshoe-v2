from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PolicyState:
    goal: str
    success_memories: list[dict[str, Any]] = field(default_factory=list)
    failure_memories: list[dict[str, Any]] = field(default_factory=list)
    pattern_memories: list[dict[str, Any]] = field(default_factory=list)
    heuristic_memories: list[dict[str, Any]] = field(default_factory=list)
    constraint_memories: list[dict[str, Any]] = field(default_factory=list)
    policy_memories: list[dict[str, Any]] = field(default_factory=list)
    failure_density: float = 0.0
    success_density: float = 0.0
    preferred_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    tool_bias_vector: dict[str, float] = field(default_factory=dict)
    constraint_rules: list[dict[str, Any]] = field(default_factory=list)


class PolicyBuilder:
    def build(self, goal: str, memories: dict[str, Any]) -> PolicyState:
        success_memories = memories.get("success_memories", [])
        failure_memories = memories.get("failure_memories", [])
        pattern_memories = memories.get("pattern_memories", [])
        heuristic_memories = memories.get("heuristic_memories", [])
        constraint_memories = memories.get("constraint_memories", [])
        policy_memories = memories.get("policy_memories", [])

        total = max(len(success_memories) + len(failure_memories), 1)
        failure_density = round(len(failure_memories) / total, 3)
        success_density = round(len(success_memories) / total, 3)

        positive_counts: dict[str, float] = {}
        negative_counts: dict[str, float] = {}
        blocked_tools: set[str] = set()
        constraints: list[str] = []
        constraint_rules: list[dict[str, Any]] = []

        for bucket in [
            success_memories,
            pattern_memories,
            heuristic_memories,
            policy_memories,
        ]:
            for m in bucket:
                weight = max(float(m.get("importance", 0.1)), 0.1)
                for tool in m.get("tool_sequence", []):
                    positive_counts[tool] = round(
                        positive_counts.get(tool, 0.0) + weight, 3
                    )

        for bucket in [failure_memories, constraint_memories]:
            for m in bucket:
                weight = max(float(m.get("importance", 0.1)), 0.1)
                summary = str(m.get("summary", ""))
                for tool in m.get("tool_sequence", []):
                    negative_counts[tool] = round(
                        negative_counts.get(tool, 0.0) + weight, 3
                    )
                    if negative_counts[tool] >= 0.25:
                        blocked_tools.add(tool)
                        rule = {
                            "type": "avoid_tool",
                            "tool_name": tool,
                            "reason": "constraint memory",
                            "summary": summary,
                        }
                        if rule not in constraint_rules:
                            constraint_rules.append(rule)

        tool_bias_vector: dict[str, float] = {}
        all_tools = set(positive_counts.keys()) | set(negative_counts.keys())
        for tool in all_tools:
            tool_bias_vector[tool] = round(
                positive_counts.get(tool, 0.0) - negative_counts.get(tool, 0.0), 3
            )

        preferred_tools = [
            tool
            for tool, score in sorted(
                tool_bias_vector.items(), key=lambda kv: kv[1], reverse=True
            )
            if score > 0
        ]

        for cm in constraint_memories:
            summary = str(cm.get("summary", "")).strip()
            if summary:
                constraints.append(summary)

        for pm in policy_memories:
            summary = str(pm.get("summary", "")).strip()
            if summary:
                constraints.append(summary)

        return PolicyState(
            goal=goal,
            success_memories=success_memories,
            failure_memories=failure_memories,
            pattern_memories=pattern_memories,
            heuristic_memories=heuristic_memories,
            constraint_memories=constraint_memories,
            policy_memories=policy_memories,
            failure_density=failure_density,
            success_density=success_density,
            preferred_tools=preferred_tools,
            blocked_tools=sorted(blocked_tools),
            constraints=constraints,
            tool_bias_vector=tool_bias_vector,
            constraint_rules=constraint_rules,
        )
