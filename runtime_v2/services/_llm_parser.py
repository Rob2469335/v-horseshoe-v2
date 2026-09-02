"""JSON extraction and normalization for LLM tool decisions."""

import json
import ast
import re
import logging
import asyncio

log = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()


def fire_and_forget(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


TOOL_CALL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "thought": {"type": "string"},
        "action": {
            "type": "string",
            "enum": [
                "delegate",
                "web_search",
                "filesystem",
                "sandbox_repl",
                "vscode_automation",
                "semantic_search",
                "remember",
                "ask_user",
                "lsp",
                "mcp",
                "mcp_register",
                "self_heal",
                "final",
                "github_research",
            ],
        },
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
        "question": {"type": "string"},
        "mode": {"type": "string"},
        "target_repo": {"type": "string"},
    },
    "required": ["action"],
}


def normalize_decision(obj: dict) -> dict:
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
        else:
            obj["action"] = "final"
            resp_val = obj.get(
                "summary",
                obj.get(
                    "content",
                    obj.get("analysis", obj.get("result", obj.get("explanation", ""))),
                ),
            )
            if not resp_val:
                resp_val = json.dumps(obj, ensure_ascii=False)
            elif not isinstance(resp_val, str):
                resp_val = json.dumps(resp_val, ensure_ascii=False)
            obj["response"] = resp_val
            log.debug("Inferred action=final from generic JSON answer object")

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


def extract_json(text: str) -> dict:
    raw_text = text or ""
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
                            valid_jsons.append(
                                normalize_decision(
                                    json.loads(
                                        text[start : i + 1].strip(), strict=False
                                    )
                                )
                            )
                        except Exception as parse_exc:
                            log.warning(f"Failed to parse JSON candidate: {parse_exc}")
                        break
        start = text.find("{", start + 1)

    if valid_jsons:
        return valid_jsons[0]

    try:
        py_obj = ast.literal_eval(text.strip())
        if isinstance(py_obj, dict):
            return normalize_decision(py_obj)
    except Exception as exc:
        log.debug("literal_eval salvage failed: %s", exc)

    # `text` was already fence-stripped above — reuse it for the salvage scan.
    stripped_fences = text
    salvage_jsons = []
    s_start = stripped_fences.find("{")
    while s_start != -1:
        s_brace = 0
        s_instr = False
        s_esc = False
        for j in range(s_start, len(stripped_fences)):
            ch = stripped_fences[j]
            if s_esc:
                s_esc = False
                continue
            if ch == "\\":
                s_esc = True
                continue
            if ch == '"':
                s_instr = not s_instr
                continue
            if not s_instr:
                if ch == "{":
                    s_brace += 1
                elif ch == "}":
                    s_brace -= 1
                    if s_brace == 0:
                        try:
                            salvage_jsons.append(
                                normalize_decision(
                                    json.loads(
                                        stripped_fences[s_start : j + 1], strict=False
                                    )
                                )
                            )
                        except Exception as exc:
                            log.debug("salvage candidate skipped: %s", exc)
                        break
        s_start = stripped_fences.find("{", s_start + 1)
    if salvage_jsons:
        return salvage_jsons[0]

    if text.strip():
        clean = text.strip()
        clean_no_xml = re.sub(
            r"</?(?:tool_call|tool_code|tools)[^>]*>", "", clean
        ).strip()
        if not clean_no_xml:
            raise ValueError(f"Model output only contained empty XML tags: {clean}")
        mid = len(clean) // 2
        if mid > 3 and clean[:mid].strip() == clean[mid:].strip():
            clean = clean[:mid].strip()
        elif mid > 3:
            for split in range(3, mid + 1):
                if clean[split : split * 2] == clean[:split]:
                    clean = clean[:split]
                    break
        return {"action": "final", "response": clean}

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
                if esc:
                    esc = False
                    continue
                if c == "\\":
                    esc = True
                    continue
                if c == '"':
                    in_str = not in_str
                    continue
                if not in_str:
                    if c == "{":
                        brace_count += 1
                    elif c == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            try:
                                think_jsons.append(
                                    normalize_decision(
                                        json.loads(
                                            think_content[t_start : i + 1], strict=False
                                        )
                                    )
                                )
                            except Exception as parse_exc:
                                log.warning(
                                    f"Failed to parse JSON candidate inside think block: {parse_exc}"
                                )
                            break
            t_start = think_content.find("{", t_start + 1)
        if think_jsons:
            log.warning(
                "Recovered JSON from inside <think> block (thinking suppression may have failed)"
            )
            return think_jsons[-1]

    log.warning(
        f"Could not extract JSON, defaulting to 'final' action. Text: {raw_text[:100]}"
    )
    return {"action": "final", "response": "Task processed."}


def normalize_model_json(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s
