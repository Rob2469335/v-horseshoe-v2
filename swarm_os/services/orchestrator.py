from __future__ import annotations

import logging
import time

from ..config.settings import settings as swarm_settings
from ..core.settings import get_settings
from ..events.store import EventStore
from ..infra.ollama import OllamaClient
from ..services.simulation_service import SimulationService
from .control_plane.critic import Critic
from .control_plane.models import ModelProfile, RouteDecision
from .control_plane.planner import Planner
from .control_plane.policy import PolicyEngine
from .control_plane.router import Router
from .control_plane.trace import TraceCollector
from swarm_os.tools.memory_bridge import MemoryBridge

log = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.events = EventStore(s.events_dir)
        self.ollama = OllamaClient()
        self.trace = TraceCollector()
        self.policy = PolicyEngine(max_steps=12)
        self.critic = Critic()
        self.planner = Planner()
        
        self.bridge = MemoryBridge()
        self.simulation = SimulationService(generate_fn=self.generate)

        self.router = Router(
            profiles=[
                ModelProfile(name="qwen2.5:3b-instruct", role="fast", max_tokens=32000),
                ModelProfile(name="qwen2.5:7b-instruct", role="fast", max_tokens=32000),
                ModelProfile(name="qwen2.5:14b-instruct", role="reasoning", max_tokens=32000),
                ModelProfile(name="qwen2.5:14b-instruct-32k", role="reasoning", max_tokens=32768),
                ModelProfile(name="qwen2.5-coder:3b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:7b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:14b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:14b-32k", role="coding", max_tokens=32768),
                ModelProfile(name="qwen3:14b", role="reasoning", max_tokens=32000),
                ModelProfile(name="qwen3-vl:8b", role="vision", preferred_temp=0.2, max_tokens=32768),
                ModelProfile(name="moondream:latest", role="vision", preferred_temp=0.2, max_tokens=8192),
            ],
            default_role="fast",
            cooldown_multiplier=2.0,
            bridge=self.bridge,
        )

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
        return events[-limit:]

    def clear_traces(self) -> None:
        self.trace.clear()

    def _infer_task_role(self, prompt: str = "") -> str:
        text = (prompt or "").lower()

        coding_markers = [
            "python", "powershell", "javascript", "typescript", "fastapi", "traceback",
            "exception", "stack trace", "refactor", "function", "class ", "compile",
            "syntaxerror", "pytest", "module", "import ", "sql", "api", "json",
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
            "investigate", "root cause", "debug this", "step by step",
        ]) or len(prompt) > 2500:
            return "reasoning"

        return "fast"

    def _fetch_installed_models(self) -> list[str]:
        try:
            import requests

            response = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()

            models: list[str] = []
            for item in data.get("models", []):
                name = item.get("model") or item.get("name")
                if isinstance(name, str) and name.strip():
                    models.append(name.strip())
            return models
        except Exception:
            return ["qwen2.5:7b-instruct", "qwen2.5:3b-instruct"]

    async def generate(self, model: str | None, prompt: str, phenotype: dict | None = None) -> tuple[str, str]:
        trace_id = self.trace.new_trace_id()
        start_ms = time.time() * 1000.0

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

        target_role = self._infer_task_role(prompt)
        installed_candidates = self._fetch_installed_models()

        if model and model.strip():
            candidates = [model.strip()]
        else:
            candidates = installed_candidates

        route_decision: RouteDecision = await self.router.route_model(
            candidates=candidates,
            role=target_role,
            allow_fallback=True,
            event_type="GENERATE",
        )

        chosen_model = route_decision.model or "qwen2.5:7b-instruct"

        self.trace.add(
            trace_id=trace_id,
            step_id="generate",
            phase="router",
            actor="orchestrator",
            action="route_model",
            status="selected",
            model=chosen_model,
            summary=route_decision.reason,
            metadata={
                "target_role": target_role,
                "fallback_mode": route_decision.fallback,
                "strategy": route_decision.strategy,
                "router_metadata": dict(route_decision.metadata),
                "phenotype_keys": sorted((phenotype or {}).keys()),
            },
        )

        try:
            result = self.ollama.generate(model=chosen_model, prompt=prompt)
            duration_ms = (time.time() * 1000.0) - start_ms

            self.trace.add(
                trace_id=trace_id,
                step_id="generate",
                phase="generator",
                actor="orchestrator",
                action="generate",
                status="completed",
                duration_ms=duration_ms,
                model=chosen_model,
                summary="Generation completed",
                metadata={
                    "result_chars": len(result or ""),
                    "target_role": target_role,
                },
            )

            critic_result = self.critic.evaluate_step(
                result={"content": result, "finish_reason": "stop"},
                expected_kind="generate",
            )

            if critic_result.accepted:
                self.router.record_success(
                    model=chosen_model,
                    latency_ms=duration_ms,
                )
            else:
                self.router.record_failure(
                    model=chosen_model,
                    cooldown_seconds=15.0 if critic_result.retryable else 45.0,
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
                metadata={
                    "score": critic_result.score,
                    "retryable": critic_result.retryable,
                },
            )

            return result, chosen_model

        except Exception as exc:
            duration_ms = (time.time() * 1000.0) - start_ms
            self.router.record_failure(
                model=chosen_model,
                cooldown_seconds=60.0,
            )
            self.trace.add(
                trace_id=trace_id,
                step_id="generate",
                phase="generator",
                actor="orchestrator",
                action="generate",
                status="failed",
                duration_ms=duration_ms,
                model=chosen_model,
                summary=str(exc),
            )
            log.exception("Generation failed on model %s", chosen_model)
            raise

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

    async def evolve(self) -> None:
        log.info("Orchestrator.evolve(): Starting evolution cycle")
        kernel, metrics = await self.simulation.run(steps=1)
        organisms = getattr(kernel, "organisms", []) or []
        top = max(organisms, key=lambda item: getattr(item, "fitness", 0.0), default=None)

        self.last_swarm_stats = {
            "status": "active" if organisms else "idling",
            "population_size": len(organisms),
            "best_fitness": round(float(getattr(metrics, "best_fitness", 0.0) or 0.0), 4),
            "best_agent_id": getattr(top, "id", "none") if top else "none",
            "active_generation": int(getattr(kernel, "generation", 0) or 0),
        }

    async def run_agent_step(self) -> dict:
        prompt = "Return a one-line system heartbeat for Horseshoe Swarm. State that the orchestrator loop is active."
        try:
            result, chosen_model = await self.generate(model=None, prompt=prompt)
            return {
                "status": "success",
                "message": "Agent step completed",
                "model": chosen_model,
                "response": result.strip(),
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": str(exc),
            }

# Compatibility export for API/tests expecting a module-level director
try:
    swarm_director
except NameError:
    try:
        swarm_director = Orchestrator()
    except Exception:
        swarm_director = None