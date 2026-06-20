# swarm_os/services/agent_service.py
from __future__ import annotations

import json
import logging
import os as _os
import re
import time
from typing import Any, Dict, List, AsyncGenerator

import httpx

from swarm_os.agent_runtime import AgentRuntime
from swarm_os.core.event_bus import event_bus
from swarm_os.services.control_plane import get_router, get_role_pool
from swarm_os.services.control_plane.shared_model_registry import CLOUD_MODEL_SPECS

try:
    from swarm_os.lib.vector.context_retriever import build_context_prompt as _build_ctx
except Exception:
    def _build_ctx(q):
        return q

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

def sort_model_pool_by_provider_policy(models: List[str]) -> List[str]:
    # Preserves ROLE_POOL order: Ollama -> OpenRouter free/fusion -> NVIDIA free -> OpenRouter paid
    openrouter_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
    nvidia_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
    result = []
    for m in models:
        provider = _provider_for_model(m)
        if provider == "ollama":
            result.append(m)
        elif provider == "openrouter" and openrouter_key:
            result.append(m)
        elif provider == "nvidia" and nvidia_key:
            result.append(m)
    return result


def repair_json_string(json_str: str) -> str:
    json_str = json_str.strip()
    # Fix invalid escape sequences from model outputs (e.g. \p, \e, \s)
    json_str = re.sub(r'\\(?!["\\/bfnrtu0-9])', r'\\\\', json_str)
    # Fix unescaped backslashes before any other repair
    try:
        import json as _json
        _json.loads(json_str)
    except _json.JSONDecodeError as _e:
        if 'escape' in str(_e).lower():
            json_str = json_str.replace('\\', 'DBLSLASH')
            json_str = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
            json_str = json_str.replace('DBLSLASH', '\\\\')
    if json_str.startswith("{") and not json_str.endswith("}"):
        json_str += "}"
    elif json_str.startswith("[") and not json_str.endswith("]"):
        json_str += "]" 
    json_str = re.sub(r',\s*([\]}])', r'\1', json_str)
    # Strip extra trailing data after first valid JSON object/array
    if json_str.startswith('{'):
        depth = 0
        for i, ch in enumerate(json_str):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_str = json_str[:i+1]
                    break
    elif json_str.startswith('['):
        depth = 0
        for i, ch in enumerate(json_str):
            if ch == '[': depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    json_str = json_str[:i+1]
                    break
    return json_str

def parse_and_repair_json(json_str: str) -> dict:
    try:
        return json.loads(json_str)
    except Exception:
        repaired = repair_json_string(json_str)
        try:
            return json.loads(repaired)
        except Exception:
            raise

def reconcile_and_repair_tool_call(tool_name: str, payload: dict, available_tools: set[str]) -> tuple[str, dict]:
    mapped_name = tool_name
    new_payload = dict(payload)

    if tool_name not in available_tools:
        if tool_name in {"read_file", "write_file", "patch_file", "file_read", "file_write", "file_patch"}:
            mapped_name = "filesystem"
            op = "read" if "read" in tool_name else "write" if "write" in tool_name else "patch"
            new_payload["operation"] = op
        elif tool_name in {"search", "google_search", "web", "search_web"}:
            mapped_name = "web_search"
        elif tool_name in {"delegate", "handoff", "transfer", "route"}:
            mapped_name = "__delegate__"
        elif tool_name in {"scout", "grep", "cat", "vscode", "vscode_scout"}:
            mapped_name = "vscode_automation"
            if "command" not in new_payload:
                if tool_name == "grep":
                    new_payload["command"] = "grep"
                elif tool_name == "cat":
                    new_payload["command"] = "cat"
                else:
                    new_payload["command"] = "scout"
            if "args" not in new_payload:
                new_payload["args"] = []

    return mapped_name, new_payload

def validate_response(prompt: str, content: str) -> bool:
    content_trimmed = content.strip()
    if not content_trimmed:
        return False
        
    if len(content_trimmed) < 24 and "<tool_call" not in content_trimmed:
        return False
        
    prompt_lower = prompt.lower()
    if "json" in prompt_lower or "schema" in prompt_lower:
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
        else:
            first_brace = content.find('{')
            last_brace = content.rfind('}')
            if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                json_str = content[first_brace:last_brace+1].strip()
            else:
                json_str = content_trimmed

        try:
            json.loads(json_str)
        except Exception:
            return False

    if "<tool_call" in content:
        match = re.search(r'<tool_call name="([^"]+)">\s*(.*?)\s*(?:</tool_call>|$)', content, re.DOTALL)
        if not match:
            return False
            
    return True

def _safe_float(value: str, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return fallback

def _provider_for_model(model_name: str) -> str:
    """Return provider string from cloud registry, default to ollama."""
    for spec in CLOUD_MODEL_SPECS:
        if spec.name == model_name:
            return spec.metadata.get("provider", "ollama")
    return "ollama"

class AgentService:
    def __init__(self, orchestrator: Any, cache: Any = None, settings: Any = None):
        self.orchestrator = orchestrator
        self.cache = cache
        self.settings = settings
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.runtimes: Dict[str, AgentRuntime] = {}
        self.manual_model_override: str | None = None
        self.current_model: str | None = None
        self.router = get_router(include_cloud=True)
        self._setup_default_agents()

    def _setup_default_agents(self) -> None:
        roles = {
            "coordinator": {"role": "coordinator", "description": "Supreme orchestrator. Analyzes intent, delegates to specialists, synthesizes final response.", "model_role": "planner"},
            "planner":     {"role": "planner",     "description": "Decomposes complex tasks into ordered execution steps with dependencies and success criteria.", "model_role": "planner"},
            "executor":    {"role": "executor",    "description": "Executes plan steps autonomously using tools. Reads files, runs searches, writes code, patches files.", "model_role": "deep_coder"},
            "researcher":  {"role": "researcher",  "description": "Deep research agent. Searches web, reads docs, synthesizes findings into actionable intelligence.", "model_role": "reasoning"},
            "coder":       {"role": "coder",       "description": "Elite software engineer. Writes, refactors, debugs, and optimizes code with surgical precision.", "model_role": "deep_coder"},
            "reviewer":    {"role": "reviewer",    "description": "Critical reviewer. Validates code quality, security, performance, and correctness.", "model_role": "reasoning"},
            "tool-runner": {"role": "tool-runner", "description": "Specialized agent for raw tool execution and capability calls.", "model_role": "coder"},
        }
        for agent_id, config in roles.items():
            self.register_agent(agent_id, config)

    def register_agent(self, agent_id: str, config: Dict[str, Any] | None = None) -> None:
        config = config or {}
        self.agents[agent_id] = {
            "id": agent_id,
            "role": config.get("role", "generalist"),
            "description": config.get("description", "A swarm agent"),
            "model_role": config.get("model_role", "general"),
            "config": config,
        }
        self.runtimes[agent_id] = AgentRuntime(config=config)

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        if agent_id not in self.agents:
            raise KeyError(f"Unknown agent_id: {agent_id}")
        return self.agents[agent_id]

    def list_agents(self) -> List[Dict[str, Any]]:
        return list(self.agents.values())

    def set_manual_model(self, model_name: str | None) -> None:
        model_name = (model_name or "").strip()
        self.manual_model_override = model_name or None

    def get_model_state(self) -> Dict[str, Any]:
        return {
            "manual_override": self.manual_model_override,
            "current_model": self.current_model,
            "role_pool": get_role_pool(),
        }

    def _role_for_agent(self, agent: Dict[str, Any], prompt: str) -> str:
        if self.manual_model_override:
            return "manual"
        role = (agent.get("model_role") or "general").lower()
        text = (prompt or "").lower()
        if any(k in text for k in ("code", "refactor", "bug", "patch", "implement", "fix", "algorithm", "function", "class", "script", "python", "javascript", "typescript")):
            return "deep_coder_long"
        if any(k in text for k in ("plan", "strategy", "architect", "design", "analyze", "breakdown", "roadmap")):
            return "planner"
        if any(k in text for k in ("write", "draft", "summarize", "explain", "describe", "essay")):
            return "writer"
        if role in {"planner", "reasoning"}:
            return "planner"
        if role == "coder_small":
            return "coder_small"
        return role if role in get_role_pool() else "reasoning"

    async def _select_model_chain(self, agent: Dict[str, Any], prompt: str) -> List[str]:
        if self.manual_model_override:
            return [self.manual_model_override]
        role = self._role_for_agent(agent, prompt)
        pool = list(get_role_pool().get(role, []))
        if pool:
            return pool
        decision = await self.router.route_model(role=role, allow_fallback=True)
        if decision.model:
            return [decision.model]
        return ["qwen2.5:7b-instruct"]

    def _temperature_for_model(self, model_name: str) -> float:
        env_temp = _safe_float(_os.environ.get("ZENITH_TEMP", "0.15"), 0.15)
        profile = self.router.profiles.get(model_name)
        if profile:
            return min(env_temp, float(profile.preferred_temp))
        if "3b" in model_name:
            return min(env_temp, 0.15)
        if "7b" in model_name or "8b" in model_name:
            return min(env_temp, 0.2)
        return min(env_temp, 0.25)

    def _looks_successful(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        if "<tool_call" in text:
            return True
        return len(text.strip()) >= 24

    def _build_system_instruction(self, agent: Dict[str, Any]) -> str:
        role = agent.get("role", "general")
        agent_id = agent.get("id", "agent")
        desc = agent.get("description", "")

        base = [
            f"You are Zenith-{agent_id.upper()}, an autonomous AI agent in the Swarm OS.",
            f"Role: {role.upper()} — {desc}",
            "",
            "TOOL CALL FORMAT (output EXACTLY, no explanation before calling):",
            '<tool_call name="tool_name">{"param": "value"}</tool_call>',
            "",
            "AVAILABLE TOOLS:",
            "- filesystem: {operation: 'read'|'write'|'patch'|'list', path: '...', content?: '...'}",
            "- vscode_automation: {command: 'grep'|'ls'|'cat'|'scout', args: [...]}",
            "- web_search: {query: '...'}",
            "- ask_user: {question: '...', options?: [...]}",
            "",
        ]

        role_mandates = {
            "coordinator": [
                "COORDINATOR MANDATES:",
                "1. Analyze the user request deeply.",
                "2. Use vscode_automation to scan the codebase first.",
                "3. Produce a direct, comprehensive response.",
                "4. Only delegate if the task requires specialized execution.",
                "5. NEVER delegate simple questions — answer them directly.",
            ],
            "planner": [
                "PLANNER MANDATES:",
                "1. Break the task into numbered steps with clear success criteria.",
                "2. Identify dependencies between steps.",
                "3. Output a structured <plan> block.",
                "4. Be specific — name files, functions, and expected outputs.",
            ],
            "executor": [
                "EXECUTOR MANDATES:",
                "1. Execute tasks autonomously using tools.",
                "2. Read files before editing them.",
                "3. Use grep/ls to understand structure before acting.",
                "4. Write complete, working code — no placeholders.",
                "5. Verify your changes after making them.",
                "6. Report results clearly when done.",
            ],
            "coder": [
                "CODER MANDATES:",
                "1. Write production-quality code only.",
                "2. Read existing code before writing new code.",
                "3. Use filesystem patch for surgical edits.",
                "4. Include error handling in all code.",
                "5. Follow existing code style and patterns.",
            ],
            "researcher": [
                "RESEARCHER MANDATES:",
                "1. Search for current, authoritative information.",
                "2. Cross-reference multiple sources.",
                "3. Synthesize findings into clear, actionable intelligence.",
                "4. Always cite sources in your response.",
            ],
            "reviewer": [
                "REVIEWER MANDATES:",
                "1. Read the code or content being reviewed.",
                "2. Check for bugs, security issues, and performance problems.",
                "3. Provide specific, actionable feedback with line references.",
                "4. Rate overall quality and list top 3 improvements.",
            ],
        }

        mandates = role_mandates.get(role, [
            "CORE MANDATES:",
            "1. Use tools to gather information before responding.",
            "2. Be direct and precise.",
            "3. Complete the task fully.",
        ])

        instruction = base + mandates + ["", "Think autonomously. Act decisively. Report clearly."]
        return "\n".join(instruction)

    async def _query_openai_compat_stream(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        messages: List[dict],
        temperature: float,
        agent_id: str,
        extra_headers: Dict[str, str] | None = None,
    ) -> tuple[str, List[dict]]:
        """Generic OpenAI-compatible streaming client for OpenRouter, NVIDIA, Gemini."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }

        full_content = ""
        chunks: List[dict] = []

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", f"{base_url}/chat/completions", json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        line = line[6:].strip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        data = json.loads(line)
                        piece = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if piece:
                            full_content += piece
                            chunks.append({"agent_id": agent_id, "content": piece, "model": model_name})
                    except Exception:
                        pass

        return full_content, chunks

    async def _query_gemini_stream(
        self,
        model_name: str,
        messages: List[dict],
        temperature: float,
        agent_id: str,
    ) -> tuple[str, List[dict]]:
        """Gemini via OpenAI-compatible endpoint."""
        api_key = _os.environ.get("GEMINI_API_KEY", "")
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        return await self._query_openai_compat_stream(
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            agent_id=agent_id,
        )

    async def _query_ollama_stream(
        self,
        model_name: str,
        messages: List[dict],
        temperature: float,
        agent_id: str,
    ) -> tuple[str, List[dict]]:
        """Route to correct provider based on model registry."""
        provider = _provider_for_model(model_name)

        if provider == "openrouter":
            api_key = _os.environ.get("OPENROUTER_API_KEY", "")
            base_url = _os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            return await self._query_openai_compat_stream(
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                agent_id=agent_id,
                extra_headers={"HTTP-Referer": "https://github.com/v-horseshoe-v2", "X-Title": "Swarm OS"},
            )

        if provider == "nvidia":
            api_key = _os.environ.get("NVIDIA_API_KEY", "")
            base_url = _os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
            return await self._query_openai_compat_stream(
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                agent_id=agent_id,
            )

        if provider == "gemini":
            return await self._query_gemini_stream(model_name, messages, temperature, agent_id)

        # Default: Ollama (local or cloud models like qwen3-coder:480b-cloud)
        chunks: List[dict] = []
        full_content = ""
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream(
                "POST",
                "http://127.0.0.1:11434/api/chat",
                json={
                    "model": model_name,
                    "messages": messages,
                    "stream": True,
                    "keep_alive": 0,
                    "options": {"temperature": temperature, "num_ctx": 4096},
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                    except Exception:
                        continue
                    if evt.get("done"):
                        break
                    piece = evt.get("message", {}).get("content", "")
                    if piece:
                        full_content += piece
                        chunks.append({"agent_id": agent_id, "content": piece, "model": model_name})

        return full_content, chunks

    async def _unload_model(self, model_name: str) -> None:
        if _provider_for_model(model_name) != "ollama":
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post("http://127.0.0.1:11434/api/generate", json={"model": model_name, "prompt": "", "keep_alive": 0})
        except Exception:
            pass

    async def _list_running_models(self) -> List[dict]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get("http://127.0.0.1:11434/api/ps")
                response.raise_for_status()
                return response.json().get("models", [])
        except Exception:
            return []

    async def step_agent_stream(
        self,
        agent_id: str,
        prompt: str,
        history: List[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        agent = self.get_agent(agent_id)
        runtime = self.runtimes[agent_id]
        history = history or []

        if isinstance(prompt, str):
            raw = prompt.strip()
            lowered = raw.lower().rstrip("?").strip()

            # Health-check intent detection (Requirement 9)
            if lowered in {"are you working", "hello", "test", "ping", "status"}:
                logger.info(f"[HEALTH CHECK] Responding immediately to health check prompt: '{raw}'")
                yield {"agent_id": agent_id, "content": "I am online, operational, and ready to assist you.", "model": "health-check"}
                yield {"agent_id": agent_id, "type": "final", "model": "health-check", "content": "I am online, operational, and ready to assist you."}
                return

            # Model commands
            lowered_raw = raw.lower()
            if lowered_raw in ("model", "/model", "model show"):
                yield {"agent_id": agent_id, "type": "model_state", "model_state": self.get_model_state(), "running_models": await self._list_running_models()}
                return
            if lowered_raw.startswith("model set ") or lowered_raw.startswith("/model set "):
                chosen = raw.split("set", 1)[1].strip()
                self.set_manual_model(chosen)
                yield {"agent_id": agent_id, "type": "model_state", "message": f"Manual model override set to {chosen}", "model_state": self.get_model_state()}
                return
            if lowered_raw in ("model auto", "/model auto", "model clear"):
                self.set_manual_model(None)
                yield {"agent_id": agent_id, "type": "model_state", "message": "Auto model selection restored.", "model_state": self.get_model_state()}
                return

        system_msg = self._build_system_instruction(agent)
        user_prompt = _build_ctx(prompt) if prompt else prompt
        messages = [{"role": "system", "content": system_msg}] + history
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})

        step_limit = 5
        active_model = None

        for step_idx in range(step_limit):
            # Select model chain or reuse previous active model
            if active_model:
                model_chain = [active_model]
            else:
                model_chain = await self._select_model_chain(agent, prompt)
                model_chain = sort_model_pool_by_provider_policy(model_chain)

            requested_role = self._role_for_agent(agent, prompt)

            if step_idx == 0:
                yield {"agent_id": agent_id, "type": "model_plan", "requested_role": requested_role, "model_chain": model_chain}

            success = False
            for attempt, chosen_model in enumerate(model_chain, start=1):
                self.current_model = chosen_model
                temperature = self._temperature_for_model(chosen_model)
                started = time.perf_counter()
                provider = _provider_for_model(chosen_model)

                yield {"agent_id": agent_id, "type": "model_selected", "model": chosen_model, "provider": provider, "attempt": attempt, "temperature": temperature}
                logger.info(f"[PROVIDER SELECT] Attempt {attempt} (Step {step_idx+1}): Running model '{chosen_model}' via provider '{provider}' (Temp: {temperature})")

                try:
                    full_chunk_content, chunks = await self._query_ollama_stream(chosen_model, messages, temperature, agent_id)

                    for chunk in chunks:
                        yield chunk

                    latency_ms = (time.perf_counter() - started) * 1000.0
                    self.router.record_success(chosen_model, latency_ms)
                    await self._unload_model(chosen_model)

                    # Validate response
                    if not validate_response(prompt, full_chunk_content):
                        reason = "empty_or_weak_response"
                        if not full_chunk_content.strip():
                            reason = "empty_response"
                        elif len(full_chunk_content.strip()) < 24 and "<tool_call" not in full_chunk_content:
                            reason = "weak_response"
                        elif "<tool_call" in full_chunk_content and not re.search(r'<tool_call name="([^"]+)">\s*(.*?)\s*(?:</tool_call>|$)', full_chunk_content, re.DOTALL):
                            reason = "malformed_tool_call"
                        elif "json" in prompt.lower() or "schema" in prompt.lower():
                            reason = "invalid_structured_output"

                        logger.warning(f"[RESPONSE VALIDATION FAILED] Model '{chosen_model}' failed validation: {reason}")
                        yield {"agent_id": agent_id, "type": "model_escalation", "from_model": chosen_model, "reason": reason}
                        continue

                    active_model = chosen_model
                    success = True
                    break

                except Exception as exc:
                    logger.exception(f"[PROVIDER FAILOVER] Model '{chosen_model}' attempt failed: {exc}")
                    
                    is_deterministic_4xx = False
                    if isinstance(exc, httpx.HTTPStatusError):
                        status_code = exc.response.status_code
                        if 400 <= status_code < 500:
                            is_deterministic_4xx = True
                            logger.error(f"[DETERMINISTIC FAILURE] Model '{chosen_model}' returned {status_code}. Cooling down model.")

                    cooldown = 3600.0 if is_deterministic_4xx else None
                    self.router.record_failure(chosen_model, cooldown_seconds=cooldown)
                    await self._unload_model(chosen_model)
                    yield {"agent_id": agent_id, "type": "model_escalation", "from_model": chosen_model, "reason": str(exc)}

            if not success:
                logger.error(f"[EXECUTION FAILURE] All model attempts failed at step {step_idx+1}.")
                yield {"agent_id": agent_id, "type": "final", "model": self.current_model, "content": "All model attempts failed."}
                return

            messages.append({"role": "assistant", "content": full_chunk_content})

            # Check for tool call
            match = re.search(r'<tool_call name="([^"]+)">\s*(.*?)\s*(?:</tool_call>|$)', full_chunk_content, re.DOTALL)
            if not match:
                logger.info(f"[EXECUTION SUCCESS] Final response generated by model '{active_model}'.")
                yield {"agent_id": agent_id, "type": "final", "model": active_model, "provider": _provider_for_model(active_model), "content": full_chunk_content}
                return

            tool_name = match.group(1).strip()
            raw_payload_str = match.group(2).strip()

            try:
                # Sanitize invalid escape sequences before parsing (common in NVIDIA/cloud model outputs)
                raw_payload_str = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu0-9])', r'\\\\', raw_payload_str)
                payload = parse_and_repair_json(raw_payload_str)
            except Exception as exc:
                logger.error(f"[TOOL CALL FAILURE] Model '{active_model}' generated unrepairable tool payload JSON: {exc}")
                yield {"agent_id": agent_id, "type": "model_escalation", "from_model": active_model, "reason": f"invalid_tool_json: {exc}"}
                active_model = None
                continue

            available_tools = set(runtime.list_tools()) if hasattr(runtime, "list_tools") else set()
            mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)

            if mapped_tool_name == "__delegate__":
                logger.warning(f"[DELEGATE PAYLOAD] {payload}")
                target = payload.get("target_agent", "executor")
                task = payload.get("task") or payload.get("content") or payload.get("message") or payload.get("instruction") or str(payload)
                logger.info(f"[DELEGATE] Coordinator -> {target}: {task}")
                # Yield handoff event so CLI shows delegation
                yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": target, "task": task}
                # Run the target agent with the delegated task
                if target in self.agents:
                    delegate_history = list(messages)
                    async for sub_chunk in self.step_agent_stream(target, task, history=delegate_history):
                        # Re-tag with original agent_id for CLI continuity
                        sub_chunk["delegated_by"] = agent_id
                        yield sub_chunk
                else:
                    yield {"agent_id": agent_id, "type": "final", "model": active_model, "provider": _provider_for_model(active_model), "content": f"Agent '{target}' not found. Task: {task}"}
                return
            if mapped_tool_name not in available_tools:
                logger.warning(f"[TOOL CALL FAILURE] Model '{active_model}' called unknown tool '{tool_name}' (mapped to '{mapped_tool_name}')")
                yield {"agent_id": agent_id, "type": "model_escalation", "from_model": active_model, "reason": f"unknown_tool: {mapped_tool_name}"}
                active_model = None
                continue

            logger.info(f"[TOOL CALL RUNNING] Executing tool '{mapped_tool_name}' with payload: {payload}")
            try:
                result = await runtime.call_tool(mapped_tool_name, payload or {})
                logger.info(f"[TOOL CALL SUCCESS] Tool '{mapped_tool_name}' succeeded.")
            except Exception as e:
                result = {"error": str(e)}
                logger.error(f"[TOOL CALL ERROR] Tool '{mapped_tool_name}' failed: {e}")

            yield {"agent_id": agent_id, "type": "tool_result", "model": active_model, "tool": mapped_tool_name, "result": result}
            # Serialize tool result safely — handle dataclasses, Pydantic models, and custom response objects
            def _serialize(obj):
                if isinstance(obj, dict): return obj
                if hasattr(obj, "model_dump"): return obj.model_dump()
                if hasattr(obj, "__dict__"): return obj.__dict__
                return str(obj)
            messages.append({"role": "user", "content": f"Observation: {json.dumps(_serialize(result), ensure_ascii=False)}"})

        logger.warning("[EXECUTION WARNING] Step limit reached without generating a final response.")
        yield {"agent_id": agent_id, "type": "final", "model": active_model, "content": "Execution step limit reached."}


