"""Stream runner - uses litellm for multi-provider structured output and streaming."""
import json
import re
import logging
import asyncio
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
        "action": {"type": "string", "enum": [
            "delegate", "web_search", "filesystem", "sandbox_repl", "vscode_automation", "semantic_search", "remember", "ask_user", "lsp", "mcp", "final"
        ]},
        "target_agent": {"type": "string"},
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
    examples = []
    if "delegate" in allowed_tools:
        examples.append('{"action": "delegate", "target_agent": "coder", "task": "Write the function"}')
    if "final" in allowed_tools:
        examples.append('{"action": "final", "response": "Here is my answer..."}')
    if "filesystem" in allowed_tools:
        examples.append('{"action": "filesystem", "operation": "write", "path": "test.py", "content": "..."}')
    if "sandbox_repl" in allowed_tools:
        examples.append('{"action": "sandbox_repl", "language": "python", "code": "print(1)"}')
    if "lsp" in allowed_tools:
        examples.append('{"action": "lsp", "operation": "diagnostics", "file_path": "test.py"}')
    if "mcp" in allowed_tools:
        examples.append('{"action": "mcp", "server": "sqlite", "tool": "query", "arguments": {"query": "SELECT * FROM users"}}')
    if "web_search" in allowed_tools:
        examples.append('{"action": "web_search", "query": "Python multiprocessing"}')
    
    examples_str = "\n".join(examples)
    
    return (
        "\n\n*** CRITICAL FORMATTING INSTRUCTION ***\n"
        "You must express your decision as a SINGLE VALID JSON OBJECT.\n"
        "Do NOT output markdown code blocks. Just output raw JSON.\n\n"
        f"{mcp_schema}\n\n"
        "The required format is:\n"
        "{\n"
        f"    \"action\": \"<one of: {tools_csv}>\",\n"
        "    ... (additional required fields based on action)\n"
        "}\n\n"
        f"Example valid outputs:\n"
        f"{examples_str}\n\n"
        "Do not use any other top-level keys unless needed for the selected action.\n"
        "For action=final, use only: {\"action\":\"final\",\"response\":\"...\"}\n"
        "DO NOT output anything other than the JSON object."
    )

JSON_REPAIR_PROMPT = (
    "Your previous reply was not accepted.\n"
    "Reply again with exactly one valid JSON object only.\n"
    "No markdown. No code fences. No prose. No explanation.\n"
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
    for i, m in enumerate(messages):
        if m.get("role") == "system":
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
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
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
                            valid_jsons.append(_normalize_decision(json.loads(text[start:i + 1].strip())))
                        except Exception:
                            pass
                        break
        start = text.find("{", start + 1)
        
    if valid_jsons:
        return valid_jsons[-1]

    try:
        py_obj = ast.literal_eval(text.strip())
        if isinstance(py_obj, dict):
            return _normalize_decision(py_obj)
    except Exception:
        pass

    if text.strip():
        return {"action": "final", "response": text.strip()}

    import logging
    logging.getLogger(__name__).warning(f"Could not extract JSON, defaulting to 'final' action. Text: {text[:100]}")
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
        "max_tokens": 4096,
    }
    if not litellm_model.startswith("openrouter/") and not litellm_model.startswith("ollama/"):
        extra["response_format"] = {"type": "json_object"}
        
    kwargs = _build_kwargs(litellm_model, extra, fallbacks)
    kwargs["num_retries"] = 0
    kwargs["timeout"] = 600.0
    return await litellm.acompletion(**kwargs)

def _get_cache_key(messages: list, agent_id: str) -> str:
    if messages:
        # Just use the last user message text to keep hashing cheap
        last_msg = messages[-1].get("content", "")
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
    fallbacks = [f["model"] for f in raw_fallbacks[:5]]

    allowed = allowed_tools or ["delegate", "final", "filesystem", "sandbox_repl", "web_search", "vscode_automation", "semantic_search", "remember", "ask_user", "lsp", "mcp"]
    
    mcp_schema = ""
    if "mcp" in allowed:
        try:
            from runtime_v2.services.tool_executor import get_mcp_manager
            import json
            manager = await get_mcp_manager()
            # The manager is initialized at application startup, so tools are already cached.
            tools = manager.cached_tools
            if tools:
                mcp_schema = "AVAILABLE EXTERNAL MCP TOOLS:\n" + json.dumps(tools, indent=2) + "\n\n"
        except Exception as e:
            log.error(f"Failed to fetch MCP schemas: {e}")
            
    system_prompt = build_tool_decision_system(allowed, mcp_schema)
    base_messages = _inject_system_prompt(messages, system_prompt)

    try:
        response = await _complete_for_tool_decision(litellm_model, base_messages, fallbacks)
        content = response.choices[0].message.content
        if not content or not content.strip():
            log.warning("[%s] Empty response from model", agent_id)
            return {"action": "final", "response": "Model returned empty response."}
        result = _extract_json(_normalize_model_json(content))
        
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
        # _cache_decision(cache_key, result)  # Disabled
        return result
    except Exception as first_exc:
        import traceback
        traceback.print_exc()
        log.warning("[%s] Tool decision request failed: %s (will use default)", agent_id, str(first_exc)[:100])
        return {"action": "final", "response": f"Unable to determine next action. Error: {repr(first_exc)}"}

async def stream_content(model: str, messages: list, agent_id: str) -> AsyncGenerator[tuple, None]:
    litellm_model = _get_litellm_model(agent_id, model)
    routing_mode = _get_routing_mode()
    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    fallbacks = [f["model"] for f in raw_fallbacks]

    extra = {
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    kwargs = _build_kwargs(litellm_model, extra, fallbacks)
    kwargs["timeout"] = 300.0

    try:
        kwargs["num_retries"] = 0
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













