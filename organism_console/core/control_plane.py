from __future__ import annotations

from time import time
from typing import Any
from uuid import uuid4

from qdrant_client import QdrantClient

from .event_bus import event_bus
from .event_models import RuntimeEvent, EventType, EventStatus
from .memory_retriever import MemoryRetriever
from .memory_synthesizer import MemorySynthesizer
from .policy_builder import PolicyBuilder
from .tool_registry import registry


class ControlPlane:
    def __init__(self) -> None:
        client = QdrantClient(url="http://127.0.0.1:6333")
        self.memory_store = MemoryRetriever(client)
        self.policy_builder = PolicyBuilder()
        self.synth = MemorySynthesizer(client)

    def _emit(self, trace_id: str, event_type: str, source: str, target: str, status: str, payload: dict[str, Any]) -> None:
        event = RuntimeEvent(
            trace_id=trace_id,
            parent_event_id=None,
            event_type=EventType(event_type),
            source=source,
            target=target,
            status=EventStatus(status),
            payload=payload,
            timestamp_ms=int(time() * 1000),
        )
        event_bus.publish(event)

    def _execute_tool(self, trace_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._emit(trace_id, "tool_called", "control_plane", tool_name, "pending", {"arguments": arguments})
        tool = registry.get(tool_name)

        try:
            output = tool.handler(arguments)
            result = {
                "tool_name": tool_name,
                "ok": True,
                "output": output,
                "error": None,
                "duration_ms": 0.0,
            }
            self._emit(trace_id, "tool_result", "control_plane", tool_name, "success", result)
            return result
        except Exception as e:
            result = {
                "tool_name": tool_name,
                "ok": False,
                "output": None,
                "error": str(e),
                "duration_ms": 0.0,
            }
            self._emit(trace_id, "tool_result", "control_plane", tool_name, "failed", result)
            return result

    def create_plan(self, trace_id: str, goal: str, healing: bool = False) -> dict[str, Any]:
        memories = self.memory_store.collect_memories(goal)
        policy_state = self.policy_builder.build(goal, memories)

        if healing:
            policy_state.failure_density = max(policy_state.failure_density, 0.7)
            if "generate" not in policy_state.preferred_tools:
                policy_state.preferred_tools.append("generate")

        plan_args = {
            "goal": goal,
            "policy_state": {
                "goal": policy_state.goal,
                "success_memories": policy_state.success_memories,
                "failure_memories": policy_state.failure_memories,
                "failure_density": policy_state.failure_density,
                "success_density": policy_state.success_density,
                "preferred_tools": policy_state.preferred_tools,
                "blocked_tools": policy_state.blocked_tools,
                "constraints": policy_state.constraints,
                "tool_bias_vector": policy_state.tool_bias_vector,
                "constraint_rules": policy_state.constraint_rules,
            },
        }

        plan_result = self._execute_tool(trace_id, "plan", plan_args)
        steps = plan_result["output"]["steps"] if plan_result["ok"] else []

        self._emit(
            trace_id,
            "plan_created",
            "control_plane",
            "executor",
            "success",
            {
                "trace_id": trace_id,
                "goal": goal,
                "steps": steps,
                "planner": "policy_state_planner",
                "healing": healing,
                "policy_state": plan_args["policy_state"],
            },
        )

        return {
            "goal": goal,
            "policy_state": plan_args["policy_state"],
            "steps": steps,
        }

    def run(self, goal: str) -> dict[str, Any]:
        trace_id = str(uuid4())
        max_replans = 1
        replan_count = 0
        final_steps = []
        final_results = []
        final_evaluation = {}
        healing_plan = None

        self._emit(trace_id, "request_received", "api", "control_plane", "success", {"goal": goal})

        while True:
            active_goal = goal if replan_count == 0 else f"{goal} (repair attempt {replan_count})"
            plan = self.create_plan(trace_id=trace_id, goal=active_goal, healing=(replan_count > 0))
            steps = plan["steps"]

            results = []

            for step in steps:
                args = dict(step.get("arguments", {}))
                result = self._execute_tool(trace_id, step["tool_name"], args)
                results.append({
                    "step_id": step["step_id"],
                    "tool_name": step["tool_name"],
                    "result": result,
                })

            failed = any(not r["result"]["ok"] for r in results)
            score = 0.4 if failed else 1.0

            evaluation = {
                "trace_id": trace_id,
                "score": score,
                "verdict": "needs_healing" if failed else "healthy",
                "reasons": ["failure detected"] if failed else ["ok"],
                "replan_count": replan_count,
            }

            final_steps = steps
            final_results = results
            final_evaluation = evaluation

            if failed and replan_count < max_replans:
                replan_count += 1
                self._emit(
                    trace_id,
                    "healing_triggered",
                    "control_plane",
                    "worker",
                    "pending",
                    {"reason": evaluation, "repair_attempt": replan_count},
                )
                healing_plan = {
                    "goal": f"{goal} (repair attempt {replan_count})",
                    "repair_attempt": replan_count,
                }
                continue

            break

        synth = self.synth.synthesize_trace(
            trace_id=trace_id,
            replan_count=replan_count,
            was_healed=(replan_count > 0 and final_evaluation.get("verdict") == "healthy"),
        )

        self._emit(
            trace_id,
            "memory_written",
            "control_plane",
            "qdrant",
            "success",
            {"trace_id": trace_id, "memory": synth},
        )

        response = {
            "trace_id": trace_id,
            "goal": goal,
            "steps": final_steps,
            "results": final_results,
            "evaluation": final_evaluation,
            "events": event_bus.list_events(trace_id),
        }

        if healing_plan is not None:
            response["healing_plan"] = healing_plan

        return response
