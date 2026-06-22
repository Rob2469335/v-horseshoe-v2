# swarm_os/services/agent_service.py
from __future__ import annotations

import json
import logging
import os as _os
import re
from typing import Any, Dict, List, AsyncGenerator

import httpx

from swarm_os.agent_runtime import AgentRuntime
from swarm_os.core.event_bus import event_bus

try:
    from swarm_os.lib.vector.context_retriever import build_context_prompt as _build_ctx
except Exception:
    def _build_ctx(q):
        return q


logger = logging.getLogger(__name__)


_LIVE_OPENROUTER_MODELS = None
_LIVE_NVIDIA_MODELS = None
_LAST_FETCH_TIME = 0.0
_CACHE_DURATION = 21600.0  # 6 hours

async def fetch_live_models_if_needed():
    global _LIVE_OPENROUTER_MODELS, _LIVE_NVIDIA_MODELS, _LAST_FETCH_TIME
    import time
    now = time.time()
    if (
        _LIVE_OPENROUTER_MODELS is not None 
        and _LIVE_NVIDIA_MODELS is not None 
        and (now - _LAST_FETCH_TIME) < _CACHE_DURATION
    ):
        return

    # Fetch OpenRouter models
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                _LIVE_OPENROUTER_MODELS = [m["id"] for m in data.get("data", [])]
            else:
                _LIVE_OPENROUTER_MODELS = []
    except Exception as e:
        logger.warning(f"Failed to fetch live OpenRouter models: {e}")
        if _LIVE_OPENROUTER_MODELS is None:
            _LIVE_OPENROUTER_MODELS = []

    # Fetch Nvidia models
    api_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
    if api_key:
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                resp = await client.get("https://integrate.api.nvidia.com/v1/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    _LIVE_NVIDIA_MODELS = [m["id"] for m in data.get("data", [])]
                else:
                    _LIVE_NVIDIA_MODELS = []
        except Exception as e:
            logger.warning(f"Failed to fetch live Nvidia models: {e}")
            if _LIVE_NVIDIA_MODELS is None:
                _LIVE_NVIDIA_MODELS = []
    else:
        _LIVE_NVIDIA_MODELS = []

    _LAST_FETCH_TIME = now


def _resolve_runtime_config(agent: dict):
    model = _os.environ.get("ZENITH_MODEL")
    temp = _os.environ.get("ZENITH_TEMP", "0.7")

    try:
        temp = float(temp)
    except Exception:
        temp = 0.7

    role = agent.get("model_role", "fast")

    if not model or not model.strip():
        model = "qwen3:14b" if role == "reasoning" else "qwen2.5:7b-instruct"

    return model, temp


STEP_LIMIT = 2


class AgentService:
    def __init__(self, orchestrator: Any, cache: Any = None, settings: Any = None):
        self.orchestrator = orchestrator
        self.cache = cache
        self.settings = settings
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.runtimes: Dict[str, AgentRuntime] = {}

        self._setup_default_agents()

    def _setup_default_agents(self) -> None:
        roles = {
            "coordinator": {
                "role": "coordinator",
                "description": "Main orchestrator for task delegation and context management.",
                "model_role": "reasoning",
            },
            "planner": {
                "role": "planner",
                "description": "Breaks down complex prompts into actionable plans.",
                "model_role": "reasoning",
            },
            "executor": {
                "role": "executor",
                "description": "Executes specific plan steps and aggregates results.",
                "model_role": "fast",
            },
            "tool-runner": {
                "role": "tool-runner",
                "description": "Specialized agent for executing capability and tool calls.",
                "model_role": "fast",
            },
        }
        for agent_id, config in roles.items():
            self.register_agent(agent_id, config)

    def register_agent(self, agent_id: str, config: Dict[str, Any] | None = None) -> None:
        config = config or {}
        self.agents[agent_id] = {
            "id": agent_id,
            "role": config.get("role", "generalist"),
            "description": config.get("description", "A swarm agent"),
            "model_role": config.get("model_role", "fast"),
            "config": config,
        }
        self.runtimes[agent_id] = AgentRuntime(config=config)

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        if agent_id not in self.agents:
            raise KeyError(f"Unknown agent_id: {agent_id}")
        return self.agents[agent_id]

    def list_agents(self) -> List[Dict[str, Any]]:
        return list(self.agents.values())

    def _build_system_instruction(self, agent: Dict[str, Any]) -> str:
        instruction = [
            f"You are Zenith (ID: {agent['id']}), a senior agentic software engineer.",
            f"Role: {agent['role'].upper()} - {agent['description']}",
            "",
            "CORE MANDATES:",
            "1. Tool Supremacy: NEVER explain what you will do. ALWAYS use a tool first.",
            "2. Surgical Precision: Use 'filesystem' with 'patch' for code edits.",
            "3. Autonomous Research: Use 'vscode_automation' (scout/grep/ls) to verify state.",
            "4. Groundedness: Use 'web_search' for any external information.",
            "",
            "ZENITH PROTOCOL:",
            "- Use <strategic_intent> to state your immediate goal.",
            '- Use <topic_update title="..." summary="..."> for new phases.',
            "- Use <plan> for multi-step reasoning.",
            "",
            "TOOL CALL FORMAT:",
            "Output EXACTLY this on its own line to call a tool:",
            '<tool_call name="tool_name">{"param": "value"}</tool_call>',
            "",
            "AVAILABLE TOOLS:",
            "- filesystem: {operation: 'read'|'write'|'patch'|'list'|'grep', path: '...', content?: '...', old?: '...', new?: '...', pattern?: '...'}",
            "- vscode_automation: {command: 'cat'|'grep'|'ls'|'find'|'scout', args: [...]}",
            "- sandbox_repl: {language: 'python'|'powershell'|'pytest', code?: '...', command?: '...', path?: '...'}",
            "- web_search: {query: '...'}",
            "- ask_user: {question: '...', options?: [...]}",
            "- playwright: {operation: 'goto'|'click'|'type'|'content'|'screenshot', url: '...', selector?: '...', text?: '...'}",
            "- context7: {operation: 'read'|'write'|'clear', session_id: '...', content?: '...', limit?: 10}",
            "- delegate: {target_agent: 'planner'|'executor'|'coder'|'tool-runner', task: '...'}",
            "",
            "Think step-by-step. Be concise. Be agentic.",
        ]
        return "\n".join(instruction)

    async def step_agent_stream(
        self,
        agent_id: str,
        prompt: str,
        history: List[dict] | None = None,
        delegation_chain: List[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
        agent = self.get_agent(agent_id)
        runtime = self.runtimes[agent_id]
        history = history or []
        delegation_chain = delegation_chain or [agent_id]

        system_msg = self._build_system_instruction(agent)
        messages = [{"role": "system", "content": system_msg}] + history
        if prompt:
            messages.append({"role": "user", "content": _build_ctx(prompt)})

        # Check if the last assistant proposed action is approved or denied
        if history and len(history) >= 2:
            last_msg = history[-1]
            proposed_msg = history[-2]
            
            is_approve = False
            is_deny = False
            if last_msg.get("role") == "user":
                content = last_msg.get("content", "")
                if "approve" in content.lower():
                    is_approve = True
                elif "deny" in content.lower():
                    is_deny = True
            
            if proposed_msg.get("role") == "assistant":
                proposed_content = proposed_msg.get("content", "")
                match = re.search(
                    r'<tool_call name="([^"]+)">\s*(\{.*?\})\s*</tool_call>',
                    proposed_content,
                    re.DOTALL,
                )
                if match:
                    prop_tool = match.group(1).strip()
                    try:
                        prop_payload = json.loads(match.group(2).strip())
                    except Exception:
                        prop_payload = {}
                    
                    available_tools = set(runtime.list_tools())
                    mapped_prop_tool, prop_payload = reconcile_and_repair_tool_call(prop_tool, prop_payload, available_tools)
                    
                    if is_approve:
                        logger.info(f"Executing approved tool call '{mapped_prop_tool}' immediately.")
                        # Register approval with runtime
                        runtime.approved_actions.append({
                            "tool": mapped_prop_tool,
                            "payload": prop_payload
                        })
                        try:
                            result = await runtime.call_tool(mapped_prop_tool, prop_payload)
                            yield {
                                "agent_id": agent_id,
                                "type": "tool_result",
                                "tool": mapped_prop_tool,
                                "result": result,
                            }
                            messages.append({"role": "tool", "content": f"Observation: {json.dumps(result, ensure_ascii=False)}"})
                        except Exception as e:
                            logger.error(f"Error executing approved tool: {e}")
                            messages.append({"role": "tool", "content": f"Observation: {json.dumps({'error': str(e)})}"})
                    elif is_deny:
                        logger.info(f"Tool call '{mapped_prop_tool}' was denied by user.")
                        messages.append({"role": "tool", "content": "Observation: User denied approval for this operation."})

        def resolve_model_and_provider(agent_id_arg: str) -> tuple[str, str]:
            env_model = _os.environ.get("ZENITH_MODEL")
            if env_model and env_model.strip():
                if "/" in env_model or any(x in env_model.lower() for x in ["openrouter", "deepseek"]):
                    return env_model.strip(), "openrouter"
                elif "nvidia" in env_model.lower():
                    return env_model.strip(), "nvidia"
                elif "gemini" in env_model.lower():
                    return env_model.strip(), "gemini"
                else:
                    return env_model.strip(), "ollama"

            from swarm_os.services.control_plane.shared_model_registry import CLOUD_MODEL_SPECS
            nvidia_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
            openrouter_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
            logger.warning(f"DEBUG ENV KEYS: nvidia_key len={len(nvidia_key)} starts={nvidia_key[:10]} | openrouter_key len={len(openrouter_key)}")

            if agent_id_arg in ("executor", "coder", "tool-runner"):
                if nvidia_key:
                    for spec in CLOUD_MODEL_SPECS:
                        if spec.metadata.get("provider") == "nvidia" and "code" in spec.capabilities and spec.role == "cloud_coder":
                            return spec.name, "nvidia"
                if openrouter_key:
                    for spec in CLOUD_MODEL_SPECS:
                        if spec.metadata.get("provider") == "openrouter" and "code" in spec.capabilities and "free" in spec.name:
                            return spec.name, "openrouter"
                return "qwen2.5-coder:7b", "ollama"
            else:
                if nvidia_key:
                    for spec in CLOUD_MODEL_SPECS:
                        if spec.metadata.get("provider") == "nvidia" and "reasoning" in spec.capabilities and spec.role == "cloud_pro_nvidia":
                            return spec.name, "nvidia"
                if openrouter_key:
                    for spec in CLOUD_MODEL_SPECS:
                        if spec.metadata.get("provider") == "openrouter" and "reasoning" in spec.capabilities and "free" in spec.name:
                            return spec.name, "openrouter"
                return "qwen2.5:3b-instruct", "ollama"

        # Resolve initial configs
        _, temperature = _resolve_runtime_config(agent)

        # 1. 480cloud
        fallback_chain = [("qwen3-coder:480b-cloud", "ollama")]

        # 2. Nvidia big models
        predefined_nvidia = [
            ("meta/llama-3.1-405b-instruct", "nvidia"),
            ("meta/llama-3.3-70b-instruct", "nvidia"),
            ("nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter"),
            ("nvidia/nemotron-3-super-120b-a12b:free", "openrouter"),
        ]

        try:
            await fetch_live_models_if_needed()
        except Exception as e:
            logger.warning(f"Error fetching live models: {e}")

        global _LIVE_NVIDIA_MODELS, _LIVE_OPENROUTER_MODELS
        nvidia_candidates = []
        if _LIVE_NVIDIA_MODELS:
            for m in _LIVE_NVIDIA_MODELS:
                m_lower = m.lower()
                is_big = False
                if any(x in m_lower for x in ["405b", "70b", "large", "super", "pro"]):
                    is_big = True
                elif "nemotron" in m_lower and not any(x in m_lower for x in ["nano", "8b", "vl"]):
                    is_big = True
                
                if is_big and any(x in m_lower for x in ["llama-3", "nemotron", "deepseek", "yi"]):
                    nvidia_candidates.append((m, "nvidia"))

        if nvidia_candidates:
            fallback_chain.extend(nvidia_candidates)
            if _LIVE_OPENROUTER_MODELS:
                for m in _LIVE_OPENROUTER_MODELS:
                    if "nvidia/nemotron" in m.lower() and m.endswith(":free"):
                        fallback_chain.append((m, "openrouter"))
            else:
                fallback_chain.append(("nvidia/nemotron-3-ultra-550b-a55b:free", "openrouter"))
                fallback_chain.append(("nvidia/nemotron-3-super-120b-a12b:free", "openrouter"))
        else:
            fallback_chain.extend(predefined_nvidia)

        # 3. OpenRouter free big models
        predefined_or_free = [
            ("deepseek/deepseek-chat-v3-5:free", "openrouter"),
            ("openrouter/free", "openrouter"),
            ("qwen/qwen3-coder:free", "openrouter"),
            ("deepseek/deepseek-v4-flash:free", "openrouter"),
        ]
        or_free_candidates = []
        if _LIVE_OPENROUTER_MODELS:
            for m in _LIVE_OPENROUTER_MODELS:
                m_lower = m.lower()
                if m_lower.endswith(":free"):
                    if any(x in m_lower for x in ["nemotron", "llama-3", "deepseek", "qwen", "gemma-4", "mixtral", "command-r", "phi-4"]):
                        if not any(x in m_lower for x in ["-8b", "-3b", "-2b", "nano", "mini-code"]):
                            or_free_candidates.append((m, "openrouter"))

        if or_free_candidates:
            for item in or_free_candidates:
                if item not in fallback_chain:
                    fallback_chain.append(item)
        else:
            for item in predefined_or_free:
                if item not in fallback_chain:
                    fallback_chain.append(item)

        # 4. Local 14b coder
        fallback_chain.append(("qwen3:14b", "ollama"))
        fallback_chain.append(("qwen2.5-coder:14b", "ollama"))

        # Prepend ZENITH_MODEL if it was set explicitly
        env_model = _os.environ.get("ZENITH_MODEL")
        if env_model and env_model.strip():
            if "/" in env_model or any(x in env_model.lower() for x in ["openrouter", "deepseek"]):
                env_provider = "openrouter"
            elif "nvidia" in env_model.lower():
                env_provider = "nvidia"
            elif "gemini" in env_model.lower():
                env_provider = "gemini"
            else:
                env_provider = "ollama"
            
            custom_item = (env_model.strip(), env_provider)
            if custom_item not in fallback_chain:
                fallback_chain.insert(0, custom_item)

        limit = STEP_LIMIT if agent_id in ("executor", "coder") else 5
        final_emitted = False
        tool_calls_made = 0
        previous_outputs: List[str] = []

        for turn in range(limit):
            full_chunk_content = ""
            chain_idx = 0
            current_model, current_provider = fallback_chain[chain_idx]
            success = False

            while not success:
                # Emit model selection metadata to caller/CLI conditionally
                is_default_local = (current_provider == "ollama" and current_model in ("qwen2.5:3b-instruct", "qwen2.5-coder:7b"))
                if chain_idx > 0 or not is_default_local:
                    yield {
                        "agent_id": agent_id,
                        "type": "model_selected",
                        "model": current_model,
                        "provider": current_provider,
                        "requested_role": agent.get("model_role", "general"),
                        "attempt": chain_idx + 1,
                        "temperature": temperature
                    }

                if current_provider == "openrouter":
                    api_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
                    base_url = _os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/v-horseshoe-v2",
                        "X-Title": "Swarm OS"
                    }
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": temperature,
                        "max_tokens": 1500
                    }
                elif current_provider == "nvidia":
                    api_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
                    base_url = _os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": temperature,
                        "max_tokens": 1500
                    }
                elif current_provider == "gemini":
                    api_key = _os.environ.get("GEMINI_API_KEY", "").strip()
                    base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": temperature,
                        "max_tokens": 1500
                    }
                else:
                    url = "http://127.0.0.1:11434/api/chat"
                    headers = None
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "keep_alive": 0,
                        "options": {"temperature": temperature, "num_predict": 1500},
                    }

                try:
                    async with httpx.AsyncClient(timeout=300.0, verify=False) as client:
                        async with client.stream(
                            "POST",
                            url,
                            json=payload,
                            headers=headers
                        ) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if not line.strip():
                                    continue

                                piece = ""
                                if current_provider in ("openrouter", "nvidia", "gemini"):
                                    if line.startswith("data: "):
                                        line = line[6:].strip()
                                    if not line or line == "[DONE]":
                                        continue
                                    try:
                                        data = json.loads(line)
                                        piece = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                    except Exception:
                                        pass
                                else:
                                    try:
                                        evt = json.loads(line)
                                        if evt.get("done"):
                                            break
                                        piece = evt.get("message", {}).get("content", "")
                                    except Exception:
                                        pass

                                if piece:
                                    full_chunk_content += piece
                                    yield {
                                        "agent_id": agent_id,
                                        "content": piece,
                                        "model": current_model,
                                    }
                                    
                                    # Stream repetition checker: halts loops within a single streaming response
                                    if len(full_chunk_content) > 120:
                                        has_rep = False
                                        for length in range(20, min(400, len(full_chunk_content) // 3)):
                                            suffix = full_chunk_content[-length:]
                                            if (full_chunk_content[-2*length:-length] == suffix and 
                                                full_chunk_content[-3*length:-2*length] == suffix):
                                                has_rep = True
                                                break
                                        if has_rep:
                                            logger.warning(f"Detected repetition loop in LLM stream for model {current_model}. Halting stream.")
                                            break
                    success = True
                except Exception as exc:
                    logger.warning(f"Request failed using provider {current_provider} with model {current_model}: {exc}")
                    chain_idx += 1
                    if chain_idx < len(fallback_chain):
                        next_model, next_provider = fallback_chain[chain_idx]
                        logger.warning(f"Escalating from {current_model} ({current_provider}) to {next_model} ({next_provider})...")
                        
                        try:
                            from organism_console.speech import play_chime_async
                            play_chime_async("escalation")
                        except Exception:
                            pass

                        yield {
                            "agent_id": agent_id,
                            "type": "model_escalation",
                            "from_model": current_model,
                            "reason": f"{exc.__class__.__name__}: {str(exc)[:100]}",
                        }
                        
                        current_model = next_model
                        current_provider = next_provider
                        full_chunk_content = ""
                    else:
                        logger.error("All models in the fallback chain have been exhausted!")
                        try:
                            from organism_console.speech import play_chime_async
                            play_chime_async("error")
                        except Exception:
                            pass
                        raise exc


            messages.append({"role": "assistant", "content": full_chunk_content})

            # Turn-level duplicate or near-identical narrative check
            clean_content = re.sub(r'<plan>.*?</plan>', '', full_chunk_content, flags=re.DOTALL)
            clean_content = re.sub(r'<tool_call[^>]*>.*?</tool_call>', '', clean_content, flags=re.DOTALL)
            clean_content = clean_content.strip()
            
            is_duplicate = False
            for prev in previous_outputs:
                if clean_content == prev:
                    is_duplicate = True
                    break
                words1 = set(clean_content.lower().split())
                words2 = set(prev.lower().split())
                if words1 and words2:
                    overlap = len(words1 & words2) / max(len(words1), len(words2))
                    if overlap > 0.85:
                        is_duplicate = True
                        break
            
            if is_duplicate:
                logger.warning(f"Detected duplicate or near-identical assistant output in turn {turn}. Terminating loop.")
                yield {
                    "agent_id": agent_id,
                    "type": "final",
                    "model": current_model,
                    "provider": current_provider,
                    "content": full_chunk_content,
                }
                return

            previous_outputs.append(clean_content)

            match = re.search(
                r'<tool_call name="([^"]+)">\s*(\{.*?\})\s*</tool_call>',
                full_chunk_content,
                re.DOTALL,
            )

            if not match:
                if final_emitted:
                    logger.info("Final response already emitted for agent '%s'.", agent_id)
                    return
                logger.info("Final response generated without tool call for agent '%s'.", agent_id)
                final_emitted = True
                yield {
                    "agent_id": agent_id,
                    "type": "final",
                    "model": current_model,
                    "provider": current_provider,
                    "content": full_chunk_content,
                }
                return

            tool_name = match.group(1).strip()

            if tool_calls_made >= 1 and agent_id in ("executor", "coder", "tool-runner"):
                logger.info("Second tool call suppressed for agent '%s'; emitting final response.", agent_id)
                final_emitted = True
                yield {
                    "agent_id": agent_id,
                    "type": "final",
                    "model": current_model,
                    "provider": current_provider,
                    "content": full_chunk_content,
                }
                return

            try:
                payload = json.loads(match.group(2).strip())
            except Exception as exc:
                yield {
                    "agent_id": agent_id,
                    "error": f"Invalid tool payload JSON: {exc}",
                    "tool": tool_name,
                }
                return

            available_tools = set(runtime.list_tools())
            mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)

            if mapped_tool_name == "__delegate__":
                target = payload.get("target_agent", "executor")
                task = payload.get("task") or payload.get("content") or payload.get("message") or payload.get("instruction") or str(payload)
                
                yield {
                    "agent_id": agent_id,
                    "type": "agent_handoff",
                    "from": agent_id,
                    "to": target,
                    "task": task
                }

                if target in delegation_chain:
                    logger.warning(f"Circular delegation loop detected: {delegation_chain} -> {target}")
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": f"Circular delegation loop detected between agents: {' -> '.join(delegation_chain)} -> {target}."
                    }
                    return

                if target in self.agents:
                    delegate_history = list(messages)
                    async for sub_chunk in self.step_agent_stream(
                        target,
                        task,
                        history=delegate_history,
                        delegation_chain=delegation_chain + [target]
                    ):
                        sub_chunk["delegated_by"] = agent_id
                        yield sub_chunk
                else:
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": f"Agent '{target}' not found. Task: {task}"
                    }
                return

            if mapped_tool_name not in available_tools:
                yield {
                    "agent_id": agent_id,
                    "error": f"Unknown tool '{mapped_tool_name}'",
                    "tool": tool_name,
                }
                return

            try:
                result = await runtime.call_tool(mapped_tool_name, payload or {})
                tool_calls_made += 1

                yield {
                    "agent_id": agent_id,
                    "type": "tool_result",
                    "tool": mapped_tool_name,
                    "result": result,
                }

                messages.append(
                    {
                        "role": "tool",
                        "content": f"Observation: {json.dumps(result, ensure_ascii=False)}",
                    }
                )

                if isinstance(result, dict) and result.get("final"):
                    return
            except Exception as err:
                from swarm_os.exceptions import ApprovalRequiredError
                if isinstance(err, ApprovalRequiredError):
                    question_text = construct_approval_question(err.tool_name, err.payload)
                    yield {
                        "ask_user": {
                            "question": question_text,
                            "options": ["approve", "deny"]
                        }
                    }
                    return
                else:
                    yield {
                        "agent_id": agent_id,
                        "error": str(err),
                        "tool": mapped_tool_name,
                    }
                    return


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


def construct_approval_question(tool_name: str, payload: dict) -> str:
    lines = [
        f"⚠️  APPROVAL REQUIRED for state-changing action:",
        f"• Action: Execute '{tool_name}'",
    ]
    
    if tool_name == "filesystem":
        op = payload.get("operation", "")
        path = payload.get("path", "")
        lines.append(f"• Operation: {op}")
        lines.append(f"• Affected File: {path}")
        
        if op == "write":
            content = payload.get("content", "")
            lines.append("• Expected Outcome: Write new content to file.")
            lines.append("• Risks: Overwriting existing file content.")
            preview = content[:200] + ("..." if len(content) > 200 else "")
            lines.append(f"• Content Preview:\n```\n{preview}\n```")
        elif op == "patch":
            old = payload.get("old", "")
            new = payload.get("new", "")
            lines.append("• Expected Outcome: Apply patch modifications.")
            lines.append("• Risks: Code syntax errors if patch fails to apply correctly.")
            lines.append(f"• Patch Diff:\n```diff\n- {old}\n+ {new}\n```")
        elif op == "delete":
            lines.append("• Expected Outcome: Delete file.")
            lines.append("• Risks: Permanent loss of file.")
            
    elif tool_name == "sandbox_repl":
        lang = payload.get("language", "")
        code = payload.get("code", "") or payload.get("command", "")
        lines.append(f"• Language: {lang}")
        lines.append("• Expected Outcome: Run execution command/script.")
        lines.append("• Risks: Executing arbitrary code with environment side effects.")
        preview = code[:200] + ("..." if len(code) > 200 else "")
        lines.append(f"• Command Preview:\n```\n{preview}\n```")
        
    lines.append("\nDo you approve this action?")
    return "\n".join(lines)


