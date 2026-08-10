from __future__ import annotations
from swarm_os.services.token_manager import TokenManager
from swarm_os.services.llm_client import CloudLLMClient
import re
import os as _os
import logging
import time
import json
import hashlib
import asyncio
import httpx

from ..config.settings import settings as swarm_settings
from ..core.settings import get_settings
from ..events.store import EventStore
from ..events.envelope import EventEnvelope
from ..infra.llama_client import LlamaClient
from ..services.simulation_service import SimulationService
from swarm_os.services.control_plane.critic import Critic
from swarm_os.services.control_plane.models import ModelProfile
from swarm_os.services.control_plane.planner import Planner
from swarm_os.services.control_plane.policy import PolicyEngine
from swarm_os.services.control_plane.router import Router
from swarm_os.services.control_plane.trace import TraceCollector
from swarm_os.memory.memory_bridge import MemoryBridge
from swarm_os.lib.mcp.registry import registry as mcp_registry

log = logging.getLogger(__name__)

global_httpx_client = httpx.AsyncClient(
    timeout=120.0,
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
    verify=swarm_settings.ssl_verify
)


async def close_global_client() -> None:
    """Close the module-level shared httpx client on shutdown."""
    await global_httpx_client.aclose()


_cached_models: list[str] = []
_models_cache_time: float = 0.0
_models_cache_lock = asyncio.Lock()

# ISSUE 17: Guard against duplicate concurrent generation requests.
# Dict[hash -> timestamp] with 5-minute TTL auto-expiry to prevent leaks
# even if an exception skips the explicit discard() call.
_active_generations: dict[str, float] = {}
_generation_lock = asyncio.Lock()
_GEN_LOCK_TTL = 300.0


def _dedup_key(messages: list[dict]) -> str:
    """Hash the input messages into a stable dedup key."""
    _dedup_input = json.dumps(
        [{"role": m.get("role"), "content": m.get("content")} for m in messages],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(_dedup_input.encode()).hexdigest()[:16]


async def _acquire_generation_slot(dedup_hash: str) -> bool:
    """Register a generation in _active_generations under the lock. Returns True
    if an identical generation is ALREADY running (caller should suppress)."""
    async with _generation_lock:
        now = time.time()
        # Prune stale entries older than TTL
        stale = [k for k, ts in _active_generations.items() if now - ts > _GEN_LOCK_TTL]
        for k in stale:
            del _active_generations[k]
        if dedup_hash in _active_generations:
            return True
        _active_generations[dedup_hash] = now
        return False


async def _release_generation_slot(dedup_hash: str) -> None:
    async with _generation_lock:
        _active_generations.pop(dedup_hash, None)


class Orchestrator:
    """
    The central brain of Swarm OS. 
    Coordinates planning, routing, generation, and trace collection.
    """
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.events = EventStore(s.events_dir)
        self.llm = LlamaClient()
        self.ollama = self.llm  # Backward compatibility alias
        self.trace = TraceCollector()
        self.policy = PolicyEngine(max_steps=12)
        self.critic = Critic()
        self.planner = Planner()
        
        self.bridge = MemoryBridge(event_log_path="data/events/events.jsonl")
        self.simulation = SimulationService(generate_fn=self.generate)
        self.mcp = mcp_registry
        self.token_manager = TokenManager(budget=500000)

        self.router = Router(
            profiles=[
                ModelProfile(name="qwen3.5-4b", role="fast", max_tokens=16384),
                ModelProfile(name="qwen3.5-4b", role="coding", max_tokens=16384),
                ModelProfile(name="qwen3.5-4b", role="reasoning", max_tokens=16384),
                ModelProfile(name="qwen3.5-4b", role="reviewer", max_tokens=16384),
                ModelProfile(name="Qwen3VL-2B-Instruct", role="vision", preferred_temp=0.2, max_tokens=8192),
            ],
            default_role="reasoning",
            cooldown_multiplier=2.0,
        )

        self.swarm_base_url = swarm_settings.swarm_url
        self.swarm_timeout = swarm_settings.swarm_timeout

    def _classify_intent(self, messages: list[dict]) -> str:
        last_content = messages[-1]["content"].lower().strip() if messages else ""
        
        chit_chat_markers = {"hello", "hi", "hey", "how are you", "good morning", "thanks", "thank you", "bye", "goodbye", "gm", "gn"}
        if last_content in chit_chat_markers or (len(last_content.split()) < 4 and any(m in last_content.split() for m in chit_chat_markers)):
            return "chit_chat"
            
        search_markers = ["search", "find", "where is", "look up", "query", "what is"]
        if any(last_content.startswith(m) for m in search_markers):
            return "search"
            
        action_markers = ["run", "execute", "start", "stop", "deploy", "build", "create", "delete"]
        if any(last_content.startswith(m) for m in action_markers):
            return "action"
            
        return "complex"

    def _infer_task_role(self, messages: list[dict]) -> str:
        intent = self._classify_intent(messages)
        if intent == "chit_chat":
            return "fast"
            
        # Infer role from the last message
        last_content = messages[-1]["content"].lower() if messages else ""

        coding_markers = [
            "python", "powershell", "javascript", "typescript", "fastapi", "traceback",
            "exception", "stack trace", "refactor", "function", "class", "def", "import",
            "syntaxerror", "pytest", "module", "sql", "api", "json",
        ]
        if any(re.search(r"\b" + re.escape(m) + r"\b", last_content) for m in coding_markers):
            return "coding"

        if any(marker in last_content for marker in ["image", "screenshot", "diagram", "vision", "ocr", "photo"]):
            return "vision"

        if any(marker in last_content for marker in ["embed", "embedding", "vector"]):
            return "embedding"

        if any(marker in last_content for marker in ["analyze", "analysis", "compare", "design", "architecture", "plan", "reason"]):
            return "reasoning"

        return "fast"

    async def _fetch_installed_models(self) -> list[str]:
        global _cached_models, _models_cache_time
        async with _models_cache_lock:
            if _cached_models and time.time() - _models_cache_time < 60.0:
                return _cached_models
            try:
                response = await global_httpx_client.get("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer llama"}, timeout=5.0)
                response.raise_for_status()
                data = response.json()
                _cached_models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                _models_cache_time = time.time()
                return _cached_models
            except Exception as e:
                log.warning("Failed to fetch installed models: %s", e)
                return _cached_models or ["qwen3.5-4b"]

    def _parse_tool_call(self, text: str) -> tuple[str, str] | None:
        """Delegated to decoupled ToolParser."""
        from .tool_parser import ToolParser
        return ToolParser.parse(text)

    def _detect_provider(self, model_name: str) -> str:
        return CloudLLMClient.detect_provider(model_name)

    async def _cloud_generate(self, model: str, messages: list[dict], provider: str, stream: bool = False):
        return await CloudLLMClient.generate(model, messages, provider, stream)

    async def _get_memory_context(self, query: str) -> str:
        return await self.bridge.get_memory_context(query)

    async def generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None) -> tuple[str, str]:
        messages = [dict(m) for m in (messages or [])]
        # Context Injection
        if prompt:
            mem_context = await self._get_memory_context(prompt)
            if mem_context:
                full_prompt = f"{mem_context}\n### Current Task:\n{prompt}\n\nAssistant: <tool>"
            else:
                full_prompt = f"{prompt}\n\nAssistant: <tool>"
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

        # Dynamic Tool Schema Discovery & Injection
        schemas = self.mcp.get_tools_schema()
        schemas_str = json.dumps(schemas, indent=2)
        tools_instruction = (
            "\n### Available MCP Tools:\n"
            "You have access to the following tools. You MUST respond with ONLY a raw JSON object to call a tool.\n"
            "Format: {\"tool\": \"tool_name\", \"params\": {\"arg\": \"val\"}}\n\n"
            f"Tool Schemas:\n{schemas_str}\n"
        )
        inserted = False
        for msg in messages:
            if msg.get("role") == "system":
                msg["content"] = (msg.get("content") or "") + tools_instruction
                inserted = True
                break
        if not inserted and messages:
            messages[0]["content"] = tools_instruction + "\n" + (messages[0].get("content") or "")

        trace_id = self.trace.new_trace_id()
        start_ms = time.time() * 1000.0

        # ISSUE 17: Dedup key based on input messages — reject duplicate concurrent generation.
        _dedup_hash = _dedup_key(messages)
        if await _acquire_generation_slot(_dedup_hash):
            log.warning("[Orchestrator] Duplicate generation blocked (hash=%s). A generation with identical messages is already running.", _dedup_hash)
            return "Duplicate generation suppressed — an identical request is already in progress.", model

        try:
            target_role = self._infer_task_role(messages)
            installed_candidates = await self._fetch_installed_models()
    
            if model and model.strip():
                candidates = [model.strip()]
            else:
                candidates = installed_candidates
    
            route_decision = await self.router.route_model(
                candidates=candidates,
                role=target_role,
                allow_fallback=True,
            )
    
            chosen_model = route_decision.model or "qwen3.5-4b"
    
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
    
            # Detect provider for the chosen model
            provider = self._detect_provider(chosen_model)
            log.info(f"[Orchestrator] generate() provider={provider} for model={chosen_model}")
    
            # If the provider is cloud but no API key is available, fall back to a local model
            if provider in ("openrouter", "nvidia") and not _os.environ.get(
                "OPENROUTER_API_KEY" if provider == "openrouter" else "NVIDIA_API_KEY", ""
            ).strip():
                log.info(f"[Orchestrator] No API key for {provider}, falling back to local model")
                chosen_model = "qwen3.5-4b"
                provider = "llama"
    
            max_steps = 5
            final_result = ""
            handled_tool_keys: set[str] = set()  # Track handled tool calls to detect repeats
    
            for step in range(max_steps):
                try:
                    # Token budget check
                    await self.token_manager.check_budget()
    
                    log.info(f"[Orchestrator] generate() Turn {step + 1}/{max_steps} starting with model={chosen_model} provider={provider}")
    
                    # Dispatch to the correct provider
                    if provider in ("openrouter", "nvidia"):
                        result = await self._cloud_generate(model=chosen_model, messages=messages, provider=provider, stream=False)
                    else:
                        result = await self.llm.generate(model=chosen_model, messages=messages)
    
                    log.info(f"[Orchestrator] generate() Turn result: {result!r}")
                    
                    # Update tokens used
                    await self.token_manager.add_usage(result)
    
                    tool_info = self._parse_tool_call(result)
                    if not tool_info:
                        final_result = result
                        log.info("[Orchestrator] Plain-text response received. Exiting loop.")
                        break
                    if tool_info:
                        tool_name, params_str = tool_info
                        # Build a dedup key from tool name + params
                        dedup_key = f"{tool_name}:{params_str}"
    
                        if dedup_key in handled_tool_keys:
                            # This exact tool call was already handled — stop looping
                            log.info(f"[Orchestrator] DUPLICATE tool call detected: {tool_name}. Breaking loop.")
                            final_result = "Duplicate tool call detected. Stopping loop."
                            break
    
                        log.info(f"[Orchestrator] Intercepted tool call in generate(): {tool_name} with params: {params_str}")
                        try:
                            params = json.loads(params_str)
                        except Exception as e:
                            log.warning(f"[Orchestrator] Failed to parse tool params JSON: {e}")
                            messages.append({"role": "assistant", "content": result})
                            messages.append({
                                "role": "user",
                                "content": f"Critic Feedback: The tool execution failed because the JSON parameters could not be parsed: {e}. Please correct the parameters and call the tool again."
                            })
                            continue
                        
                        # Execute tool
                        log.info(f"[Orchestrator] Executing tool {tool_name}...")
                        if tool_name == "command":
                            observation = {
                                "ok": True,
                                "kind": "command",
                                "command": params.get("command"),
                                "confidence": params.get("confidence"),
                                "handled": True,
                                "status": "completed",
                                "result": f"Slash command {params.get('command')} was intercepted and handled by the orchestrator.",
                                "next_step": "Continue the assistant response using this command result. Do not call the same slash command again unless the user explicitly asks to rerun it.",
                                "note": "Slash command intercepted by orchestrator compatibility shim.",
                            }
                        else:
                            observation = await self.mcp.call(tool_name, params)
                        log.info(f"[Orchestrator] Tool execution result: {observation}")
    
                        # Mark this tool call as handled
                        handled_tool_keys.add(dedup_key)
                        
                        obs_str = json.dumps(observation)
                        if len(obs_str) > 2000:
                            obs_str = obs_str[:2000] + "... [Truncated for context limit]"
                        
                        # Critic evaluation
                        critic_res = self.critic.evaluate_step(observation, expected_kind="tool")
                        if not critic_res.accepted:
                            log.warning(f"[Orchestrator] Critic rejected tool execution: {critic_res.reason}")
                            # Cleanup failed thought from active context window
                            if len(messages) > 1 and messages[-1].get("role") == "user" and "Critic Feedback" in messages[-1].get("content", ""):
                                messages.pop()
                                messages.pop()
                            
                            messages.append({"role": "assistant", "content": result})
                            messages.append({
                                "role": "user",
                                "content": f"Critic Feedback: The tool execution returned an error or was rejected: {critic_res.reason}. Please correct the parameters and call the tool again."
                            })
                            continue
    
                        # For handled slash commands, return immediately instead of continuing
                        # The command shim is a terminal action — no need to re-prompt the model
                        if tool_name == "command":
                            log.info("[Orchestrator] Slash command handled. Returning immediately.")
                            final_result = observation.get("result", result)
                            messages.append({"role": "assistant", "content": result})
                            messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{obs_str}\n\nContinue with the next assistant response."})
                            return final_result, model
                        else:
                            # Tool succeeded — append observation and let model continue
                            messages.append({"role": "assistant", "content": result})
                            messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{obs_str}\n\nContinue with the next step."})
                            continue
                except ValueError as e:
                    if "budget exceeded" in str(e).lower():
                        log.warning(f"[Orchestrator] Token budget exceeded: {e}")
                    else:
                        self.router.record_failure(model=chosen_model, cooldown_seconds=60.0)
                        log.exception("Generation failed with ValueError")
                    raise
                except Exception:
                    self.router.record_failure(model=chosen_model, cooldown_seconds=60.0)
                    log.exception("Generation failed")
                    raise
    
            # If we exhausted all steps without a final result, use what we have
            if not final_result:
                final_result = "[System: Generation completed without a final response after maximum steps.]"
    
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
    
            # Emit to the live SSE event bus so the organism dashboard's swarm feed updates.
            from swarm_os.core.event_bus import event_bus
            event_bus.emit("GENERATION_COMPLETED", trace_id, {
                "model": chosen_model,
                "duration_ms": duration_ms,
                "summary": "Generation completed",
            })
    
            await asyncio.to_thread(
                self.events.append,
                EventEnvelope.create(
                    event_type="generation_completed",
                    source="orchestrator",
                    payload={
                        "model": chosen_model,
                        "task_id": trace_id,
                        "elapsed": duration_ms,
                        "status": "completed",
                        "content": final_result,
                    }
                )
            )
    
        finally:
            await _release_generation_slot(_dedup_hash)

        # Record the SUCCESS in the bandit — without this the model's
        # successes counter never advances (record_failure at the except above
        # is the only other call), so total_requests climbs while successes
        # stays 0 and a model that failed ONCE can never recover its standing
        # (strategy.py scores success_rate = successes / total_requests).
        try:
            self.router.record_success(model=chosen_model, latency_ms=duration_ms)
        except Exception as exc:
            log.debug("Failed to record success: %s", exc)

        return final_result, chosen_model


    async def stream_generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None):
        log.info("[Orchestrator] Entering stream_generate. model=%s, prompt=%s, messages=%s", model, prompt, messages)
        messages = [dict(m) for m in (messages or [])]
        
        # Context Injection
        if prompt:
            mem_context = await self._get_memory_context(prompt)
            if mem_context:
                full_prompt = f"{mem_context}\n### Current Task:\n{prompt}\n\nAssistant: <tool>"
            else:
                full_prompt = f"{prompt}\n\nAssistant: <tool>"
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
            yield "\n[Error: No input provided]", "none", "none"
            return

        # Dynamic Tool Schema Discovery & Injection
        schemas = self.mcp.get_tools_schema()
        schemas_str = json.dumps(schemas, indent=2)
        tools_instruction = (
            "\n### Available MCP Tools:\n"
            "You have access to the following tools. You MUST respond with ONLY a raw JSON object to call a tool.\n"
            "Format: {\"tool\": \"tool_name\", \"params\": {\"arg\": \"val\"}}\n\n"
            f"Tool Schemas:\n{schemas_str}\n"
        )
        inserted = False
        for msg in messages:
            if msg.get("role") == "system":
                msg["content"] = (msg.get("content") or "") + tools_instruction
                inserted = True
                break
        if not inserted and messages:
            messages[0]["content"] = tools_instruction + "\n" + (messages[0].get("content") or "")

        trace_id = self.trace.new_trace_id()
        start_ms = time.time() * 1000.0

        # Dedup parity with generate(): rapid double-clicks on a streaming
        # endpoint would otherwise spawn duplicate concurrent LLM requests.
        _dedup_hash = _dedup_key(messages)
        if await _acquire_generation_slot(_dedup_hash):
            log.warning("[Orchestrator] Duplicate stream generation blocked (hash=%s). An identical stream is already running.", _dedup_hash)
            yield "Duplicate generation suppressed — an identical request is already in progress.", "none", trace_id
            return

        target_role = self._infer_task_role(messages)
        installed_candidates = await self._fetch_installed_models()

        if model and model.strip():
            candidates = [model.strip()]
        else:
            candidates = installed_candidates

        route_decision = await self.router.route_model(
            candidates=candidates,
            role=target_role,
            allow_fallback=True,
        )

        chosen_model = route_decision.model or "qwen3.5-4b"

        log.info("[Orchestrator] Routing decision: %s. Starting LLM stream...", chosen_model)
        
        # Detect provider for the chosen model
        provider = self._detect_provider(chosen_model)
        log.info(f"[Orchestrator] stream_generate() provider={provider} for model={chosen_model}")

        # If the provider is cloud but no API key is available, fall back to a local model
        if provider in ("openrouter", "nvidia") and not _os.environ.get(
            "OPENROUTER_API_KEY" if provider == "openrouter" else "NVIDIA_API_KEY", ""
        ).strip():
            log.info(f"[Orchestrator] No API key for {provider}, falling back to local model")
            chosen_model = "qwen3.5-4b"
            provider = "llama"

        max_steps = 5
        handled_tool_keys: set[str] = set()  # Track handled tool calls to detect repeats
        accumulated_text = ""
        stream_failed = False  # set by the exception branch; gates bandit success

        try:
            for step in range(max_steps):
                # Token budget check
                if await self.token_manager.is_exhausted():
                    yield f"\n[Error: Token budget exceeded ({await self.token_manager.get_usage()} used)]", chosen_model, trace_id
                    break

                log.info(f"[Orchestrator] stream_generate() Turn {step + 1}/{max_steps} starting with model={chosen_model} provider={provider}")
                accumulated_text = ""
                try:
                    # Dispatch to the correct provider
                    if provider in ("openrouter", "nvidia"):
                        stream_gen = await self._cloud_generate(model=chosen_model, messages=messages, provider=provider, stream=True)
                        async for chunk in stream_gen:
                            accumulated_text += chunk
                            yield chunk, chosen_model, trace_id
                    else:
                        async for chunk in self.llm.stream_generate(model=chosen_model, messages=messages):
                            accumulated_text += chunk
                            yield chunk, chosen_model, trace_id

                    # Update tokens used
                    await self.token_manager.add_usage(accumulated_text)

                    tool_info = self._parse_tool_call(accumulated_text)
                    if tool_info:
                        tool_name, params_str = tool_info
                        dedup_key = f"{tool_name}:{params_str}"

                        if dedup_key in handled_tool_keys:
                            log.info(f"[Orchestrator] DUPLICATE tool call detected in stream: {tool_name}. Breaking loop.")
                            yield "\n[System: Duplicate tool call detected. Stopping loop.]\n", chosen_model, trace_id
                            break

                        log.info(f"[Orchestrator] Intercepted tool call in stream_generate(): {tool_name} with params: {params_str}")
                        try:
                            params = json.loads(params_str)
                        except Exception as e:
                            log.warning(f"[Orchestrator] Failed to parse tool params JSON: {e}")
                            obs_text = f"\n[Critic Rejection: Invalid JSON parameters: {e}. Requesting self-correction...]\n"
                            yield obs_text, chosen_model, trace_id

                            messages.append({"role": "assistant", "content": accumulated_text})
                            messages.append({
                                "role": "user",
                                "content": f"Critic Feedback: The tool execution failed because the JSON parameters could not be parsed: {e}. Please correct the parameters and call the tool again."
                            })
                            continue

                        # Execute tool
                        log.info(f"[Orchestrator] Executing tool {tool_name}...")
                        if tool_name == "command":
                            observation = {
                                "ok": True,
                                "kind": "command",
                                "command": params.get("command"),
                                "confidence": params.get("confidence"),
                                "handled": True,
                                "status": "completed",
                                "result": f"Slash command {params.get('command')} was intercepted and handled by the orchestrator.",
                                "next_step": "Continue the assistant response using this command result. Do not call the same slash command again unless the user explicitly asks to rerun it.",
                                "note": "Slash command intercepted by orchestrator compatibility shim.",
                            }
                        else:
                            observation = await self.mcp.call(tool_name, params)
                        log.info(f"[Orchestrator] Tool execution result: {observation}")

                        # Mark this tool call as handled
                        handled_tool_keys.add(dedup_key)

                        obs_str = json.dumps(observation)
                        if len(obs_str) > 2000:
                            obs_str = obs_str[:2000] + "... [Truncated for context limit]"

                        # Critic evaluation
                        critic_res = self.critic.evaluate_step(observation, expected_kind="tool")
                        if not critic_res.accepted:
                            log.warning(f"[Orchestrator] Critic rejected tool execution: {critic_res.reason}")
                            obs_text = f"\n[Critic Rejection: {critic_res.reason}. Requesting self-correction...]\n"
                            yield obs_text, chosen_model, trace_id

                            if len(messages) > 1 and messages[-1].get("role") == "user" and "Critic Feedback" in messages[-1].get("content", ""):
                                messages.pop()
                                messages.pop()

                            messages.append({"role": "assistant", "content": accumulated_text})
                            messages.append({
                                "role": "user",
                                "content": f"Critic Feedback: The tool execution returned an error or was rejected: {critic_res.reason}. Please correct the parameters and call the tool again."
                            })
                            continue

                        # For handled slash commands, break immediately — the shim is terminal
                        if tool_name == "command":
                            log.info("[Orchestrator] Slash command handled in stream. Continuing.")
                            obs_text = f"\n[Observation: {obs_str}]\n"
                            yield obs_text, chosen_model, trace_id
                            messages.append({"role": "assistant", "content": accumulated_text})
                            messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{obs_str}\n\nContinue with the next assistant response."})
                            break
                        # Yield observation back to the stream so the client receives it
                        obs_text = f"\n[Observation: {obs_str}]\n"
                        yield obs_text, chosen_model, trace_id

                        # Inject back into history for non-command tools
                        messages.append({"role": "assistant", "content": accumulated_text})
                        messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{obs_str}\n\nContinue with the next assistant response."})
                        continue
                    else:
                        log.info("[Orchestrator] Final stream result received. Exiting loop.")
                        break
                except Exception as exc:
                    log.exception("[Orchestrator] Streaming failed with error")
                    stream_failed = True
                    yield f"\n[Stream Error: {exc}]", chosen_model, trace_id
                    break
        finally:
            await _release_generation_slot(_dedup_hash)

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

        await asyncio.to_thread(
            self.events.append,
            EventEnvelope.create(
                event_type="stream_completed",
                source="orchestrator",
                payload={
                    "model": chosen_model,
                    "task_id": trace_id,
                    "elapsed": duration_ms,
                    "status": "completed",
                    "content": accumulated_text,
                }
            )
        )

        # Record the SUCCESS in the bandit. Before this, stream_generate only
        # fed record_failure (on exceptions) — a model that succeeds only via
        # the streaming path kept successes at 0 while total_requests climbed,
        # the same one-sided blindness generate() had before its own fix
        # (strategy.py scores success_rate = successes / total_requests).
        if not stream_failed:
            try:
                self.router.record_success(model=chosen_model, latency_ms=duration_ms)
            except Exception as exc:
                log.debug("Failed to record success: %s", exc)

        await _release_generation_slot(_dedup_hash)


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













