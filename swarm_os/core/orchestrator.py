from __future__ import annotations
import re
import logging
import time
import json
import asyncio

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
from swarm_os.lib.mcp.registry import registry as mcp_registry

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
        self.mcp = mcp_registry

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

    def _parse_tool_call(self, text: str) -> tuple[str, str] | None:
        # Check Pattern A: <tool_call name="tool">params</tool_call>
        match_a = re.search(r'<tool_call\s+name="([^"]+)">\s*(\{.*?\})\s*</tool_call>', text, re.DOTALL)
        if match_a:
            return match_a.group(1).strip(), match_a.group(2).strip()
        # Check Pattern B: <tool>tool</tool> params
        match_b = re.search(r'<tool>([^<]+)</tool>\s*(\{.*?\})', text, re.DOTALL)
        if match_b:
            return match_b.group(1).strip(), match_b.group(2).strip()
        return None

    async def _get_memory_context(self, query: str) -> str:
        try:
            vec = await self.bridge._embed(query)
            if vec is not None:
                async with self.bridge.lock_vector:
                    results = await asyncio.to_thread(
                        self.bridge.vs.search,
                        query_vector=vec,
                        limit=3,
                    )
                if results:
                    context_parts = ["### Relevant historical context from swarm runs:"]
                    for hit in results:
                        payload = hit.get("payload", {}) or {}
                        summary = payload.get("summary", "")
                        models = payload.get("models", [])
                        outcome = payload.get("dominant_outcome", "unknown")
                        if summary:
                            context_parts.append(f"- Summary: {summary} (Models: {', '.join(models)}, Outcome: {outcome})")
                    return "\n".join(context_parts) + "\n"
        except Exception as e:
            log.warning("Failed to retrieve memory context: %s", e)
        return ""

    async def generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None) -> tuple[str, str]:
        messages = list(messages or [])
        # Context Injection
        if prompt:
            mem_context = await self._get_memory_context(prompt)
            if mem_context:
                full_prompt = f"{mem_context}\n### Current Task:\n{prompt}"
            else:
                full_prompt = prompt
            messages.append({"role": "user", "content": full_prompt})
        elif messages:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    mem_context = await self._get_memory_context(content)
                    if mem_context:
                        msg["content"] = f"{mem_context}\n### Current Task:\n{content}"
                    break
            
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

        max_steps = 5
        final_result = ""
        for step in range(max_steps):
            try:
                print(f"[Orchestrator] generate() Turn {step + 1}/{max_steps} starting with model={chosen_model}", flush=True)
                result = await self.ollama.generate(model=chosen_model, messages=messages)
                print(f"[Orchestrator] generate() Turn result: {result!r}", flush=True)
                
                tool_info = self._parse_tool_call(result)
                if tool_info:
                    tool_name, params_str = tool_info
                    print(f"[Orchestrator] Intercepted tool call in generate(): {tool_name} with params: {params_str}", flush=True)
                    try:
                        params = json.loads(params_str)
                    except Exception as e:
                        params = {}
                        print(f"[Orchestrator] Failed to parse tool params JSON: {e}", flush=True)
                    
                    # Execute tool
                    print(f"[Orchestrator] Executing tool {tool_name}...", flush=True)
                    observation = await self.mcp.call(tool_name, params)
                    print(f"[Orchestrator] Tool execution result: {observation}", flush=True)
                    
                    # Inject back into history
                    messages.append({"role": "assistant", "content": result})
                    messages.append({"role": "tool", "content": f"Observation: {json.dumps(observation)}"})
                    continue
                else:
                    final_result = result
                    print(f"[Orchestrator] Final result received in generate(). Exiting loop.", flush=True)
                    break
            except Exception as exc:
                self.router.record_failure(model=chosen_model, cooldown_seconds=60.0)
                log.exception("Generation failed")
                raise

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

        return final_result, chosen_model

    async def stream_generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None):
        log.info("[Orchestrator] Entering stream_generate. model=%s, prompt=%s, messages=%s", model, prompt, messages)
        print(f"[Orchestrator] Entering stream_generate. model={model}, prompt={prompt}", flush=True)
        messages = list(messages or [])
        
        # Context Injection
        if prompt:
            mem_context = await self._get_memory_context(prompt)
            if mem_context:
                full_prompt = f"{mem_context}\n### Current Task:\n{prompt}"
            else:
                full_prompt = prompt
            messages.append({"role": "user", "content": full_prompt})
        elif messages:
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    mem_context = await self._get_memory_context(content)
                    if mem_context:
                        msg["content"] = f"{mem_context}\n### Current Task:\n{content}"
                    break

        if not messages:
            log.warning("[Orchestrator] No input messages provided for stream_generate")
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

        log.info("[Orchestrator] Routing decision: %s. Starting Ollama stream...", chosen_model)
        
        max_steps = 5
        for step in range(max_steps):
            print(f"[Orchestrator] stream_generate() Turn {step + 1}/{max_steps} starting with model={chosen_model}", flush=True)
            accumulated_text = ""
            print(f"[Orchestrator] OllamaClient beginning stream for model={chosen_model}", flush=True)
            try:
                async for chunk in self.ollama.stream_generate(model=chosen_model, messages=messages):
                    accumulated_text += chunk
                    log.info("[Orchestrator] Received chunk: %r", chunk)
                    print(f"[Orchestrator] Received chunk from Ollama: {chunk!r}", flush=True)
                    yield chunk, chosen_model, trace_id
                
                tool_info = self._parse_tool_call(accumulated_text)
                if tool_info:
                    tool_name, params_str = tool_info
                    print(f"[Orchestrator] Intercepted tool call in stream_generate(): {tool_name} with params: {params_str}", flush=True)
                    try:
                        params = json.loads(params_str)
                    except Exception as e:
                        params = {}
                        print(f"[Orchestrator] Failed to parse tool params JSON: {e}", flush=True)
                    
                    # Execute tool
                    print(f"[Orchestrator] Executing tool {tool_name}...", flush=True)
                    observation = await self.mcp.call(tool_name, params)
                    print(f"[Orchestrator] Tool execution result: {observation}", flush=True)
                    
                    # Yield observation back to the stream so the client receives it
                    obs_text = f"\n[Observation: {json.dumps(observation)}]\n"
                    yield obs_text, chosen_model, trace_id
                    
                    # Inject back into history
                    messages.append({"role": "assistant", "content": accumulated_text})
                    messages.append({"role": "tool", "content": f"Observation: {json.dumps(observation)}"})
                    continue
                else:
                    print(f"[Orchestrator] Final stream result received. Exiting loop.", flush=True)
                    break
            except Exception as exc:
                log.exception("[Orchestrator] Streaming failed with error")
                yield f"\n[Stream Error: {exc}]", chosen_model, trace_id
                break

        duration_ms = (time.time() * 1000.0) - start_ms
        log.info("[Orchestrator] Stream completed successfully. Duration: %.2f ms", duration_ms)
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

