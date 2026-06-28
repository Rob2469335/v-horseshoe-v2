"""Parses tool calls from model output."""
import re, json
from typing import Optional, Tuple

_STANDARD = re.compile(r'<tool_call\s+name="([^"]+)">\s*(.*?)\s*(?:</tool_call>|$)', re.DOTALL)
_ATTR = re.compile(r'<tool_call\s+name="delegate"\s+target_agent="([^"]+)"\s+task="([^"]+)"\s*/?>')
_SHORT = re.compile(r'<(filesystem|web_search|vscode_automation|sandbox_repl|delegate|context7)>\s*(\{.*?\})\s*</\1>', re.DOTALL)

def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw.strip())
    except Exception:
        return {"raw": raw.strip()}

def extract(text: str) -> Optional[Tuple[str, dict]]:
    m = _STANDARD.search(text)
    if m:
        return m.group(1).strip(), _parse_json(m.group(2))
    m2 = _ATTR.search(text)
    if m2:
        return "delegate", {"target_agent": m2.group(1), "task": m2.group(2)}
    m3 = _SHORT.search(text)
    if m3:
        return m3.group(1).strip(), _parse_json(m3.group(2))
    return None
