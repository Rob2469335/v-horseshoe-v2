import pathlib, re

p = pathlib.Path(r'C:\Users\rober\Projects\v-horseshoe-v2\swarm_os\services\agent_service.py')
src = p.read_text(encoding='utf-8')

# Fix 1: Tighten system prompt to force tool use
old_instruction = '''            "AUTONOMOUS TOOL LOOP:",
            "You can execute tools by outputting EXACTLY this format and no other format:",
            "<tool_call name=\\"web_search\\">{\\\"query\\\": \\\"your search here\\\"}</tool_call>\\n\\nNEVER use any other XML format like <web_search> or <search>. Only use the tool_call format.",'''

new_instruction = '''            "CRITICAL RULE: You MUST use tools to answer questions. NEVER describe what you would do - DO IT.",
            "To use a tool output EXACTLY this on its own line (no other text before the closing tag):",
            "<tool_call name=\\"filesystem\\">{\\"operation\\": \\"list\\", \\"path\\": \\".\\"}</tool_call>",
            "WAIT for the Observation before continuing. Do NOT describe steps - execute them.",
            "Available tool names: filesystem, vscode_automation, web_search",'''

if old_instruction in src:
    src = src.replace(old_instruction, new_instruction)
    print("System prompt tightened OK")
else:
    print("System prompt not matched - trying partial")
    # Find and show the area
    idx = src.find("AUTONOMOUS TOOL LOOP")
    if idx > -1:
        print(repr(src[idx:idx+300]))

# Fix 2: Add native filesystem tool that bypasses MCP for basic ops
old_tool_dispatch = '''                        if tool_name == "delegate":'''
new_tool_dispatch = '''                        if tool_name == "filesystem":
                            import os, pathlib as _pl
                            op = params.get("operation", "list")
                            path = params.get("path", ".")
                            base = _pl.Path(r"C:\\Users\\rober\\Projects\\v-horseshoe-v2")
                            target = (base / path).resolve()
                            if op == "list":
                                items = [f.name + ("/" if f.is_dir() else "") for f in target.iterdir()] if target.exists() else []
                                result = {"ok": True, "items": items, "path": str(target)}
                            elif op == "read":
                                result = {"ok": True, "content": target.read_text(encoding="utf-8", errors="replace")[:3000] if target.exists() else "NOT FOUND"}
                            elif op == "write":
                                target.write_text(params.get("content", ""), encoding="utf-8")
                                result = {"ok": True, "written": str(target)}
                            else:
                                result = {"ok": False, "error": f"Unknown op: {op}"}
                        elif tool_name == "delegate":'''

if old_tool_dispatch in src:
    src = src.replace(old_tool_dispatch, new_tool_dispatch)
    print("Filesystem tool added OK")
else:
    print("Tool dispatch not matched")

p.write_text(src, encoding="utf-8")
print("Done")
