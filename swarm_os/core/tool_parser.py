import re
import json
import logging

log = logging.getLogger(__name__)

class ToolParser:
    @staticmethod
    def parse(text: str) -> tuple[str, str] | None:
        # Check Pattern A: <tool_call name="tool">params</tool_call>
        match_a = re.search(r'<tool_call\s+name="([^"]+)">\s*(\{.*?\})\s*</tool_call>', text, re.DOTALL)
        if match_a:
            return match_a.group(1).strip(), match_a.group(2).strip()

        # Check Pattern B: <tool>tool</tool> params
        match_b = re.search(r'<tool>([^<]+)</tool>\s*(\{.*?\})', text, re.DOTALL)
        if match_b:
            return match_b.group(1).strip(), match_b.group(2).strip()

        # Check Pattern C: plain JSON object output
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                obj = json.loads(stripped, strict=False)
                if isinstance(obj, dict):
                    if "tool" in obj and isinstance(obj["tool"], str):
                        params = obj.get("params", {})
                        return obj["tool"].strip(), json.dumps(params)
                    if "tool_name" in obj and isinstance(obj["tool_name"], str):
                        params = obj.get("params", {})
                        return obj["tool_name"].strip(), json.dumps(params)
                    _cmd_val = obj.get("command", "")
                    _CLI_ONLY = {"/goal", "/plan", "/debug", "/compress", "/boot", "/exit", "/debate", "/chat", "/agents", "/tokens", "/trace", "/clear", "/model", "/focus"}
                    if ("command" in obj and isinstance(_cmd_val, str)
                        and _cmd_val.strip() in _CLI_ONLY):
                        return "command", json.dumps({"command": _cmd_val.strip()})
            except json.JSONDecodeError as e:
                log.debug("Pattern C JSON decode failed: %s", e)
                
        # Check Pattern D: markdown json block
        match_md = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match_md:
            try:
                obj = json.loads(match_md.group(1), strict=False)
                if isinstance(obj, dict):
                    if "tool" in obj and isinstance(obj["tool"], str):
                        return obj["tool"].strip(), json.dumps(obj.get("params", {}))
                    if "tool_name" in obj and isinstance(obj["tool_name"], str):
                        return obj["tool_name"].strip(), json.dumps(obj.get("params", {}))
            except json.JSONDecodeError as e:
                log.debug("Pattern D JSON decode failed: %s", e)

        # Check Pattern E: just a tool name in tags
        match_tool_only = re.search(r'<tool>([^<]+)</tool>', text)
        if match_tool_only:
            t = match_tool_only.group(1).strip()
            text_after = text[match_tool_only.end():].strip()
            if text_after.startswith("{") and text_after.endswith("}"):
                return t, text_after
            else:
                return t, "{}"

        # Fallback Check: loosely search for JSON with "tool_name"
        loose_match = re.search(r'\{[^{}]*"tool(?:_name)?"\s*:\s*"([^"]+)"[^{}]*\}', text, re.DOTALL)
        if loose_match:
            t_name = loose_match.group(1)
            try:
                obj = json.loads(loose_match.group(0), strict=False)
                if isinstance(obj, dict):
                    params = obj.get("params", {})
                    return t_name.strip(), json.dumps(params)
            except json.JSONDecodeError as e:
                log.debug("Fallback 1 JSON decode failed: %s", e)

        # Fallback Check 2: strip repeated markdown code-fences and salvage innermost valid JSON object
        # (advance past the scanned window, not one char, to avoid O(N^2) rescans on pathological input)
        stripped_fences = re.sub(r'```[a-zA-Z]*', '', text).replace('```', '').strip()
        salvage_objs = []
        s_start = stripped_fences.find("{")
        while s_start != -1:
            s_brace = 0
            s_instr = False
            s_esc = False
            s_scan_end = s_start
            for j in range(s_start, len(stripped_fences)):
                s_scan_end = j
                ch = stripped_fences[j]
                if s_esc: s_esc = False; continue
                if ch == "\\": s_esc = True; continue
                if ch == '"': s_instr = not s_instr; continue
                if not s_instr:
                    if ch == "{": s_brace += 1
                    elif ch == "}":
                        s_brace -= 1
                        if s_brace == 0:
                            try:
                                obj = json.loads(stripped_fences[s_start:j + 1], strict=False)
                                if isinstance(obj, dict) and ("tool" in obj or "tool_name" in obj):
                                    salvage_objs.append(obj)
                            except json.JSONDecodeError as e:
                                log.debug("Fallback 2 JSON decode failed: %s", e)
                            break
            s_start = stripped_fences.find("{", s_scan_end + 1)
        if salvage_objs:
            obj = salvage_objs[-1]
            t_name = obj.get("tool", obj.get("tool_name", "")).strip()
            params = obj.get("params", {})
            return t_name, json.dumps(params)

        return None
