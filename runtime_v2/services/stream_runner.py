"""Stream runner - uses litellm for multi-provider structured output and streaming.

Responsibilities:
- Orchestrate LLM tool-call decisions: load config, fetch MCP schemas, inject memory,
  call the LLM with retry, extract JSON, validate, and coerce invalid actions.
- Stream content generation for final responses.

Extracted modules:
  _llm_parser.py  — JSON extraction, normalization, fire-and-forget helper
  _llm_prompts.py — system prompt building for tool decisions
  _llm_client.py  — litellm config, API call wrappers, SSL setup
  _llm_cache.py   — decision caching
"""
import json
import re
import logging
import asyncio
from typing import Optional

from runtime_v2.services._llm_parser import (
    fire_and_forget,
    extract_json,
    normalize_model_json,
)
from runtime_v2.services._llm_prompts import build_tool_decision_system
from runtime_v2.services._llm_client import (
    bootstrap_ssl,
    get_routing_mode,
    get_litellm_model,
    inject_system_prompt,
    complete_for_tool_decision,
)
from runtime_v2.services._llm_cache import get_cache_key

from dotenv import load_dotenv
load_dotenv()

bootstrap_ssl()

import litellm
litellm.telemetry = False
litellm.suppress_debug_info = True

log = logging.getLogger(__name__)


async def get_tool_decision(
    model: str, messages: list, agent_id: str, allowed_tools: list = None
) -> Optional[dict]:
    cache_key = get_cache_key(messages, agent_id)
    # Cache permanently disabled — see commit history for rationale.
    # cached = _get_cached_decision(cache_key)
    # if cached:
    #     return cached

    litellm_model = get_litellm_model(agent_id, model)
    routing_mode = get_routing_mode()
    from runtime_v2.services.fallback_manager import get_live_fallbacks
    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    fallbacks = [
        f["model"]
        for f in raw_fallbacks
        if not f["model"].startswith("openai/")
    ][:3]

    allowed = allowed_tools or [
        "delegate", "final", "filesystem", "sandbox_repl", "web_search",
        "vscode_automation", "semantic_search", "remember", "ask_user",
        "lsp", "mcp", "self_heal"
    ]

    mcp_schema = ""
    if "mcp" in allowed:
        try:
            from runtime_v2.services.tool_executor import get_mcp_manager
            manager = await get_mcp_manager()
            tools = manager.cached_tools
            if tools:
                _last_user_msg = next(
                    (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
                )
                _keywords = {w.lower() for w in _last_user_msg.split() if len(w) > 3}

                def _tool_relevant(t: dict) -> bool:
                    haystack = (t.get("name", "") + " " + t.get("description", "")).lower()
                    return any(kw in haystack for kw in _keywords)

                relevant = [t for t in tools if _tool_relevant(t)][:5]
                if relevant:
                    mcp_schema = "RELEVANT MCP TOOLS:\n" + json.dumps(relevant, separators=(",", ":")) + "\n\n"
                else:
                    tool_names = ", ".join(t["name"] for t in tools[:10])
                    mcp_schema = f"AVAILABLE MCP TOOLS (use action=mcp): {tool_names}\n\n"
        except Exception as e:
            log.error(f"Failed to fetch MCP schemas: {e}")

    system_prompt = build_tool_decision_system(allowed, mcp_schema)

    # Memory augmentation: inject relevant memories within context budget
    try:
        _last_user_msg = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        if _last_user_msg and len(_last_user_msg) > 20:
            _approx_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
            _approx_tokens += len(system_prompt) // 4
            _context_limit = 16384
            _headroom = _context_limit - _approx_tokens

            if _headroom >= 1800:
                from runtime_v2.services.memory_core import get_relevant_memories
                memory_query = f"agent:{agent_id} {_last_user_msg[:200]}"
                memories_str = await asyncio.to_thread(get_relevant_memories, memory_query)
                injected_chars = len(memories_str or "")
                if memories_str:
                    mem_budget = min(600, _headroom * 4)
                    system_prompt = system_prompt + f"\n\n[RELEVANT MEMORIES]\n{memories_str[:mem_budget]}"
                    log.debug("[%s] Injected %d chars of memory (%d token headroom)", agent_id, min(600, len(memories_str)), _headroom)
                # ReflexionMemory: inject a distilled "do-not-repeat" hint from past
                # failures so the agent's own ASPO lessons steer this decision.
                try:
                    from swarm_os.services.reflection_loop import get_reflection_service
                    hint = await get_reflection_service().check_for_past_mistakes(memory_query)
                    if hint and len(hint) > 10:
                        remaining = min(400, (_headroom * 4) - injected_chars)
                        if remaining > 50:
                            system_prompt = system_prompt + f"\n\n[PAST-MISTAKE WARNING]\n{hint[:remaining]}"
                            log.debug("[%s] Injected reflexion warning (%d chars)", agent_id, min(400, len(hint)))
                except Exception as refl_err:
                    log.debug("Reflexion hint skipped: %s", refl_err)
            else:
                log.debug("[%s] Skipping memory injection — only %d tokens headroom", agent_id, _headroom)
    except Exception as mem_err:
        log.debug("Memory augmentation skipped: %s", mem_err)

    base_messages = inject_system_prompt(messages, system_prompt)

    MAX_EMPTY_RETRIES = 2
    _STEP_TIMEOUT = 180.0

    for empty_retry in range(MAX_EMPTY_RETRIES + 1):
        try:
            # UPGRADE: asyncio.timeout() context manager (composable, cancels cleanly)
            async with asyncio.timeout(_STEP_TIMEOUT):
                response = await complete_for_tool_decision(litellm_model, base_messages, fallbacks)
            if not response or not response.choices:
                log.warning("[%s] Empty response.choices (attempt %d/%d) — routing through retry.", agent_id, empty_retry + 1, MAX_EMPTY_RETRIES + 1)
                content = ""
            else:
                content = response.choices[0].message.content

            if not content or not content.strip():
                critique = (
                    f"REFLEXION: Agent '{agent_id}' returned an EMPTY response on attempt {empty_retry + 1}. "
                    f"The system prompt for this agent may be missing, the context may be malformed, "
                    f"or the model emitted an immediate EOS token. Fix: ensure agent has a defined role in "
                    f"system_prompts.py and that its prompt instructs it to output a JSON object."
                )
                log.warning("[%s] Empty response (attempt %d/%d) — storing reflexion: %s", agent_id, empty_retry + 1, MAX_EMPTY_RETRIES + 1, critique)
                try:
                    from runtime_v2.services.memory_core import remember_fact
                    fire_and_forget(asyncio.to_thread(remember_fact, critique, category="self_reflection"))
                except Exception:
                    pass

                if empty_retry < MAX_EMPTY_RETRIES:
                    past_lessons = ""
                    try:
                        from runtime_v2.services.memory_core import get_relevant_memories
                        lessons = await asyncio.to_thread(
                            get_relevant_memories,
                            f"agent:{agent_id} empty response failure fix"
                        )
                        if lessons:
                            past_lessons = f"\nPAST LESSONS FROM MEMORY:\n{lessons}\n"
                    except Exception:
                        pass

                    recovery_hint = (
                        f"SYSTEM RECOVERY (attempt {empty_retry + 2}): Your previous response was completely empty. "
                        f"You MUST output a valid JSON object now. Do not output anything else. "
                        f"Allowed actions: {', '.join(allowed)}. "
                        f"Example: {{\"action\":\"filesystem\",\"operation\":\"list\",\"path\":\"runtime_v2\"}}"
                        f"{past_lessons}"
                    )
                    base_messages = base_messages + [{"role": "user", "content": recovery_hint}]
                    log.warning("[%s] Retrying with recovery prompt (attempt %d)...", agent_id, empty_retry + 2)
                    continue
                else:
                    log.error("[%s] Agent returned empty after %d retries. Giving up.", agent_id, MAX_EMPTY_RETRIES + 1)
                    return {"action": "final", "response": f"[SYSTEM: Agent '{agent_id}' returned empty response after {MAX_EMPTY_RETRIES + 1} attempts. Model output was likely truncated. To fix this, please retry the request with a narrower scope (e.g., specify a smaller path, or use --max-characters/--max-chunks if applicable). Check its system prompt definition.]"}

            try:
                result = extract_json(normalize_model_json(content))
            except Exception as parse_exc:
                log.warning("[%s] JSON parse failed: %s — routing through retry (attempt %d)", agent_id, str(parse_exc)[:80], empty_retry + 1)
                critique = f"REFLEXION: Agent '{agent_id}' produced malformed JSON on attempt {empty_retry+1}: {str(parse_exc)[:150]}. Model may have had context overflow — the prompt was too long and the JSON was truncated before the 'action' field."
                try:
                    from runtime_v2.services.memory_core import remember_fact
                    fire_and_forget(asyncio.to_thread(remember_fact, critique, category="self_reflection"))
                except Exception:
                    pass
                if empty_retry < MAX_EMPTY_RETRIES:
                    base_messages = base_messages + [{"role": "user", "content": f"SYSTEM RECOVERY: Your JSON was malformed or truncated. Output ONLY a valid JSON object. Allowed actions: {', '.join(allowed)}. Example: {{\"action\":\"filesystem\",\"operation\":\"list\",\"path\":\".\"}}"}]
                    continue
                return {"action": "final", "response": f"[SYSTEM: {agent_id} produced malformed JSON after {MAX_EMPTY_RETRIES+1} attempts. Check context length.]"}

            result_action = result.get("action", "final")
            if result_action not in allowed:
                log.warning("[%s] Model hallucinated action '%s' (allowed: %s). Coercing to filesystem/final.", agent_id, result_action, allowed)
                if "filesystem" in allowed:
                    result["action"] = "filesystem"
                    if "path" not in result and "content" not in result:
                        result["action"] = "final"
                        result["response"] = result.get("response", result.get("task", "Task completed."))
                else:
                    result["action"] = "final"
                    result["response"] = result.get("response", result.get("task", "Task completed."))

            if result.get("action") == "final" and "response" in result:
                resp_str = str(result["response"])
                resp_str = re.sub(r"(?i)\b(Next, I will|I will now|I will proceed to|Let's proceed to|We will now|In the next step|I will continue by)\b.*", "", resp_str, flags=re.DOTALL).strip()
                if not resp_str:
                    resp_str = "Task completed based on available information."
                result["response"] = resp_str

            log.debug("[%s] Tool decision: %s", agent_id, result.get("action"))
            try:
                from runtime_v2.services.fallback_manager import record_model_success
                record_model_success(litellm_model)
            except Exception:
                pass
            return result

        except Exception as exc:
            is_timeout = isinstance(exc, asyncio.TimeoutError)
            # UPGRADE: feed outcome-driven cooldown so a failing local/cloud model is
            # skipped on the NEXT call instead of being retried blindly.
            try:
                from runtime_v2.services.fallback_manager import record_model_failure
                record_model_failure(litellm_model, str(exc)[:200])
            except Exception:
                pass
            if empty_retry < MAX_EMPTY_RETRIES and not is_timeout:
                log.warning("[%s] Tool decision transient error (attempt %d/%d): %s — retrying...", agent_id, empty_retry + 1, MAX_EMPTY_RETRIES + 1, str(exc)[:100])
                base_messages = base_messages + [{"role": "user", "content": f"SYSTEM: Previous LLM call failed ({str(exc)[:60]}). Retry with a valid JSON tool decision."}]
                continue
            import traceback
            if not is_timeout:
                traceback.print_exc()
            log.warning("[%s] Tool decision failed after %d retries (timeout=%s): %s (will use default)", agent_id, empty_retry + 1, is_timeout, str(exc)[:100])
            try:
                from runtime_v2.services.memory_core import remember_fact
                if is_timeout:
                    failure_memory = (
                        f"REFLEXION: Agent '{agent_id}' timed out ({_STEP_TIMEOUT}s) after {MAX_EMPTY_RETRIES + 1} attempts. "
                        f"Likely cause: RAM pressure causing OS-level swapping (tps dropped to ~0.1). "
                        f"Solution: free system memory or reduce concurrent model loads."
                    )
                else:
                    failure_memory = f"REFLEXION: Agent '{agent_id}' tool decision failed after {MAX_EMPTY_RETRIES + 1} attempts: {str(exc)[:200]}."
                fire_and_forget(asyncio.to_thread(remember_fact, failure_memory, category="self_reflection"))
            except Exception as mem_exc:
                log.warning("[%s] Failed to store reflexion memory: %s", agent_id, str(mem_exc)[:200])
            return {"action": "final", "response": f"Unable to determine next action after {MAX_EMPTY_RETRIES + 1} attempts. Last error: {repr(exc)}"}
