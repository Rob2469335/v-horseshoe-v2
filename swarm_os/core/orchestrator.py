from __future__ import annotations
import re

import logging
import time

from ..config.settings import settings as swarm_settings
from ..core.settings import get_settings
from ..events.store import EventStore
from ..infra.ollama import OllamaClient
from ..services.simulation_service import SimulationService
from swarm_os.services.control_plane.critic import Critic
from swarm_os.services.control_plane.models import ModelProfile, RouteDecision
from swarm_os.services.control_plane.planner import Planner
from swarm_os.services.control_plane.policy import PolicyEngine
from swarm_os.services.control_plane.router import Router
from swarm_os.services.control_plane.trace import TraceCollector
from swarm_os.memory.memory_bridge import MemoryBridge

log = logging.getLogger(__name__)

class Orchestrator:
    """
    The central brain of Swarm OS. 
    Coordinates planning, routing, generation, and trace collection.
    """
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
                ModelProfile(name="qwen3:14b", role="reasoning", max_tokens=32000),
                
                ModelProfile(name="qwen2.5-coder:3b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:7b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:14b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:14b-32k", role="coding", max_tokens=32768),
                ModelProfile(name="qwen2.5-coder:32b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen3-vl:8b", role="vision", preferred_temp=0.2, max_tokens=32768),
                ModelProfile(name="moondream:latest", role="vision", preferred_temp=0.2, max_tokens=8192),
            ],
            default_role="reasoning",
            cooldown_multiplier=2.0,
        )

        self.swarm_base_url = swarm_settings.swarm_url
        self.swarm_timeout = swarm_settings.swarm_timeout

    def _infer_task_role(self, messages: list[dict]) -> str:
        # Infer role from the last message
        last_content = messages[-1]["content"].lower() if messages else ""

        coding_markers = [
            "python", "powershell", "javascript", "typescript", "fastapi", "traceback",
            "exception", "stack trace", "refactor", "function", "class ", "def ", "import ",
            "syntaxerror", "pytest", "module", "sql", "api", "json",
        ]
        if any(re.search(r"\\b" + re.escape(m.strip()) + r"\\b", last_content) for m in coding_markers):
            rv_classes = ["class a", "class b", "class c"]
            if not any(rv in last_content for rv in rv_classes):
                return "coding"

        if any(marker in last_content for marker in ["image", "screenshot", "diagram", "vision", "ocr", "photo"]):
            return "vision"

        if any(marker in last_content for marker in ["embed", "embedding", "vector"]):
            return "embedding"

        if any(marker in last_content for marker in ["analyze", "analysis", "compare", "design", "architecture", "plan", "reason"]):
            return "reasoning"

        return "fast"

    def _fetch_installed_models(self) -> list[str]:
        try:
            import requests
            response = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            return [m.get("name") for m in data.get("models", []) if m.get("name")]
        except Exception:
            return ["qwen2.5:7b-instruct", "qwen2.5:3b-instruct"]

    async def generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None) -> tuple[str, str]:
        messages = list(messages or [])
        if prompt:
            messages.append({"role": "user", "content": prompt})
            
        if not messages:
            raise ValueError("Either messages or prompt must be provided")

        trace_id = self.trace.new_trace_id()
        start_ms = time.time() * 1000.0

        target_role = self._infer_task_role(messages)
        installed_candidates = self._fetch_installed_models()

        if model and model.strip():
            candidates = [model.strip()]
        else:
            candidates = installed_candidates

        route_decision = await self.router.route_model(
            candidates=candidates,
            role=target_role,
            allow_fallback=True,
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
            summary=getattr(route_decision, "reason", "routed"),
            metadata={
                "target_role": target_role,
                "fallback_mode": getattr(route_decision, "fallback", False),
                "strategy": getattr(route_decision, "strategy", "default"),
                "router_metadata": dict(getattr(route_decision, "metadata", {})) if getattr(route_decision, "metadata", None) else {},
            },
        )

        try:
            result = await self.ollama.generate(model=chosen_model, messages=messages)
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
                summary="Generation completed"
            )

            return result, chosen_model

        except Exception as exc:
            self.router.record_failure(model=chosen_model, cooldown_seconds=60.0)
            log.exception("Generation failed")
            raise

    async def stream_generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None):
        messages = list(messages or [])
        if prompt:
            messages.append({"role": "user", "content": prompt})

        if not messages:
            yield f"\n[Error: No input provided]", "none", "none"
            return

        trace_id = self.trace.new_trace_id()
        start_ms = time.time() * 1000.0

        target_role = self._infer_task_role(messages)
        installed_candidates = self._fetch_installed_models()

        if model and model.strip():
            candidates = [model.strip()]
        else:
            candidates = installed_candidates

        route_decision = await self.router.route_model(
            candidates=candidates,
            role=target_role,
            allow_fallback=True,
        )

        chosen_model = route_decision.model or "qwen2.5:7b-instruct"

        try:
            async for chunk in self.ollama.stream_generate(model=chosen_model, messages=messages):
                yield chunk, chosen_model, trace_id
            
            duration_ms = (time.time() * 1000.0) - start_ms
            self.trace.add(
                trace_id=trace_id,
                step_id="generate_stream",
                phase="generator",
                actor="orchestrator",
                action="generate",
                status="completed",
                duration_ms=duration_ms,
                model=chosen_model,
                summary="Stream completed"
            )

        except Exception as exc:
            log.exception("Streaming failed")
            yield f"\n[Stream Error: {exc}]", chosen_model, trace_id

    async def evolve(self) -> None:
        pass

    async def run_agent_step(self) -> dict:
        prompt = "Return a one-line system heartbeat for Horseshoe Swarm."
        result, model = await self.generate(model=None, messages=[{"role": "user", "content": prompt}])
        return {"status": "success", "response": result, "model": model}

    def get_recent_traces(self, limit: int = 50) -> list[dict]:
        return self.trace.events()[-limit:]

def build_orchestrator(settings=None, event_store=None):
    return Orchestrator()

