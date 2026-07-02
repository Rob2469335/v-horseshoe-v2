"""Stream runner - uses litellm for multi-provider structured output and streaming."""
import json
import re
import logging
from typing import AsyncGenerator, Optional
import litellm
from dotenv import load_dotenv
from runtime_v2.services.model_registry import get_model
from runtime_v2.services.fallback_manager import get_live_fallbacks

# Load environment variables (.env) so litellm automatically picks up API keys
load_dotenv()

# Suppress litellm telemetry and noisy success logging
litellm.telemetry = False
litellm.suppress_debug_info = True

log = logging.getLogger(__name__)

TOOL_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [
            "delegate","web_search","filesystem","sandbox_repl",
            "vscode_automation","final"
        ]},
        "target_agent": {"type": "string"},
        "task":         {"type": "string"},
        "query":        {"type": "string"},
        "operation":    {"type": "string"},
        "path":         {"type": "string"},
        "content":      {"type": "string"},
        "old":          {"type": "string"},
        "new":          {"type": "string"},
        "language":     {"type": "string"},
        "code":         {"type": "string"},
        "command":      {"type": "string"},
        "args":         {"type": "array", "items": {"type": "string"}},
        "response":     {"type": "string"}
    },
    "required": ["action"]
}

# System prompt injected into every tool-decision call so the model knows
# exactly what JSON it must produce. This is the most important instruction
# for keeping routing stable across all providers (local and cloud).
TOOL_DECISION_SYSTEM = (
    "\n\n*** CRITICAL FORMATTING INSTRUCTION ***\n"
    "You must express your decision as a SINGLE VALID JSON OBJECT.\n"
    "The JSON must have the key \"action\" which must be one of:\n"
    "  delegate, web_search, filesystem, sandbox_repl, vscode_automation, final\n\n"
    "Example valid outputs:\n"
    "{\"action\": \"delegate\", \"target_agent\": \"coder\", \"task\": \"Write the function\"}\n"
    "{\"action\": \"final\", \"response\": \"Here is my answer...\"}\n"
    "{\"action\": \"web_search\", \"query\": \"Python multiprocessing best practices\"}\n\n"
    "DO NOT output anything other than the JSON object."
)


def _get_litellm_model(agent_id: str, fallback_model: str) -> str:
    """Resolve the litellm provider/model string based on the registry backend."""
    from runtime_v2.services.model_registry import get_model
    default_model, backend = get_model(agent_id)

    model = fallback_model if fallback_model else default_model

    if model.startswith("router/"):
        model = model.split("/", 1)[1]

    if model.startswith("ollama/"):
        return model

    if backend == "router":
        if "/" in model and not model.startswith("ollama/"):
            model = model.split("/", 1)[1]
        return f"ollama/{model}"

    if backend == "ollama":
        return f"ollama/{model}"
    elif backend == "openrouter":
        return f"openrouter/{model}"
    elif backend == "groq":
        return f"groq/{model}"
    elif backend == "nvidia":
        return f"openai/{model}"
    elif backend == "gemini":
        return f"gemini/{model}"
    else:
        if "/" in model:
            return model
        return f"{backend}/{model}"
def _build_kwargs(litellm_model: str, extra: dict, fallbacks: list) -> dict:
# os.environ["SSL_CERT_FILE"] = certifi.where()  # DISABLED: conflicts with monkey-patch causing recursion
# os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()  # DISABLED: see above
    """Build kwargs for litellm.acompletion safely."""
    kwargs = {
        "model": litellm_model,
        "fallbacks": fallbacks,
        **extra
    }
    return kwargs


def _inject_system_prompt(messages: list, system: str) -> list:
    """Prepend or merge the routing system prompt into the message list."""
    messages = list(messages)
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            messages[i] = {"role": "system", "content": m["content"] + system}
            return messages
    return [{"role": "system", "content": system}] + messages


def _extract_json(text: str) -> dict:
    """Robustly extract a JSON object from model output that may contain prose."""
    # Strip deepseek-style thinking blocks first
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    
    # 1. Markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())
    # 2. Brace counting
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
            
            if c == '\\':
                escape_next = True
                continue
                
            if c == '"':
                in_string = not in_string
                continue
                
            if not in_string:
                if c == '{':
                    brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return json.loads(text[start:i + 1].strip())
    # Try fixing single-quoted Python dict output from models
    try:
        import ast
        py_obj = ast.literal_eval(text.strip())
        if isinstance(py_obj, dict):
            return py_obj
    except Exception:
        pass
    raise ValueError(f"No JSON object found in model output: {text[:300]}")


async def get_tool_decision(model: str, messages: list, agent_id: str) -> Optional[dict]:
    """Ask model what action to take next. Returns structured dict or None on error."""
    litellm_model = _get_litellm_model(agent_id, model)
    raw_fallbacks = await get_live_fallbacks()
    fallbacks = [f["model"] for f in raw_fallbacks]

    # Always inject the routing system prompt so every provider knows the format
    messages = _inject_system_prompt(messages, TOOL_DECISION_SYSTEM)

    extra = {
        "messages": messages,
        "temperature": 0.2,
        "timeout": 300.0,  # Deepseek reasoning can take a while
    }
    
    # We intentionally DO NOT enforce structured JSON format natively for any provider.
    # Native strict JSON modes (like Ollama's format= or Groq's response_format) often
    # cause models (especially Qwen or DeepSeek) to return empty content or crash when
    # they want to output thought blocks or markdown backticks first.
    # Instead, we rely entirely on the system prompt and our robust _extract_json regex.

    kwargs = _build_kwargs(litellm_model, extra, fallbacks)

    try:
        response = await litellm.acompletion(**kwargs)
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("Model returned empty content")
        return _extract_json(content)
    except Exception as exc:
        log.error("[%s] tool decision failed: %s", agent_id, exc)
        return None


async def stream_content(model: str, messages: list, agent_id: str) -> AsyncGenerator[tuple, None]:
    """Stream free-text response - used for reviewer final verdict."""
    litellm_model = _get_litellm_model(agent_id, model)
    raw_fallbacks = await get_live_fallbacks()
    fallbacks = [f["model"] for f in raw_fallbacks]

    extra = {
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
        "timeout": 300.0,
    }
    # CRITICAL: _build_kwargs scopes api_base to Ollama only ΓÇö never leaks to fallbacks
    kwargs = _build_kwargs(litellm_model, extra, fallbacks)

    try:
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            piece = chunk.choices[0].delta.content or ""
            if piece:
                yield piece, "content"
    except Exception as exc:
        log.error("[%s] stream error: %s", agent_id, exc)
        yield str(exc), "error"



