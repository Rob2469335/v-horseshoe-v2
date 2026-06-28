"""Stream runner - uses Ollama structured output for tool decisions."""
import json
import logging
from typing import AsyncGenerator, Optional
import httpx
from runtime_v2.services.model_registry import OLLAMA_URL

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

async def get_tool_decision(model: str, messages: list, agent_id: str) -> Optional[dict]:
    """Ask model what action to take next. Returns structured dict or None on error."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": "1h",
        "format": TOOL_CALL_SCHEMA,
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            text = resp.json()["message"]["content"].strip()
            if text.startswith("```json"): text = text[7:]
            elif text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            return json.loads(text.strip())
    except Exception as exc:
        log.error("[%s] tool decision failed: %s", agent_id, exc)
        return None

async def stream_content(model: str, messages: list, agent_id: str) -> AsyncGenerator[tuple, None]:
    """Stream free-text response - used for reviewer final verdict."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": "1h",
        "options": {"temperature": 0.2, "num_predict": -1, "num_ctx": 4096},
    }
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                        if evt.get("done"):
                            break
                        piece = evt.get("message", {}).get("content", "")
                        if piece:
                            yield piece, "content"
                    except Exception:
                        pass
    except Exception as exc:
        log.error("[%s] stream error: %s", agent_id, exc)
        yield str(exc), "error"
