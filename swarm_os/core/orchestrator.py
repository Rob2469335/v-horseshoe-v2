from __future__ import annotations
import re
import os as _os
import logging
import time
import json
import asyncio
import httpx

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
        self.total_tokens_used = 0
        self.max_tokens_budget = 500000

        self.router = Router(
            profiles=[
                ModelProfile(name="qwen2.5:3b-instruct", role="fast", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:7b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen3:7b", role="reasoning", max_tokens=32000),
                
                ModelProfile(name="qwen2.5-coder:3b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:7b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:7b", role="coding", max_tokens=32000),
                ModelProfile(name="qwen2.5-coder:7b-32k", role="coding", max_tokens=32768),
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
        if any(re.search(r"\b" + re.escape(m.strip()) + r"\b", last_content) for m in coding_markers):
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
            return ["qwen3:7b", "qwen2.5-coder:7b"]

    def _parse_tool_call(self, text: str) -> tuple[str, str] | None:
        # Check Pattern A: <tool_call name="tool">params</tool_call>
        match_a = re.search(r'<tool_call\s+name="([^"]+)">\s*(\{.*?\})\s*</tool_call>', text, re.DOTALL)
        if match_a:
            return match_a.group(1).strip(), match_a.group(2).strip()

        # Check Pattern B: <tool>tool</tool> params
        match_b = re.search(r'<tool>([^<]+)</tool>\s*(\{.*?\})', text, re.DOTALL)
        if match_b:
            return match_b.group(1).strip(), match_b.group(2).strip()

        # Check Pattern C: plain JSON object output
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    if "tool" in obj and isinstance(obj["tool"], str):
                        params = obj.get("params", {})
                        return obj["tool"].strip(), json.dumps(params)
                    if "tool_name" in obj and isinstance(obj["tool_name"], str):
                        params = obj.get("params", {})
                        return obj["tool_name"].strip(), json.dumps(params)
                    _cmd_val = obj.get("command", "")
                    _CLI_ONLY = {"/goal", "/plan", "/debug", "/compress", "/boot", "/exit", "/debate", "/chat", "/agents", "/tokens", "/trace", "/compress", "/boot", "/exit", "/clear", "/model", "/focus"}
                    if ("command" in obj and isinstance(_cmd_val, str)
                            and _cmd_val.startswith("/")
                            and not any(_cmd_val.startswith(c) for c in _CLI_ONLY)):
                        return "command", json.dumps(obj)
            except Exception:
                pass

        return None

    def _detect_provider(self, model_name: str) -> str:
        """Detect the intended provider for a given model name.
        
        This is purely name-based classification. API key availability
        is checked separately in generate()/stream_generate() to decide
        whether to fall back to a local model.
        """
        if not model_name:
            return "ollama"
        name_lower = model_name.lower()
        if "glm" in name_lower:
            return "glm"
        # Models with '/' are cloud models
        if "/" in model_name:
            # NVIDIA NIM models (nvidia/* and meta/* on NVIDIA)
            if model_name.startswith("nvidia/") or model_name.startswith("meta/"):
                return "nvidia"
            # Everything else with '/' is an OpenRouter model
            return "openrouter"
        if "openrouter" in name_lower or "deepseek" in name_lower:
            return "openrouter"
        return "ollama"

    async def _cloud_generate(self, model: str, messages: list[dict], provider: str, stream: bool = False):
        """Call a cloud provider (OpenRouter or NVIDIA NIM) for generation."""
        if provider == "openrouter":
            api_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
            base_url = _os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/v-horseshoe-v2",
                "X-Title": "Swarm OS",
            }
        elif provider == "nvidia":
            api_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
            base_url = _os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
            url = f"{base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        else:
            raise ValueError(f"Unknown cloud provider: {provider}")

        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": 0.7,
            "max_tokens": 1500,
        }

        if not stream:
            async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        else:
            # Return an async generator for streaming
            return self._cloud_stream_generate(url, payload, headers)

    async def _cloud_stream_generate(self, url: str, payload: dict, headers: dict):
        """Async generator that streams from a cloud provider."""
        async with httpx.AsyncClient(timeout=300.0, verify=False) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        line = line[6:].strip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        data = json.loads(line)
                        chunk = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if chunk:
                            yield chunk
                    except Exception:
                        pass

    async def _get_memory_context(self, query: str) -> str:
        return await self.bridge.get_memory_context(query)

    async def generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None) -> tuple[str, str]:
        messages = list(messages or [])
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
            "You have access to the following tools. STOP YAPPING AND START ACTING. You MUST use the exact XML tags below to execute a tool. Do not just write out your plan. To call a tool, generate one of the following formats:\n"
            '<tool_call name="tool_name">{"arg": "val"}</tool_call>\n'
            '<tool>tool_name</tool> {"arg": "val"}\n\n'
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

        chosen_model = route_decision.model or "qwen3:7b"

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
        print(f"[Orchestrator] generate() provider={provider} for model={chosen_model}", flush=True)

        # If the provider is cloud but no API key is available, fall back to a local model
        if provider in ("openrouter", "nvidia") and not _os.environ.get(
            "OPENROUTER_API_KEY" if provider == "openrouter" else "NVIDIA_API_KEY", ""
        ).strip():
            print(f"[Orchestrator] No API key for {provider}, falling back to local model", flush=True)
            chosen_model = "qwen3:7b"
            provider = "ollama"

        max_steps = 5
        final_result = ""
        handled_tool_keys: set[str] = set()  # Track handled tool calls to detect repeats

        for step in range(max_steps):
            try:
                # Token budget check
                if self.total_tokens_used >= self.max_tokens_budget:
                    raise ValueError(f"Token budget exceeded: {self.total_tokens_used} used (limit {self.max_tokens_budget})")

                print(f"[Orchestrator] generate() Turn {step + 1}/{max_steps} starting with model={chosen_model} provider={provider}", flush=True)

                # Dispatch to the correct provider
                if provider in ("openrouter", "nvidia"):
                    result = await self._cloud_generate(model=chosen_model, messages=messages, provider=provider, stream=False)
                else:
                    result = await self.ollama.generate(model=chosen_model, messages=messages)

                print(f"[Orchestrator] generate() Turn result: {result!r}", flush=True)
                
                # Update tokens used
                self.total_tokens_used += int(len(result) / 4) + 1

                tool_info = self._parse_tool_call(result)
                if not tool_info:
                    final_result = result
                    print(f"[Orchestrator] Plain-text response received. Exiting loop.", flush=True)
                    break
                if tool_info:
                    tool_name, params_str = tool_info
                    # Build a dedup key from tool name + params
                    dedup_key = f"{tool_name}:{params_str}"

                    if dedup_key in handled_tool_keys:
                        # This exact tool call was already handled — stop looping
                        print(f"[Orchestrator] DUPLICATE tool call detected: {tool_name}. Breaking loop.", flush=True)
                        final_result = f"The command was already handled. {result}"
                        break

                    print(f"[Orchestrator] Intercepted tool call in generate(): {tool_name} with params: {params_str}", flush=True)
                    try:
                        params = json.loads(params_str)
                    except Exception as e:
                        params = {}
                        print(f"[Orchestrator] Failed to parse tool params JSON: {e}", flush=True)
                    
                    # Execute tool
                    print(f"[Orchestrator] Executing tool {tool_name}...", flush=True)
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
                    print(f"[Orchestrator] Tool execution result: {observation}", flush=True)

                    # Mark this tool call as handled
                    handled_tool_keys.add(dedup_key)
                    
                    # Critic evaluation
                    critic_res = self.critic.evaluate_step(observation, expected_kind="tool")
                    if not critic_res.accepted:
                        print(f"[Orchestrator] Critic rejected tool execution: {critic_res.reason}", flush=True)
                        messages.append({"role": "assistant", "content": result})
                        messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{json.dumps(observation)}\n\nThe slash command has already been handled. Continue with the next assistant response directly and do not call the same slash command again unless the user explicitly asks for a rerun."})
                        messages.append({
                            "role": "user",
                            "content": f"Critic Feedback: The tool execution returned an error or was rejected: {critic_res.reason}. Please correct the parameters and call the tool again."
                        })
                        continue

                    # For handled slash commands, return immediately instead of continuing
                    # The command shim is a terminal action — no need to re-prompt the model
                    if tool_name == "command":
                        print(f"[Orchestrator] Slash command handled. Returning immediately.", flush=True)
                        final_result = observation.get("result", result)
                        messages.append({"role": "assistant", "content": result})
                        messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{json.dumps(observation)}\n\nContinue with the next assistant response."})
                        return final_result, model
                    else:
                        # Tool succeeded — append observation and let model continue
                        messages.append({"role": "assistant", "content": result})
                        messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{json.dumps(observation)}\n\nContinue with the next step."})
                        continue
            except Exception as exc:
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

        return final_result, chosen_model


    async def stream_generate(self, model: str | None, messages: list[dict] | None = None, prompt: str | None = None):
        log.info("[Orchestrator] Entering stream_generate. model=%s, prompt=%s, messages=%s", model, prompt, messages)
        print(f"[Orchestrator] Entering stream_generate. model={model}, prompt={prompt}", flush=True)
        messages = list(messages or [])
        
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
            yield f"\n[Error: No input provided]", "none", "none"
            return

        # Dynamic Tool Schema Discovery & Injection
        schemas = self.mcp.get_tools_schema()
        schemas_str = json.dumps(schemas, indent=2)
        tools_instruction = (
            "\n### Available MCP Tools:\n"
            "You have access to the following tools. STOP YAPPING AND START ACTING. You MUST use the exact XML tags below to execute a tool. Do not just write out your plan. To call a tool, generate one of the following formats:\n"
            '<tool_call name="tool_name">{"arg": "val"}</tool_call>\n'
            '<tool>tool_name</tool> {"arg": "val"}\n\n'
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

        chosen_model = route_decision.model or "qwen3:7b"

        log.info("[Orchestrator] Routing decision: %s. Starting Ollama stream...", chosen_model)
        
        # Detect provider for the chosen model
        provider = self._detect_provider(chosen_model)
        print(f"[Orchestrator] stream_generate() provider={provider} for model={chosen_model}", flush=True)

        # If the provider is cloud but no API key is available, fall back to a local model
        if provider in ("openrouter", "nvidia") and not _os.environ.get(
            "OPENROUTER_API_KEY" if provider == "openrouter" else "NVIDIA_API_KEY", ""
        ).strip():
            print(f"[Orchestrator] No API key for {provider}, falling back to local model", flush=True)
            chosen_model = "qwen3:7b"
            provider = "ollama"

        max_steps = 5
        handled_tool_keys: set[str] = set()  # Track handled tool calls to detect repeats

        for step in range(max_steps):
            # Token budget check
            if self.total_tokens_used >= self.max_tokens_budget:
                yield f"\n[Error: Token budget exceeded ({self.total_tokens_used} used)]", chosen_model, trace_id
                break

            print(f"[Orchestrator] stream_generate() Turn {step + 1}/{max_steps} starting with model={chosen_model} provider={provider}", flush=True)
            accumulated_text = ""
            try:
                # Dispatch to the correct provider
                if provider in ("openrouter", "nvidia"):
                    stream_gen = await self._cloud_generate(model=chosen_model, messages=messages, provider=provider, stream=True)
                    async for chunk in stream_gen:
                        accumulated_text += chunk
                        yield chunk, chosen_model, trace_id
                else:
                    async for chunk in self.ollama.stream_generate(model=chosen_model, messages=messages):
                        accumulated_text += chunk
                        yield chunk, chosen_model, trace_id
                
                # Update tokens used
                self.total_tokens_used += int(len(accumulated_text) / 4) + 1

                tool_info = self._parse_tool_call(accumulated_text)
                if tool_info:
                    tool_name, params_str = tool_info
                    dedup_key = f"{tool_name}:{params_str}"

                    if dedup_key in handled_tool_keys:
                        print(f"[Orchestrator] DUPLICATE tool call detected in stream: {tool_name}. Breaking loop.", flush=True)
                        yield f"\n[System: Duplicate tool call detected. Stopping loop.]\n", chosen_model, trace_id
                        break

                    print(f"[Orchestrator] Intercepted tool call in stream_generate(): {tool_name} with params: {params_str}", flush=True)
                    try:
                        params = json.loads(params_str)
                    except Exception as e:
                        params = {}
                        print(f"[Orchestrator] Failed to parse tool params JSON: {e}", flush=True)
                    
                    # Execute tool
                    print(f"[Orchestrator] Executing tool {tool_name}...", flush=True)
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
                    print(f"[Orchestrator] Tool execution result: {observation}", flush=True)

                    # Mark this tool call as handled
                    handled_tool_keys.add(dedup_key)
                    
                    # Critic evaluation
                    critic_res = self.critic.evaluate_step(observation, expected_kind="tool")
                    if not critic_res.accepted:
                        print(f"[Orchestrator] Critic rejected tool execution: {critic_res.reason}", flush=True)
                        obs_text = f"\n[Critic Rejection: {critic_res.reason}. Requesting self-correction...]\n"
                        yield obs_text, chosen_model, trace_id
                        
                        messages.append({"role": "assistant", "content": accumulated_text})
                        messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{json.dumps(observation)}\n\nThe slash command has already been handled. Continue with the next assistant response directly and do not call the same slash command again unless the user explicitly asks for a rerun."})
                        messages.append({
                            "role": "user",
                            "content": f"Critic Feedback: The tool execution returned an error or was rejected: {critic_res.reason}. Please correct the parameters and call the tool again."
                        })
                        continue

                    # For handled slash commands, break immediately — the shim is terminal
                    if tool_name == "command":
                        print(f"[Orchestrator] Slash command handled in stream. Continuing.", flush=True)
                        obs_text = f"\n[Observation: {json.dumps(observation)}]\n"
                        yield obs_text, chosen_model, trace_id
                        messages.append({"role": "assistant", "content": accumulated_text})
                        messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{json.dumps(observation)}\n\nContinue with the next assistant response."})
                        continue
                    # Yield observation back to the stream so the client receives it
                    obs_text = f"\n[Observation: {json.dumps(observation)}]\n"
                    yield obs_text, chosen_model, trace_id
                    
                    # Inject back into history for non-command tools
                    messages.append({"role": "assistant", "content": accumulated_text})
                    messages.append({"role": "user", "content": f"TOOL OBSERVATION:\n{json.dumps(observation)}\n\nContinue with the next assistant response."})
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











