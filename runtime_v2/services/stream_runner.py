"""Stream runner - uses litellm for multi-provider structured output and streaming."""
import json
import re
import logging
import asyncio

_background_tasks = set()

def _fire_and_forget(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task

import os
from typing import AsyncGenerator, Optional
import litellm
from dotenv import load_dotenv
from runtime_v2.services.model_registry import get_model
from runtime_v2.services.fallback_manager import get_live_fallbacks
from datetime import datetime, timedelta

load_dotenv()

litellm.telemetry = False
litellm.suppress_debug_info = True

log = logging.getLogger(__name__)

_decision_cache = {}
_cache_ttl = 300

TOOL_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": [
            "delegate", "web_search", "filesystem", "sandbox_repl", "vscode_automation", "semantic_search", "remember", "ask_user", "lsp", "mcp", "mcp_register", "self_heal", "final"
        ]},
        "target_agent": {"type": "string"},
        "server_name": {"type": "string"},
        "task": {"type": "string"},
        "query": {"type": "string"},
        "operation": {"type": "string"},
        "path": {"type": "string"},
        "content": {"type": "string"},
        "old": {"type": "string"},
        "new": {"type": "string"},
        "language": {"type": "string"},
        "code": {"type": "string"},
        "command": {"type": "string"},
        "args": {"type": "array", "items": {"type": "string"}},
        "response": {"type": "string"},
        "fact": {"type": "string"},
        "category": {"type": "string"},
        "question": {"type": "string"}
    },
    "required": ["action"]
}

def build_tool_decision_system(allowed_tools: list, mcp_schema: str = "") -> str:
    tools_csv = ", ".join(allowed_tools) if allowed_tools else "final"

    # Fast-path for routing-only agents (coordinator/executor): no thought key,
    # ultra-minimal prompt so small 4B models don't drift into natural language.
    routing_only = set(allowed_tools) <= {"delegate", "final", "ask_user"}
    if routing_only:
        delegate_ex = ""
        if "delegate" in allowed_tools:
            delegate_ex = '\nExample: {"action":"delegate","target_agent":"coder","task":"Read ./Modelfile"}'
        final_ex = '\nExample: {"action":"final","response":"Hello!"}'
        return (
            "/no_think\n"  # Qwen3 native token: disables chain-of-thought at tokenizer level
            f"Output ONE JSON object with action from: {tools_csv}."
            f"{delegate_ex}"
            f"{final_ex}"
            "\nNo markdown. No prose. Only JSON."
        )

    examples = []
    if "delegate" in allowed_tools:
        examples.append('{"thought": "Need code", "action": "delegate", "target_agent": "coder", "task": "Write the function"}')
    if "final" in allowed_tools:
        examples.append('{"thought": "Done", "action": "final", "response": "Here is my answer."}')
    if "filesystem" in allowed_tools:
        examples.append('{"thought": "Writing file", "action": "filesystem", "operation": "write", "path": "test.py", "content": "..."}')
    if "sandbox_repl" in allowed_tools:
        examples.append('{"thought": "Testing", "action": "sandbox_repl", "language": "python", "code": "print(1)"}')
    if "lsp" in allowed_tools:
        examples.append('{"thought": "Linting", "action": "lsp", "operation": "diagnostics", "file_path": "test.py"}')
    if "mcp" in allowed_tools:
        examples.append('{"thought": "Querying DB", "action": "mcp", "server": "sqlite", "tool": "query", "arguments": {"query": "SELECT * FROM users"}}')
    if "mcp_register" in allowed_tools:
        examples.append('{"thought": "Adding tool", "action": "mcp_register", "server_name": "my_tool", "command": "python", "args": [".swarm_brain/tools/my_tool.py"]}')
    if "web_search" in allowed_tools:
        examples.append('{"thought": "Searching docs", "action": "web_search", "query": "Python multiprocessing"}')
    
    examples_str = "\n".join(examples)
    
    return (
        "/no_think\n"  # Qwen3 native token: disables chain-of-thought at tokenizer level
        "\n\n*** CRITICAL FORMATTING INSTRUCTION ***\n"
        "You must express your decision as a SINGLE VALID JSON OBJECT.\n"
        "Do NOT output markdown code blocks. Just output raw JSON.\n"
        "You MAY use a 'thought' key to plan your action, but it MUST BE EXTREMELY SHORT (1-2 sentences max).\n\n"
        f"{mcp_schema}\n\n"
        "The required format is:\n"
        "{\n"
        "    \"thought\": \"<Brief 1-2 sentence plan>\",\n"
        f"    \"action\": \"<one of: {tools_csv}>,\"\n"
        "    ... (additional required fields based on action)\n"
        "}\n\n"
        f"Example valid outputs:\n"
        f"{examples_str}\n\n"
        "Do not use any other top-level keys unless needed for the selected action.\n"
        "For action=final, use only: {\"thought\":\"...\", \"action\":\"final\",\"response\":\"...\"}\n"
        "DO NOT use XML tags like <tool_call> or <tool_code>. Do NOT wrap your JSON in any tags.\n"
        "DO NOT output anything other than the JSON object."
    )

JSON_REPAIR_PROMPT = (
    "Your previous reply was not accepted.\n"
    "Reply again with exactly one valid JSON object only.\n"
    "No markdown. No code fences. No prose. No explanation.\n"
    "DO NOT use XML tags like <tool_call> or <tool_code>. Just output raw JSON.\n"
    "Use an 'action' key."
)

def _get_routing_mode() -> str:
    mode = os.getenv("SWARM_ROUTING_MODE", "local_only").strip().lower()
    if mode not in ("auto", "local_only", "cloud_allowed"):
        return "local_only"
    return mode

def _get_litellm_model(agent_id: str, fallback_model: str) -> str:
    default_model, backend = get_model(agent_id)
    # If the caller provides a fallback_model (e.g. adaptive routing chose the sidecar), use it!
    model = fallback_model if fallback_model else default_model

    if model.startswith("router/"):
        model = model.split("/", 1)[1]

    if model.startswith("ollama_chat/") or model.startswith("ollama/"):
        return model

    if "/" in model and not model.startswith("ollama") and backend != "ollama":
        return model

    # Force local models to use ollama_chat
    if backend == "ollama" or backend == "router" or model.startswith("ollama/"):
        model_name = model.replace("ollama/", "") if model.startswith("ollama/") else model
        return f"ollama_chat/{model_name}"

    if backend == "openrouter":
        return f"openrouter/{model}"
    if backend == "groq":
        return f"groq/{model}"
    if backend == "nvidia":
        return f"nvidia_nim/{model}"
    if backend == "gemini":
        return f"gemini/{model}"
    return f"{backend}/{model}" if "/" not in model else model

def _build_kwargs(litellm_model: str, extra: dict, fallbacks: list) -> dict:
    return {"model": litellm_model, "fallbacks": fallbacks, "timeout": 600.0, **extra}

def _inject_system_prompt(messages: list, system: str) -> list:
    messages = list(messages)
    # For ultra-short routing prompts (coordinator/executor), REPLACE the system message
    # rather than appending. Appending after a long persona prompt buries the JSON
    # instruction and causes small models to anchor on the persona and ignore the format.
    replace_mode = len(system.strip()) < 300
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            if replace_mode:
                messages[i] = {"role": "system", "content": system}
            else:
                messages[i] = {"role": "system", "content": m["content"] + system}
            return messages
    return [{"role": "system", "content": system}] + messages

def _normalize_decision(obj: dict) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("Tool decision is not a dict")

    if "action" not in obj:
        if "tool" in obj:
            obj["action"] = obj["tool"]
        elif "name" in obj:
            obj["action"] = obj["name"]
        elif "text" in obj:
            txt = obj.get("text")
            if isinstance(txt, list):
                txt = " ".join(str(x) for x in txt if x is not None).strip()
            else:
                txt = str(txt).strip()
            obj["action"] = "final"
            obj["response"] = txt or "Task processed."
        elif "target_agent" in obj and "task" in obj:
            obj["action"] = "delegate"
        # --- STRUCTURAL INFERENCE: model forgot the "action" key but the field signature is clear ---
        # This handles the very common pattern where the 35B model outputs:
        # {"thought":"...","operation":"read","path":"..."} — inferring action=filesystem
        elif "operation" in obj and "path" in obj:
            obj["action"] = "filesystem"
            log.debug("Inferred action=filesystem from operation+path fields")
        elif "operation" in obj and "file_path" in obj:
            obj["action"] = "lsp"
            log.debug("Inferred action=lsp from operation+file_path fields")
        elif "query" in obj and "path" not in obj:
            obj["action"] = "web_search"
            log.debug("Inferred action=web_search from query field")
        elif "code" in obj or "language" in obj:
            obj["action"] = "sandbox_repl"
            log.debug("Inferred action=sandbox_repl from code/language fields")
        elif "fact" in obj:
            obj["action"] = "remember"
            log.debug("Inferred action=remember from fact field")
        elif "question" in obj:
            obj["action"] = "ask_user"
            log.debug("Inferred action=ask_user from question field")
        elif "response" in obj:
            obj["action"] = "final"
            log.debug("Inferred action=final from response field")

    action = str(obj.get("action", "")).strip()
    aliases = {
        "websearch": "web_search",
        "web-search": "web_search",
        "search_web": "web_search",
        "searchweb": "web_search",
        "file_system": "filesystem",
        "fs": "filesystem",
        "shell": "sandbox_repl",
        "bash": "sandbox_repl",
        "terminal": "sandbox_repl",
        "sandboxrepl": "sandbox_repl",
        "vscodeautomation": "vscode_automation",
        "done": "final",
        "answer": "final",
    }
    if action in aliases:
        action = aliases[action]
    obj["action"] = action

    if not obj["action"]:
        raise ValueError("Missing action after normalization")

    return obj


def _extract_json(text: str) -> dict:
    import ast
    raw_text = text or ""
    # Pass 1: strip <think> blocks and try to find JSON in the remainder
    text = re.sub(r"<think>.*?(?:</think>|$)", "", raw_text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    
    valid_jsons = []
    start = text.find("{")
    while start != -1:
        brace_count = 0
        in_string = False
        escape_next = False
        for i in range(start, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == "\\":
                escape_next = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if not in_string:
                if c == "{":
                    brace_count += 1
                elif c == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        try:
                            valid_jsons.append(_normalize_decision(json.loads(text[start:i + 1].strip(), strict=False)))
                        except Exception as parse_exc:
                            log.warning(f"Failed to parse JSON candidate: {parse_exc}")
                            pass
                        break
        start = text.find("{", start + 1)
        
    if valid_jsons:
        # Take the FIRST complete JSON object — if the model looped and repeated itself,
        # the first copy is always the clean, complete one. The second copy is the broken partial.
        return valid_jsons[0]

    try:
        py_obj = ast.literal_eval(text.strip())
        if isinstance(py_obj, dict):
            return _normalize_decision(py_obj)
    except Exception:
        pass

    if text.strip():
        # Deduplicate repeated plain-text: model sometimes emits the same sentence twice.
        clean = text.strip()
        
        # If the text is just hallucinated empty XML tags (like <tool_call><tool_code></tool_code>), 
        # raise ValueError so the outer function triggers the self-healing retry loop.
        clean_no_xml = re.sub(r'</?(?:tool_call|tool_code|tools)[^>]*>', '', clean).strip()
        if not clean_no_xml:
            raise ValueError(f"Model output only contained empty XML tags: {clean}")

        mid = len(clean) // 2
        if mid > 3 and clean[:mid].strip() == clean[mid:].strip():
            clean = clean[:mid].strip()
        elif mid > 3:
            # Sliding search: find shortest prefix that repeats immediately after itself
            for split in range(3, mid + 1):
                if clean[split:split * 2] == clean[:split]:
                    clean = clean[:split]
                    break
        return {"action": "final", "response": clean}

    # Pass 2: model burned all tokens inside <think> — scan the raw think block for JSON
    # This is the last-resort recovery for Qwen3 deep-think mode exhausting max_tokens
    think_match = re.search(r"<think>(.*?)(?:</think>|$)", raw_text, flags=re.DOTALL)
    if think_match:
        think_content = think_match.group(1)
        think_jsons = []
        t_start = think_content.find("{")
        while t_start != -1:
            brace_count = 0
            in_str = False
            esc = False
            for i in range(t_start, len(think_content)):
                c = think_content[i]
                if esc: esc = False; continue
                if c == "\\": esc = True; continue
                if c == '"': in_str = not in_str; continue
                if not in_str:
                    if c == "{": brace_count += 1
                    elif c == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            try:
                                think_jsons.append(_normalize_decision(json.loads(think_content[t_start:i + 1], strict=False)))
                            except Exception as parse_exc:
                                log.warning(f"Failed to parse JSON candidate inside think block: {parse_exc}")
                                pass
                            break
            t_start = think_content.find("{", t_start + 1)
        if think_jsons:
            log.warning("Recovered JSON from inside <think> block (thinking suppression may have failed)")
            return think_jsons[-1]

    log.warning(f"Could not extract JSON, defaulting to 'final' action. Text: {raw_text[:100]}")
    return {"action": "final", "response": "Task processed."}

def _normalize_model_json(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s

async def _complete_for_tool_decision(litellm_model: str, messages: list, fallbacks: list):
    extra = {
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1800,  # Balanced: enough for inline file-write content, bounded to avoid timeout at ~4-10 tok/s
        "num_ctx": 4096,     # CRITICAL: Prevent Qwen from allocating 32k context and crashing VRAM
    }
    if not litellm_model.startswith("openrouter/"):
        # extra["response_format"] = {"type": "json_object"}
        pass
    # Suppress thinking for Ollama models. IMPORTANT: `think` must be at the TOP LEVEL
    # of the request body, NOT inside `options`. Placing it inside `options` is silently
    # ignored by Ollama's /api/chat endpoint, causing the model to burn all max_tokens
    # inside a <think> block, leaving nothing for the actual JSON response.
    if litellm_model.startswith("ollama_chat/"):
        extra["extra_body"] = {"think": False}
        
    kwargs = _build_kwargs(litellm_model, extra, fallbacks)
    kwargs["max_retries"] = 0
    kwargs["timeout"] = 900.0  # Raised from 600s: large multi-agent prompts (MCP schemas) can push generation past 600s at ~3-4 tok/s
    return await litellm.acompletion(**kwargs)

def _get_cache_key(messages: list, agent_id: str) -> str:
    if messages:
        # Just use the last user message text to keep hashing cheap
        last_msg = messages[-1].get("content", "")
        if not isinstance(last_msg, str):
            import json
            last_msg = json.dumps(last_msg)
        import hashlib
        h = hashlib.sha256(last_msg.encode('utf-8')).hexdigest()
        return f"{agent_id}:{h}"
    return f"{agent_id}:default"

def _get_cached_decision(cache_key: str) -> Optional[dict]:
    if cache_key in _decision_cache:
        decision, timestamp = _decision_cache[cache_key]
        if datetime.now() - timestamp < timedelta(seconds=_cache_ttl):
            return decision
        del _decision_cache[cache_key]
    return None

def _cache_decision(cache_key: str, decision: dict):
    if len(_decision_cache) > 500:
        # Evict oldest 100 entries instead of clearing completely
        keys_to_remove = list(_decision_cache.keys())[:100]
        for k in keys_to_remove:
            _decision_cache.pop(k, None)
    _decision_cache[cache_key] = (decision, datetime.now())

async def get_tool_decision(model: str, messages: list, agent_id: str, allowed_tools: list = None) -> Optional[dict]:
    cache_key = _get_cache_key(messages, agent_id)
    # Caching disabled to prevent infinite replay loops on truncated context
    # cached = _get_cached_decision(cache_key)
    # if cached:
    #     log.debug("[%s] Tool decision (cached): %s", agent_id, cached.get("action"))
    #     return cached

    litellm_model = _get_litellm_model(agent_id, model)
    routing_mode = _get_routing_mode()
    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    # CRITICAL: Never use local Ollama models as LiteLLM fallbacks.
    # On a shared-memory GPU (Intel Arc), loading a second local model as a fallback
    # exhausts shared memory and causes the primary model to drop to CPU speed.
    # Only cloud/API models (no ollama/ prefix) are safe to use as fallbacks.
    fallbacks = [f["model"] for f in raw_fallbacks if not f["model"].startswith("ollama/")][:3]

    allowed = allowed_tools or ["delegate", "final", "filesystem", "sandbox_repl", "web_search", "vscode_automation", "semantic_search", "remember", "ask_user", "lsp", "mcp", "self_heal"]
    
    mcp_schema = ""
    if "mcp" in allowed:
        try:
            from runtime_v2.services.tool_executor import get_mcp_manager
            import json
            manager = await get_mcp_manager()
            tools = manager.cached_tools
            if tools:
                # Only inject schemas for tools whose name/description share keywords with
                # the current user message. Caps injected tokens as tool registry grows.
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
                    # Fallback: names-only so model knows tools exist without full schemas
                    tool_names = ", ".join(t["name"] for t in tools[:10])
                    mcp_schema = f"AVAILABLE MCP TOOLS (use action=mcp): {tool_names}\n\n"
        except Exception as e:
            log.error(f"Failed to fetch MCP schemas: {e}")
            
    system_prompt = build_tool_decision_system(allowed, mcp_schema)
    
    # --- MEMORY AUGMENTATION: Context-budget-aware memory injection ---
    # Pull the most relevant memories from Qdrant (6000+ stored) and inject them
    # ONLY if there is enough token headroom. The 35B model runs at 4096 ctx here;
    # injecting too many tokens causes JSON truncation (the "Missing action" error).
    # Budget rule: estimate current tokens ~= 4 chars per token. Only inject if
    # the current prompt + system prompt leaves >= 1800 tokens free for the response.
    try:
        _last_user_msg = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        if _last_user_msg and len(_last_user_msg) > 20:
            # Rough token estimate: sum of all message content chars / 4
            _approx_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
            _approx_tokens += len(system_prompt) // 4
            _context_limit = 4096  # Conservative — actual may vary by agent
            _headroom = _context_limit - _approx_tokens
            
            if _headroom >= 1800:  # Only inject if model has room to think
                from runtime_v2.services.memory_core import get_relevant_memories
                memory_query = f"agent:{agent_id} {_last_user_msg[:200]}"
                memories_str = await asyncio.to_thread(get_relevant_memories, memory_query)
                if memories_str:
                    # Hard cap: max 600 chars of memory regardless of what Qdrant returns
                    mem_budget = min(600, _headroom * 3)  # 3 chars per token safety margin
                    system_prompt = system_prompt + f"\n\n[RELEVANT MEMORIES]\n{memories_str[:mem_budget]}"
                    log.debug("[%s] Injected %d chars of memory (%d token headroom)", agent_id, min(600, len(memories_str)), _headroom)
            else:
                log.debug("[%s] Skipping memory injection — only %d tokens headroom", agent_id, _headroom)
    except Exception as mem_err:
        log.debug("Memory augmentation skipped: %s", mem_err)

    base_messages = _inject_system_prompt(messages, system_prompt)


    MAX_EMPTY_RETRIES = 2
    for empty_retry in range(MAX_EMPTY_RETRIES + 1):
        try:
            response = await _complete_for_tool_decision(litellm_model, base_messages, fallbacks)
            content = response.choices[0].message.content

            if not content or not content.strip():
                # --- SELF-HEALING: Learn from empty response ---
                critique = (
                    f"REFLEXION: Agent '{agent_id}' returned an EMPTY response on attempt {empty_retry + 1}. "
                    f"The system prompt for this agent may be missing, the context may be malformed, "
                    f"or the model emitted an immediate EOS token. Fix: ensure agent has a defined role in "
                    f"system_prompts.py and that its prompt instructs it to output a JSON object."
                )
                log.warning("[%s] Empty response (attempt %d/%d) — storing reflexion: %s", agent_id, empty_retry + 1, MAX_EMPTY_RETRIES + 1, critique)
                try:
                    from runtime_v2.services.memory_core import remember_fact
                    import asyncio as _asyncio
                    _asyncio.create_task(
                        _asyncio.to_thread(remember_fact, critique, category="self_reflection")
                    )
                except Exception:
                    pass

                if empty_retry < MAX_EMPTY_RETRIES:
                    # Query self_reflection shard for what went wrong with this agent before
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

                    # Inject a recovery directive at the END of messages to jolt the model
                    recovery_hint = (
                        f"SYSTEM RECOVERY (attempt {empty_retry + 2}): Your previous response was completely empty. "
                        f"You MUST output a valid JSON object now. Do not output anything else. "
                        f"Allowed actions: {', '.join(allowed)}. "
                        f"Example: {{\"action\":\"filesystem\",\"operation\":\"list\",\"path\":\"runtime_v2\"}}"
                        f"{past_lessons}"
                    )

                    base_messages = base_messages + [
                        {"role": "user", "content": recovery_hint}
                    ]
                    log.warning("[%s] Retrying with recovery prompt (attempt %d)...", agent_id, empty_retry + 2)
                    continue
                else:
                    log.error("[%s] Agent returned empty after %d retries. Giving up.", agent_id, MAX_EMPTY_RETRIES + 1)
                    return {"action": "final", "response": f"[SYSTEM: Agent '{agent_id}' returned empty response after {MAX_EMPTY_RETRIES + 1} attempts. Model output was likely truncated. To fix this, please retry the request with a narrower scope (e.g., specify a smaller path, or use --max-characters/--max-chunks if applicable). Check its system prompt definition.]"}

            try:
                result = _extract_json(_normalize_model_json(content))
            except Exception as parse_exc:
                # JSON parse failed (e.g. "Missing action after normalization") —
                # treat exactly like an empty response so the retry+reflexion loop fires
                log.warning("[%s] JSON parse failed: %s — treating as empty response (attempt %d)", agent_id, str(parse_exc)[:80], empty_retry + 1)
                content = ""  # Force the empty-response branch below on next iteration
                critique = f"REFLEXION: Agent '{agent_id}' produced malformed JSON on attempt {empty_retry+1}: {str(parse_exc)[:150]}. Model may have had context overflow — the prompt was too long and the JSON was truncated before the 'action' field."
                try:
                    from runtime_v2.services.memory_core import remember_fact
                    import asyncio as _asyncio
                    _asyncio.create_task(_asyncio.to_thread(remember_fact, critique, category="self_reflection"))
                except Exception:
                    pass
                if empty_retry < MAX_EMPTY_RETRIES:
                    base_messages = base_messages + [{"role": "user", "content": f"SYSTEM RECOVERY: Your JSON was malformed or truncated. Output ONLY a valid JSON object. Allowed actions: {', '.join(allowed)}. Example: {{\"action\":\"filesystem\",\"operation\":\"list\",\"path\":\".\"}}"}]
                    continue
                return {"action": "final", "response": f"[SYSTEM: {agent_id} produced malformed JSON after {MAX_EMPTY_RETRIES+1} attempts. Check context length.]"}
            
            # Pre-validate: if the model hallucinated an action not in allowed_tools,
            # silently coerce it to a valid action instead of feeding the error back
            result_action = result.get("action", "final")
            if result_action not in allowed:
                log.warning("[%s] Model hallucinated action '%s' (allowed: %s). Coercing to filesystem/final.", agent_id, result_action, allowed)
                if "filesystem" in allowed:
                    # For agents like coder/debugger, assume they meant to write code
                    result["action"] = "filesystem"
                    # Preserve any content/path the model might have included
                    if "path" not in result and "content" not in result:
                        # Model didn't provide filesystem params, fall back to final
                        result["action"] = "final"
                        result["response"] = result.get("response", result.get("task", "Task completed."))
                else:
                    result["action"] = "final"
                    result["response"] = result.get("response", result.get("task", "Task completed."))
            
            log.debug("[%s] Tool decision: %s", agent_id, result.get("action"))
            return result

        except Exception as first_exc:
            import traceback
            traceback.print_exc()
            log.warning("[%s] Tool decision request failed: %s (will use default)", agent_id, str(first_exc)[:100])
            # Store this failure in reflexion memory so agent knows next time
            try:
                from runtime_v2.services.memory_core import remember_fact
                import asyncio as _asyncio
                failure_memory = f"REFLEXION: Agent '{agent_id}' tool decision threw exception: {str(first_exc)[:200]}. This means the LLM call failed, possibly due to context overflow or network error."
                _fire_and_forget(_asyncio.to_thread(remember_fact, failure_memory, category="self_reflection"))
            except Exception as mem_exc:
                log.warning("[%s] Failed to store reflexion memory: %s", agent_id, str(mem_exc)[:200])
            return {"action": "final", "response": f"Unable to determine next action. Error: {repr(first_exc)}"}



async def stream_content(model: str, messages: list, agent_id: str) -> AsyncGenerator[tuple, None]:
    litellm_model = _get_litellm_model(agent_id, model)
    routing_mode = _get_routing_mode()
    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    # CRITICAL: Never use local Ollama models as LiteLLM fallbacks (see tool-decision path).
    fallbacks = [f["model"] for f in raw_fallbacks if not f["model"].startswith("ollama/")][:3]

    extra = {
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
        "max_tokens": 8192,
        "num_ctx": 4096,     # CRITICAL: Prevent Qwen from allocating 32k context and crashing VRAM
    }
    if litellm_model.startswith("ollama_chat/"):
        # IMPORTANT: `think` must be at TOP LEVEL, not inside `options`.
        # Ollama's /api/chat endpoint silently ignores think inside options.
        extra["extra_body"] = {"think": False}
    kwargs = _build_kwargs(litellm_model, extra, fallbacks)
    kwargs["timeout"] = 900.0

    try:
        kwargs["max_retries"] = 0
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            if not chunk.choices:
                continue
            piece = chunk.choices[0].delta.content or ""
            if piece:
                yield piece, "content"
    except Exception as exc:
        log.error("[%s] stream error: %s", agent_id, exc)
        yield str(exc), "error"













