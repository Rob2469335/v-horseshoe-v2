# swarm_os/services/agent_service.py
from __future__ import annotations

import asyncio
import json
import logging
import os as _os
import re
import time
from typing import Any, Dict, List, AsyncGenerator

import httpx

from swarm_os.agent_runtime import AgentRuntime
from swarm_os.core.event_bus import event_bus
from swarm_os.kernel.model_router import ModelRouter

try:
    from swarm_os.lib.vector.context_retriever import build_context_prompt as _build_ctx
except ImportError:
    def _build_ctx(q):
        return q

logger = logging.getLogger(__name__)

_LIVE_OPENROUTER_MODELS = None
_LIVE_NVIDIA_MODELS = None
_LIVE_GROQ_MODELS = None
_LIVE_GEMINI_MODELS = None
_LAST_FETCH_TIME = 0.0
_CACHE_DURATION = 1800.0  # 30 minutes

_ASYNC_CLIENT = None

def get_async_client() -> httpx.AsyncClient:
    global _ASYNC_CLIENT
    if _ASYNC_CLIENT is None or _ASYNC_CLIENT.is_closed:
        _ASYNC_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), verify=False)
    return _ASYNC_CLIENT

async def _fetch_openrouter_models():
    global _LIVE_OPENROUTER_MODELS
    try:
        client = get_async_client()
        resp = await client.get("https://openrouter.ai/api/v1/models")
        if resp.status_code == 200:
            data = resp.json()
            _LIVE_OPENROUTER_MODELS = [m["id"] for m in data.get("data", [])]
        else:
            _LIVE_OPENROUTER_MODELS = []
    except Exception as e:
        logger.warning(f"Failed to fetch live OpenRouter models: {e}")
        _LIVE_OPENROUTER_MODELS = []

async def _fetch_nvidia_models():
    global _LIVE_NVIDIA_MODELS
    api_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
    if api_key:
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            client = get_async_client()
            resp = await client.get("https://integrate.api.nvidia.com/v1/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                _LIVE_NVIDIA_MODELS = [m["id"] for m in data.get("data", [])]
            else:
                _LIVE_NVIDIA_MODELS = []
        except Exception as e:
            logger.warning(f"Failed to fetch live Nvidia models: {e}")
            _LIVE_NVIDIA_MODELS = []
    else:
        _LIVE_NVIDIA_MODELS = []

async def _fetch_groq_models():
    global _LIVE_GROQ_MODELS
    groq_key = _os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            headers = {"Authorization": f"Bearer {groq_key}"}
            client = get_async_client()
            resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                _LIVE_GROQ_MODELS = [m["id"] for m in data.get("data", [])]
            else:
                _LIVE_GROQ_MODELS = []
        except Exception as e:
            logger.warning(f"Failed to fetch live Groq models: {e}")
            _LIVE_GROQ_MODELS = []
    else:
        _LIVE_GROQ_MODELS = []

async def _fetch_gemini_models():
    global _LIVE_GEMINI_MODELS
    gemini_key = _os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        _LIVE_GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro"]
    else:
        _LIVE_GEMINI_MODELS = []

async def fetch_live_models_if_needed():
    global _LIVE_OPENROUTER_MODELS, _LIVE_NVIDIA_MODELS, _LIVE_GROQ_MODELS, _LIVE_GEMINI_MODELS, _LAST_FETCH_TIME
    now = time.time()
    if (
        _LIVE_OPENROUTER_MODELS is not None
        and _LIVE_NVIDIA_MODELS is not None
        and _LIVE_GROQ_MODELS is not None
        and _LIVE_GEMINI_MODELS is not None
        and (now - _LAST_FETCH_TIME) < _CACHE_DURATION
    ):
        return

    try:
        await asyncio.gather(
            _fetch_openrouter_models(),
            _fetch_nvidia_models(),
            _fetch_groq_models(),
            _fetch_gemini_models(),
            return_exceptions=True
        )
    except Exception as e:
        logger.error(f"Error updating live models concurrently: {e}")

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

def reconcile_and_repair_tool_call(tool_name: str, payload: dict, available_tools: set[str]) -> tuple[str, dict]:
    mapped_name = tool_name
    new_payload = dict(payload or {})

    if tool_name in {"delegate", "handoff", "transfer", "route"}:
        return "__delegate__", new_payload

    if tool_name not in available_tools:
        if tool_name in {"read_file", "write_file", "patch_file", "file_read", "file_write", "file_patch"}:
            mapped_name = "filesystem"
            op = "read" if "read" in tool_name else "write" if "write" in tool_name else "patch"
            new_payload["operation"] = op
        elif tool_name in {"search", "google_search", "web", "search_web"}:
            mapped_name = "web_search"
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
        "⚠️ APPROVAL REQUIRED for state-changing action:",
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
            "reviewer": {
                "role": "reviewer",
                "description": "Audits code and proposals, finds bugs and design flaws.",
                "model_role": "reasoning",
            },
            "coder": {
                "role": "coder",
                "description": "Code-writing specialist focusing on high-quality modifications.",
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
            "COORDINATOR RULES (only if agent_id == coordinator):",
            "- You are the COORDINATOR. Your FIRST and ONLY action is ALWAYS to delegate to planner.",
            "- NEVER use filesystem, vscode_automation, sandbox_repl, web_search, or playwright directly.",
            "- OUTPUT EXACTLY THIS as your first response:",
            '- <tool_call name="delegate">{"target_agent": "planner", "task": "<restate the user task>"}</tool_call>',
            "- Do NOT read files. Do NOT analyze. Do NOT plan. Just delegate immediately.",
            "",
            "Think step-by-step. Be concise. Be agentic.",
            "",
            "ROLE RULES:",
            "- planner: delegate to executor, coder, or tool-runner; planner should not do implementation work directly.",
            "- executor: execute tasks directly with tools; NEVER delegate to executor.",
            "- coder: write or patch code; NEVER delegate to coder unless the task specifically requires code changes.",
            "- tool-runner: run tools and checks; NEVER delegate to tool-runner unless a concrete tool action is needed.",
            "- Only use ask_user if agent_id is coordinator.",
            "- If you are executor and need code changes, delegate to coder.",
            "- If you are executor and need tool execution or verification, use tools directly or delegate to tool-runner.",
            "- Never delegate to yourself. Choose a different agent or use a tool directly.",
        ]

        role_id = agent["id"]
        if role_id == "planner":
            instruction.append("PLANNER RULES:")
            instruction.append("- Output a <plan> block, then IMMEDIATELY delegate the first step to executor.")
            instruction.append('- ALWAYS end your response with: <tool_call name="delegate">{"target_agent": "executor", "task": "<first step>"}</tool_call>')
            instruction.append("- NEVER just output a plan and stop. Always delegate.")
        elif role_id == "executor":
            instruction.append("EXECUTOR RULES:")
            instruction.append("- Use filesystem to read files. Use tools to execute tasks.")
            instruction.append("- When done, delegate to coder if code changes needed, else delegate to tool-runner.")
        elif role_id == "coder":
            instruction.append("CODER RULES:")
            instruction.append("- Use filesystem patch to make code changes. Verify syntax after.")
            instruction.append('- When done, delegate to tool-runner to verify: <tool_call name="delegate">{"target_agent": "tool-runner", "task": "verify the changes"}</tool_call>')
        elif role_id == "tool-runner":
            instruction.append("TOOL-RUNNER RULES:")
            instruction.append("- Run verification tools. Check files exist. Run tests if needed.")
            instruction.append('- When done, delegate to reviewer: <tool_call name="delegate">{"target_agent": "reviewer", "task": "review the changes"}</tool_call>')
        elif role_id in ("reviewer", "critic"):
            instruction.append("REVIEWER RULES:")
            instruction.append("- Read the modified files. Check for bugs, correctness, and quality.")
            instruction.append("- Output a final verdict. Do NOT delegate further.")

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

        if len(delegation_chain) >= 7:
            logger.warning(f"Delegation depth exceeded: {delegation_chain}")
            yield {
                "agent_id": agent_id,
                "type": "error",
                "content": f"Delegation depth exceeded: {' -> '.join(delegation_chain)}",
            }
            return

        system_msg = self._build_system_instruction(agent)
        messages = [{"role": "system", "content": system_msg}] + history
        if prompt:
            messages.append({"role": "user", "content": _build_ctx(prompt)})

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
                    r'<tool_call\s+name="([^"]+)">\s*(\{.*?\})\s*(?:</tool_call>|<strategic_intent|<plan|<tool_call|<topic_update|$)',
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
                        runtime.approved_actions.append(
                            {
                                "tool": mapped_prop_tool,
                                "payload": prop_payload,
                            }
                        )
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

        _, temperature = _resolve_runtime_config(agent)

        try:
            await fetch_live_models_if_needed()
        except Exception as e:
            logger.warning(f"Error fetching live models: {e}")

        global _LIVE_NVIDIA_MODELS, _LIVE_OPENROUTER_MODELS
        fallback_chain = ModelRouter.build_fallback_chain(
            agent_id,
            _LIVE_NVIDIA_MODELS,
            _LIVE_OPENROUTER_MODELS
        )

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
                is_default_local = (current_provider == "ollama" and current_model in ("qwen2.5:3b-instruct", "qwen2.5-coder:7b"))
                if chain_idx > 0 or not is_default_local:
                    yield {
                        "agent_id": agent_id,
                        "type": "model_selected",
                        "model": current_model,
                        "provider": current_provider,
                        "requested_role": agent.get("model_role", "general"),
                        "attempt": chain_idx + 1,
                        "temperature": temperature,
                    }

                if current_provider == "openrouter":
                    api_key = _os.environ.get("OPENROUTER_API_KEY", "").strip()
                    base_url = _os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/v-horseshoe-v2",
                        "X-Title": "Swarm OS",
                    }
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": temperature,
                        "max_tokens": 1500,
                        "stop": ["</tool_call>"],
                    }
                elif current_provider == "nvidia":
                    api_key = _os.environ.get("NVIDIA_API_KEY", "").strip()
                    base_url = _os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": temperature,
                        "max_tokens": 1500,
                        "stop": ["</tool_call>"],
                    }
                elif current_provider == "gemini":
                    api_key = _os.environ.get("GEMINI_API_KEY", "").strip()
                    base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
                    url = f"{base_url}/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": temperature,
                        "max_tokens": 1500,
                        "stop": ["</tool_call>"],
                    }
                elif current_provider == "groq":
                    api_key = _os.environ.get("GROQ_API_KEY", "").strip()
                    url = "https://api.groq.com/openai/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "temperature": temperature,
                        "max_tokens": 1500,
                    }
                else:
                    url = "http://127.0.0.1:11434/api/chat"
                    headers = None
                    payload = {
                        "model": current_model,
                        "messages": messages,
                        "stream": True,
                        "keep_alive": 0,
                        "options": {"temperature": temperature, "num_predict": 1500, "stop": ["</tool_call>"]},
                    }

                try:
                    async with httpx.AsyncClient(timeout=300.0, verify=False) as client:
                        async with client.stream(
                            "POST",
                            url,
                            json=payload,
                            headers=headers,
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
                                        piece = line

                                if piece:
                                    full_chunk_content += piece
                                    yield {
                                        "agent_id": agent_id,
                                        "content": piece,
                                        "model": current_model,
                                    }

                                    if len(full_chunk_content) > 120:
                                        has_rep = False
                                        for length in range(20, min(400, len(full_chunk_content) // 3)):
                                            suffix = full_chunk_content[-length:]
                                            if (
                                                full_chunk_content[-2 * length:-length] == suffix
                                                and full_chunk_content[-3 * length:-2 * length] == suffix
                                            ):
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

            clean_assistant = re.sub(r'<tool_call[^>]*>.*?(?:</tool_call>|$)', '', full_chunk_content, flags=re.DOTALL).strip()
            messages.append({"role": "assistant", "content": clean_assistant})

            duplicate_check_text = re.sub(r'<plan>.*?</plan>', '', full_chunk_content, flags=re.DOTALL).strip()

            is_duplicate = False
            for prev in previous_outputs:
                if duplicate_check_text == prev:
                    is_duplicate = True
                    break
                words1 = set(duplicate_check_text.lower().split())
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

            previous_outputs.append(duplicate_check_text)

            pending_calls = []
            for match in re.finditer(r'<tool_call\s+name="([^"]+)">\s*(.*?)\s*</tool_call>', full_chunk_content, re.DOTALL):
                tool_name = match.group(1).strip()
                raw_payload = match.group(2).strip()
                try:
                    payload = json.loads(raw_payload)
                except Exception as e:
                    logger.warning(
                        f"Malformed tool payload from agent '{agent_id}' for tool '{tool_name}': {e}; payload={raw_payload[:300]!r}"
                    )
                    payload = {}
                pending_calls.append((tool_name, payload))

            if not pending_calls:
                delegate_tag = re.search(
                    r'<delegate\s+target_agent="([^"]+)"\s+task="([^"]+)"\s*/?>',
                    full_chunk_content,
                    re.DOTALL,
                )
                if delegate_tag:
                    pending_calls.append(
                        (
                            "delegate",
                            {
                                "target_agent": delegate_tag.group(1).strip(),
                                "task": delegate_tag.group(2).strip(),
                            },
                        )
                    )
                else:
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

            available_tools = set(runtime.list_tools())
            normal_calls = []
            delegate_calls = []

            for tool_name, payload in pending_calls:
                mapped_tool_name, payload = reconcile_and_repair_tool_call(tool_name, payload, available_tools)

                if mapped_tool_name == "ask_user" and agent_id != "coordinator":
                    logger.warning(f"ask_user blocked for non-coordinator agent: {agent_id}")
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": "Only the coordinator agent is allowed to ask the user questions.",
                    }
                    return

                if mapped_tool_name == "__delegate__":
                    delegate_calls.append((tool_name, mapped_tool_name, payload))
                else:
                    normal_calls.append((tool_name, mapped_tool_name, payload))

            for tool_name, mapped_tool_name, payload in normal_calls:
                if mapped_tool_name not in available_tools:
                    yield {
                        "agent_id": agent_id,
                        "type": "error",
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
                    yield {
                        "agent_id": agent_id,
                        "type": "error",
                        "error": str(err),
                        "tool": mapped_tool_name,
                    }
                    return

            if len(delegate_calls) > 1:
                logger.warning(f"Multiple delegate calls emitted by agent '{agent_id}'. Only the first delegate will execute.")
                delegate_calls = delegate_calls[:1]

            if delegate_calls:
                tool_name, mapped_tool_name, payload = delegate_calls[0]
                target = payload.get("target_agent", "executor")
                task = payload.get("task") or payload.get("content") or payload.get("message") or payload.get("instruction") or str(payload)

                if target == agent_id:
                    logger.warning(f"Self-delegation blocked: {agent_id} -> {target}")
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": f"Self-delegation blocked for agent: {agent_id}.",
                    }
                    return

                yield {
                    "agent_id": agent_id,
                    "type": "agent_handoff",
                    "from": agent_id,
                    "to": target,
                    "task": task,
                }

                visit_count = delegation_chain.count(target)
                if visit_count >= 2:
                    logger.warning(f"Repeated delegation target blocked: {delegation_chain} -> {target}")
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": f"Repeated delegation target blocked between agents: {' -> '.join(delegation_chain)} -> {target}.",
                    }
                    return
                elif visit_count >= 1:
                    logger.warning(f"Circular delegation loop detected: {delegation_chain} -> {target}")
                    yield {
                        "agent_id": agent_id,
                        "type": "final",
                        "model": current_model,
                        "provider": current_provider,
                        "content": f"Circular delegation loop detected between agents: {' -> '.join(delegation_chain)} -> {target}.",
                    }
                    return

                if target in self.agents:
                    delegate_history = list(messages)
                    async for sub_chunk in self.step_agent_stream(
                        target,
                        task,
                        history=delegate_history,
                        delegation_chain=delegation_chain + [target],
                    ):
                        sub_chunk["delegated_by"] = agent_id
                        yield sub_chunk
                    return

                yield {
                    "agent_id": agent_id,
                    "type": "final",
                    "model": current_model,
                    "provider": current_provider,
                    "content": f"Agent '{target}' not found. Task: {task}",
                }
                return

            if tool_calls_made == 0 and not final_emitted:
                logger.info("No tool calls made and no final emitted for agent '%s'; emitting final.", agent_id)
                final_emitted = True
                yield {
                    "agent_id": agent_id,
                    "type": "final",
                    "model": current_model,
                    "provider": current_provider,
                    "content": full_chunk_content,
                }
                return
