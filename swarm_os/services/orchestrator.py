from __future__ import annotations

import logging
import json

import httpx

from ..core.settings import get_settings
from ..domain.models import SwarmJob, SwarmNode
from ..domain.policies import can_accept_job, score_node
from ..events.envelope import EventEnvelope
from ..events.store import EventStore
from ..infra.ollama import OllamaClient
from ..services.simulation_service import SimulationService
from ..services.control_plane import TraceCollector, PolicyEngine, Critic, Planner, Router, StateManager
from ..config.settings import settings as swarm_settings

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.events = EventStore(s.events_dir)
        self.ollama = OllamaClient()
        self.simulation = SimulationService(generate_fn=self.generate)
        self.trace = TraceCollector()
        self.policy = PolicyEngine(max_steps=12)
        self.critic = Critic()
        self.planner = Planner()
        self.router = Router()
        self.state_manager = StateManager()
        self.swarm_base_url = swarm_settings.swarm_url
        self.swarm_timeout = swarm_settings.swarm_timeout

        self.last_swarm_stats = {
            "status": "idling",
            "population_size": 0,
            "best_fitness": 0.0,
            "best_agent_id": "none",
            "active_generation": 0,
        }

    def get_recent_traces(self, limit: int = 50) -> list[dict]:
        events = self.trace.events()
        if limit <= 0:
            return []
        sliced = events[-limit:]
        return [
            {
                "trace_id": e.trace_id,
                "step_id": e.step_id,
                "phase": e.phase,
                "actor": e.actor,
                "action": e.action,
                "status": e.status,
                "duration_ms": e.duration_ms,
                "model": e.model,
                "tokens": e.tokens,
                "cost": e.cost,
                "summary": e.summary,
                "metadata": dict(e.metadata),
            }
            for e in sliced
        ]

    def clear_traces(self) -> None:
        self.trace.clear()

    def plan_task(self, task: str, context: dict | None = None) -> list[dict]:
        trace_id = self.trace.new_trace_id()
        context = context or {}
        self.trace.add(
            trace_id=trace_id,
            step_id="plan",
            phase="planner",
            actor="planner",
            action="plan_task",
            status="started",
            summary=task[:120],
            metadata={"context_keys": sorted(context.keys())},
        )
        plan = self.planner.make_plan(task, context)
        self.trace.add(
            trace_id=trace_id,
            step_id="plan",
            phase="planner",
            actor="planner",
            action="plan_task",
            status="completed",
            summary=f"planned:{len(plan)}",
            metadata={"step_ids": [step.step_id for step in plan]},
        )
        return [
            {
                "step_id": step.step_id,
                "kind": step.kind,
                "goal": step.goal,
                "assigned_to": step.assigned_to,
                "metadata": dict(step.metadata),
            }
            for step in plan
        ]

    def route_task(self, task: str, context: dict | None = None) -> dict:
        trace_id = self.trace.new_trace_id()
        context = context or {}
        self.trace.add(
            trace_id=trace_id,
            step_id="route",
            phase="router",
            actor="router",
            action="route_task",
            status="started",
            summary=task[:120],
            metadata={"context_keys": sorted(context.keys())},
        )
        plan = self.planner.make_plan(task, context)
        if not plan:
            self.trace.add(
                trace_id=trace_id,
                step_id="route",
                phase="router",
                actor="router",
                action="route_task",
                status="completed",
                summary="no_plan",
            )
            return {
                "action": "complete",
                "reason": "no_plan",
                "target": "orchestrator",
                "plan": [],
            }

        first = plan[0]
        decision = self.router.route(first)
        self.trace.add(
            trace_id=trace_id,
            step_id="route",
            phase="router",
            actor="router",
            action="route_task",
            status="completed",
            summary=decision.reason,
            metadata={"target": decision.target, "action": decision.action},
        )
        return {
            "action": decision.action,
            "reason": decision.reason,
            "target": decision.target,
            "plan": [
                {
                    "step_id": step.step_id,
                    "kind": step.kind,
                    "goal": step.goal,
                    "assigned_to": step.assigned_to,
                    "metadata": dict(step.metadata),
                }
                for step in plan
            ],
            "metadata": dict(decision.metadata),
        }

    def assign_job(self, node: SwarmNode, job: SwarmJob) -> bool:
        accepted = can_accept_job(node, job)
        event = EventEnvelope.create(
            event_type="job.assignment.checked",
            source="orchestrator",
            payload={
                "node_id": node.node_id,
                "job_id": job.job_id,
                "accepted": accepted,
                "score": score_node(node, job),
            },
        )
        self.events.append(event)
        log.info("assign_job node=%s job=%s accepted=%s", node.node_id, job.job_id, accepted)
        return accepted

    def _choose_model(self, prompt: str, requested_model: str | None = None) -> str:
        if requested_model and requested_model.strip():
            return requested_model.strip()

        p = (prompt or "").lower()

        if any(x in p for x in [
            "refactor", "python", "powershell", "bug", "traceback", "exception",
            "write code", "edit file", "patch", "function", "class", "fastapi"
        ]):
            return "qwen2.5-coder:14b-32k"

        if len(prompt) > 2500 or any(x in p for x in [
            "analyze", "compare", "design", "architecture", "plan", "reason",
            "investigate", "root cause", "debug this", "step by step"
        ]):
            return "qwen2.5:14b-instruct-32k"

        if any(x in p for x in [
            "hi", "hello", "hey", "ping", "heartbeat", "status", "smoke", "test", "thanks", "help"
        ]) or (len((prompt or "").split()) <= 4):
            return "qwen2.5:3b-instruct"

        return "qwen2.5:7b-instruct"


    def _fetch_installed_models(self) -> list[str]:
        try:
            import requests
            response = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            models = []
            for item in data.get("models", []):
                name = item.get("model") or item.get("name")
                if isinstance(name, str) and name.strip():
                    models.append(name.strip())
            return models
        except Exception:
            return []

    def _classify_model_name(self, model_name: str) -> set[str]:
        name = (model_name or "").lower()
        tags = set()

        if "coder" in name or "code" in name:
            tags.add("coding")
        if "embed" in name:
            tags.add("embedding")
        if "rerank" in name or "reranker" in name:
            tags.add("reranker")
        if "vision" in name or "vl" in name or "moondream" in name:
            tags.add("vision")
        if any(x in name for x in ["14b", "12b", "32k", "qwen3"]):
            tags.add("reasoning")
        if any(x in name for x in ["3b", "7b"]):
            tags.add("fast")
        if "instruct" in name or "mistral" in name or "qwen" in name:
            tags.add("chat")

        return tags

    def _choose_first_available(self, installed_models: list[str], candidates: list[str]) -> str | None:
        installed_lookup = {m.lower(): m for m in installed_models}
        for candidate in candidates:
            hit = installed_lookup.get(candidate.lower())
            if hit:
                return hit
        return None

    def _infer_task_type(self, prompt: str = "") -> str:
        text = (prompt or "").lower()

        coding_markers = [
            "python", "powershell", "javascript", "typescript", "fastapi", "traceback",
            "exception", "stack trace", "refactor", "function", "class ", "compile",
            "syntaxerror", "pytest", "module", "import ", "sql", "api", "json"
        ]
        if any(marker in text for marker in coding_markers):
            return "coding"

        if any(marker in text for marker in ["image", "screenshot", "diagram", "vision", "ocr", "photo"]):
            return "vision"

        if any(marker in text for marker in ["embed", "embedding", "vector"]):
            return "embedding"

        if any(marker in text for marker in ["rerank", "reranker"]):
            return "reranker"

        if any(marker in text for marker in [
            "analyze", "analysis", "compare", "design", "architecture", "plan", "reason",
            "investigate", "root cause", "debug this", "step by step"
        ]):
            return "reasoning"

        return "general"

    def _select_model_from_inventory(
        self,
        prompt: str,
        requested_model: str | None = None,
        phenotype: dict | None = None,
    ) -> dict:
        phenotype = phenotype or {}
        installed_models = self._fetch_installed_models()
        route_profile = str(phenotype.get("route_profile") or "default").strip().lower()
        task_type = self._infer_task_type(prompt)

        if requested_model:
            chosen = self._choose_first_available(installed_models, [requested_model])
            if chosen:
                return {
                    "chosen_model": chosen,
                    "route_profile": route_profile,
                    "source": "request",
                    "reason": "requested_model_available",
                }


        role_candidates = {
            "triage": ["qwen2.5:3b-instruct", "qwen2.5:7b-instruct", "mistral-nemo:12b"],
            "default": ["qwen2.5:3b-instruct", "qwen2.5:7b-instruct", "mistral-nemo:12b", "qwen2.5:14b-instruct"],
            "reasoning": ["qwen3:14b", "qwen2.5:14b-instruct-32k", "qwen2.5:14b-instruct"],
            "recovery": ["qwen2.5:3b-instruct", "qwen2.5:7b-instruct", "mistral-nemo:12b"],
            "coding": ["qwen2.5-coder:14b-32k", "qwen2.5-coder:14b", "qwen2.5-coder:7b-16k", "qwen2.5-coder:7b", "qwen2.5-coder:3b"],
            "embedding": ["qwen3-embedding:8b", "nomic-embed-text:latest"],
            "reranker": ["qllama/bge-reranker-v2-m3:latest"],
            "vision": ["moondream:latest"],
        }

        if task_type == "coding":
            chosen = self._choose_first_available(installed_models, role_candidates["coding"])
            if chosen:
                return {
                    "chosen_model": chosen,
                    "route_profile": route_profile,
                    "source": "inventory",
                    "reason": "coding_task",
                }

        if task_type == "reasoning":
            chosen = self._choose_first_available(installed_models, role_candidates["reasoning"])
            if chosen:
                return {
                    "chosen_model": chosen,
                    "route_profile": route_profile,
                    "source": "inventory",
                    "reason": "reasoning_task",
                }

        if task_type == "embedding":
            chosen = self._choose_first_available(installed_models, role_candidates["embedding"])
            if chosen:
                return {
                    "chosen_model": chosen,
                    "route_profile": route_profile,
                    "source": "inventory",
                    "reason": "embedding_task",
                }

        if task_type == "reranker":
            chosen = self._choose_first_available(installed_models, role_candidates["reranker"])
            if chosen:
                return {
                    "chosen_model": chosen,
                    "route_profile": route_profile,
                    "source": "inventory",
                    "reason": "reranker_task",
                }

        if task_type == "vision":
            chosen = self._choose_first_available(installed_models, role_candidates["vision"])
            if chosen:
                return {
                    "chosen_model": chosen,
                    "route_profile": route_profile,
                    "source": "inventory",
                    "reason": "vision_task",
                }

        chosen = self._choose_first_available(installed_models, role_candidates.get(route_profile, role_candidates["default"]))
        if chosen:
            return {
                "chosen_model": chosen,
                "route_profile": route_profile,
                "source": "inventory",
                "reason": f"{route_profile}_profile",
            }

        chosen = self._choose_first_available(installed_models, role_candidates["default"])
        if chosen:
            return {
                "chosen_model": chosen,
                "route_profile": route_profile,
                "source": "inventory",
                "reason": "default_fallback",
            }

        return {
            "chosen_model": requested_model or "qwen2.5:7b-instruct",
            "route_profile": route_profile,
            "source": "fallback",
            "reason": "hard_fallback",
        }

    def build_route(
        self,
        prompt: str,
        requested_model: str | None = None,
        phenotype: dict | None = None,
    ) -> dict:
        return self._select_model_from_inventory(
            prompt=prompt,
            requested_model=requested_model,
            phenotype=phenotype,
        )

    def generate(self, model: str | None, prompt: str, phenotype: dict | None = None) -> tuple[str, str]:
        trace_id = self.trace.new_trace_id()
        start_ms = self.trace.now_ms()
        step_budget = self.policy.check_step_budget(1)
        if not step_budget.allowed:
            self.trace.add(
                trace_id=trace_id,
                step_id="generate",
                phase="policy",
                actor="orchestrator",
                action="generate",
                status="blocked",
                summary=step_budget.reason,
            )
            raise RuntimeError(step_budget.reason)

        route = self.build_route(
            prompt=prompt,
            requested_model=model,
            phenotype=phenotype,
        )
        chosen_model = route["chosen_model"]
        self.trace.add(
            trace_id=trace_id,
            step_id="generate",
            phase="start",
            actor="orchestrator",
            action="generate",
            status="started",
            model=chosen_model,
            summary="generation_started",
            metadata={"route": route},
        )

        used_path = "ollama"
        result = self.ollama.generate(model=chosen_model, prompt=prompt)
        duration_ms = self.trace.now_ms() - start_ms

        critic_result = self.critic.evaluate_step(
            result={
                "content": result,
                "finish_reason": "stop",
            },
            expected_kind="generate",
        )

        self.trace.add(
            trace_id=trace_id,
            step_id="generate",
            phase="critic",
            actor="critic",
            action="evaluate",
            status="accepted" if critic_result.accepted else "rejected",
            duration_ms=duration_ms,
            model=chosen_model,
            summary=critic_result.reason,
            metadata={"score": critic_result.score, "retryable": critic_result.retryable},
        )

        event = EventEnvelope.create(
            event_type="llm.generate",
            source="orchestrator",
            payload={
                "requested_model": model,
                "chosen_model": chosen_model,
                "prompt_len": len(prompt),
                "result_len": len(result),
                "path": used_path,
                "trace_id": trace_id,
                "critic_accepted": critic_result.accepted,
                "critic_score": critic_result.score,
                "critic_reason": critic_result.reason,
            },
        )
        self.events.append(event)
        return result, chosen_model

    async def evolve(self) -> None:
        log.info("Orchestrator.evolve(): Starting evolution cycle")
        kernel, metrics = await self.simulation.run(steps=1)

        organisms = getattr(kernel, "organisms", []) or []
        top = max(organisms, key=lambda x: getattr(x, "fitness", 0.0), default=None)

        self.last_swarm_stats = {
            "status": "active" if organisms else "idling",
            "population_size": len(organisms),
            "best_fitness": round(float(getattr(metrics, "best_fitness", 0.0) or 0.0), 4),
            "best_agent_id": getattr(top, "id", "none") if top else "none",
            "active_generation": int(getattr(kernel, "generation", 0) or 0),
        }

        event = EventEnvelope.create(
            event_type="swarm.evolve",
            source="orchestrator",
            payload={
                "generation": getattr(kernel, "generation", None),
                "best_fitness": getattr(metrics, "best_fitness", None),
            },
        )
        self.events.append(event)
        log.info(
            "Orchestrator.evolve(): Evolution cycle complete generation=%s best_fitness=%s",
            getattr(kernel, "generation", None),
            getattr(metrics, "best_fitness", None),
        )

    async def run_agent_step(self) -> dict:
        log.info("Orchestrator.run_agent_step(): Starting agent step")

        task = "Return a one-line system heartbeat for Horseshoe Swarm. State that the orchestrator loop is active."
        route = self.route_task(task, {"phase": "agent_step"})
        prompt = (
            "Return a one-line system heartbeat for Horseshoe Swarm. "
            "State that the orchestrator loop is active."
        )

        try:
            result, chosen_model = self.generate(model=None, prompt=prompt)
            response_text = result.strip()

            payload = {
                "status": "success",
                "message": "Agent step completed",
                "model": chosen_model,
                "response": response_text,
                "route": route,
            }

            event = EventEnvelope.create(
                event_type="agent.step.completed",
                source="orchestrator",
                payload={
                    "model": chosen_model,
                    "response_len": len(response_text),
                    "route_action": route.get("action"),
                    "route_reason": route.get("reason"),
                    "route_target": route.get("target"),
                    "status": "success",
                },
            )
            self.events.append(event)

            log.info(
                "Orchestrator.run_agent_step(): completed model=%s response_len=%d",
                chosen_model,
                len(response_text),
            )
            return payload

        except Exception as e:
            log.exception("Orchestrator.run_agent_step(): agent step failed")

            event = EventEnvelope.create(
                event_type="agent.step.failed",
                source="orchestrator",
                payload={"error": str(e)},
            )
            self.events.append(event)

            return {
                "status": "error",
                "message": str(e),
            }

