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
            "delegate","websearch","filesystem","sandboxrepl","vscodeautomation","final"
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
        "response": {"type": "string"}
    },
    "required": ["action"]
}

TOOL_DECISION_SYSTEM = (
    "\n\n*** CRITICAL FORMATTING INSTRUCTION ***\n"
    "You must express your decision as a SINGLE VALID JSON OBJECT.\n"
    "The JSON must have the key \"action\" which must be one of:\n"
    "  delegate, web_search, filesystem, sandbox_repl, vscodeautomation, final\n\n"
    "Example valid outputs:\n"
    "{\"action\": \"delegate\", \"target_agent\": \"coder\", \"task\": \"Write the function\"}\n"
    "{\"action\": \"final\", \"response\": \"Here is my answer...\"}\n"
    "{\"action\": \"web_search\", \"query\": \"Python multiprocessing best practices\"}\n\n"
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
    mode = os.getenv("SWARM_ROUTING_MODE", "auto").strip().lower()
    if mode not in ("auto", "local_only", "cloud_allowed"):
        return "auto"
    return mode

def _get_litellm_model(agent_id: str, fallback_model: str) -> str:
    default_model, backend = get_model(agent_id)
    # If the caller provides a fallback_model (e.g. adaptive routing chose the sidecar), use it!
    model = fallback_model if fallback_model else default_model

    if model.startswith("router/"):
        model = model.split("/", 1)[1]

    if model.startswith("ollama_chat/") or model.startswith("ollama/"):
        return model

    if "/" in model:
        return model

    if backend == "router":
        return f"ollama_chat/{model}"

    if backend == "ollama":
        return f"ollama_chat/{model}"
    if backend == "openrouter":
        return f"openrouter/{model}"
    if backend == "groq":
        return f"groq/{model}"
    if backend == "nvidia":
        return f"openai/{model}"
    if backend == "gemini":
        return f"gemini/{model}"
    return f"{backend}/{model}" if "/" not in model else model

def _build_kwargs(litellm_model: str, extra: dict, fallbacks: list) -> dict:
    return {"model": litellm_model, "fallbacks": fallbacks, **extra}

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
    text = re.sub(r"^\[[^\]]*\]\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    text = re.sub(r"\s*\[[^\]]*\]\s*$", "", text).strip()

    candidates = [text]
    for pat in [r"```(?:json)?\s*(\{.*?\})\s*```", r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})"]:
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            candidates.append(m.group(1).strip())

    for candidate in candidates:
        if not candidate or len(candidate) < 2:
            continue
        try:
            return _normalize_decision(json.loads(candidate))
        except Exception:
            pass

    start = text.find("{")
    if start != -1:
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
                            return _normalize_decision(json.loads(text[start:i + 1].strip()))
                        except Exception:
                            pass

    try:
        py_obj = ast.literal_eval(text.strip())
        if isinstance(py_obj, dict):
            return _normalize_decision(py_obj)
    except Exception:
        pass

    if text.strip():
        return {"action": "final", "response": text.strip()}

    log.warning(f"Could not extract JSON, defaulting to 'final' action. Text: {text[:100]}")
    return {"action": "final", "response": "Task processed."}

def _normalize_model_json(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.replace("True", "true").replace("False", "false").replace("None", "null")
    if "'" in s and '"' not in s:
        s = s.replace("'", '"')
    return s

async def _complete_for_tool_decision(litellm_model: str, messages: list, fallbacks: list):
    extra = {
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 4096,
        "timeout": 180.0,
        "max_retries": 0,
    }
    kwargs = _build_kwargs(litellm_model, extra, fallbacks)
    kwargs["num_retries"] = 0
    kwargs["timeout"] = 600
    kwargs["num_ctx"] = 32768
    return await litellm.acompletion(**kwargs)

def _get_cache_key(messages: list, agent_id: str) -> str:
    if messages:
        last_msg = messages[-1].get("content", "")[:200]
        return f"{agent_id}:{hash(last_msg)}"
    return f"{agent_id}:default"

def _get_cached_decision(cache_key: str) -> Optional[dict]:
    if cache_key in _decision_cache:
        decision, timestamp = _decision_cache[cache_key]
        if datetime.now() - timestamp < timedelta(seconds=_cache_ttl):
            return decision
        del _decision_cache[cache_key]
    return None

def _cache_decision(cache_key: str, decision: dict):
    _decision_cache[cache_key] = (decision, datetime.now())

async def get_tool_decision(model: str, messages: list, agent_id: str, allowed_tools: list = None) -> Optional[dict]:
    cache_key = _get_cache_key(messages, agent_id)
    cached = _get_cached_decision(cache_key)
    if cached:
        log.debug("[%s] Tool decision (cached): %s", agent_id, cached.get("action"))
        return cached

    litellm_model = _get_litellm_model(agent_id, model)
    routing_mode = _get_routing_mode()
    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    fallbacks = [f["model"] for f in raw_fallbacks[:5]]

    base_messages = _inject_system_prompt(messages, TOOL_DECISION_SYSTEM)

    try:
        response = await _complete_for_tool_decision(litellm_model, base_messages, fallbacks)
        content = response.choices[0].message.content
        if not content or not content.strip():
            log.warning("[%s] Empty response from model", agent_id)
            return {"action": "final", "response": "Model returned empty response."}
        result = _extract_json(_normalize_model_json(content))
        log.debug("[%s] Tool decision: %s", agent_id, result.get("action"))
        _cache_decision(cache_key, result)
        return result
    except Exception as first_exc:
        log.warning("[%s] Tool decision request failed: %s (will use default)", agent_id, str(first_exc)[:100])
        return {"action": "final", "response": "Unable to determine next action. Task delegated to default handler."}

async def stream_content(model: str, messages: list, agent_id: str) -> AsyncGenerator[tuple, None]:
    litellm_model = _get_litellm_model(agent_id, model)
    routing_mode = _get_routing_mode()
    raw_fallbacks = await get_live_fallbacks(mode=routing_mode)
    fallbacks = [f["model"] for f in raw_fallbacks]

    extra = {
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
        "max_tokens": 4096,
        "timeout": 300.0,
    }
    kwargs = _build_kwargs(litellm_model, extra, fallbacks)

    try:
        kwargs["num_retries"] = 0
        kwargs["timeout"] = 600
        kwargs["num_ctx"] = 32768
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            piece = chunk.choices[0].delta.content or ""
            if piece:
                yield piece, "content"
    except Exception as exc:
        log.error("[%s] stream error: %s", agent_id, exc)
        yield str(exc), "error"













