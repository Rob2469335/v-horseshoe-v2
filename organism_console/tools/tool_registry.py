from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from qdrant_client import QdrantClient

from .memory_retriever import MemoryRetriever

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: ToolHandler
    timeout_s: float = 30.0
    retry_limit: int = 1
    idempotent: bool = True
    emits_events: bool = True
    tags: list[str] = field(default_factory=list)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())


registry = ToolRegistry()
memory_retriever = MemoryRetriever(QdrantClient(url="http://127.0.0.1:6333"))


def call_plan_api(args: dict[str, Any]) -> dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    policy_state = args.get("policy_state", {})
    failure_density = float(policy_state.get("failure_density", 0.0))
    tool_bias = policy_state.get("tool_bias_vector", {})
    blocked_tools = set(policy_state.get("blocked_tools", []))

    candidate_steps: list[dict[str, Any]] = []

    if goal:
        candidate_steps.append({
            "step_id": "step-1",
            "tool_name": "search_memory",
            "purpose": "retrieve prior context",
            "arguments": {"query": goal, "limit": 5}
        })

        candidate_steps.append({
            "step_id": "step-2",
            "tool_name": "generate",
            "purpose": "produce answer or action",
            "arguments": {"goal": goal}
        })

        if failure_density >= 0.5:
            candidate_steps.insert(0, {
                "step_id": "step-0",
                "tool_name": "search_memory",
                "purpose": "healing retrieval from failure memories",
                "arguments": {"query": goal, "limit": 5}
            })

    filtered: list[dict[str, Any]] = []
    next_id = 1

    for step in candidate_steps:
        tool = step["tool_name"]
        if tool in blocked_tools:
            continue
        if tool_bias.get(tool, 0.0) <= -0.4:
            continue

        step["step_id"] = f"step-{next_id}"
        filtered.append(step)
        next_id += 1

    if not filtered and "generate" not in blocked_tools:
        filtered.append({
            "step_id": "step-1",
            "tool_name": "generate",
            "purpose": "fallback direct generation due to constraints",
            "arguments": {"goal": goal}
        })

    return {
        "goal": goal,
        "policy_state": policy_state,
        "constraints_applied": list(blocked_tools),
        "steps": filtered,
    }


def call_generate_api(args: dict[str, Any]) -> dict[str, Any]:
    goal = str(args.get("goal", "")).strip()
    memory = args.get("memory")

    if isinstance(memory, dict) and memory.get("matches"):
        summaries = [str(m.get("summary", "")) for m in memory.get("matches", [])[:3] if m.get("summary")]
        joined = " | ".join(summaries) if summaries else "No summary available"
        top = memory["matches"][0]
        return {
            "text": f"Goal: {goal}`nUsing memories: {joined}",
            "used_memory": True,
            "memory_score": top.get("score"),
            "memory_count": len(memory.get("matches", [])),
        }

    return {
        "text": f"Goal: {goal}`nNo relevant memory found.",
        "used_memory": False,
        "memory_count": 0,
    }


def qdrant_query(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    return memory_retriever.search(query=query, limit=int(args.get("limit", 5)))


def register_builtin_tools() -> None:
    registry.register(ToolSpec(
        name="plan",
        description="Create an execution plan using memory-weighted constraints and tool bias.",
        handler=call_plan_api,
        tags=["planning", "memory-guided", "constraints"]
    ))
    registry.register(ToolSpec(
        name="generate",
        description="Generate an answer or artifact from a goal.",
        handler=call_generate_api,
        tags=["generation"]
    ))
    registry.register(ToolSpec(
        name="search_memory",
        description="Query episodic and semantic memory.",
        handler=qdrant_query,
        tags=["memory", "qdrant", "semantic"]
    ))
