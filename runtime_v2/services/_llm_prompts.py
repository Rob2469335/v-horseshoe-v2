"""System prompt building for LLM tool-call decision making."""

JSON_REPAIR_PROMPT = (
    "Your previous reply was not accepted.\n"
    "Reply again with exactly one valid JSON object only.\n"
    "No markdown. No code fences. No prose. No explanation.\n"
    "DO NOT use XML tags like <tool_call> or <tool_code>. Just output raw JSON.\n"
    "Use an 'action' key."
)


def build_tool_decision_system(allowed_tools: list, mcp_schema: str = "") -> str:
    tools_csv = ", ".join(allowed_tools) if allowed_tools else "final"

    routing_only = set(allowed_tools) <= {"delegate", "final", "ask_user"}
    if routing_only:
        delegate_ex = ""
        if "delegate" in allowed_tools:
            delegate_ex = (
                '\nValid target_agent names: coordinator, planner, researcher, executor, '
                'coder, tool-runner, reviewer, debugger, tool-maker, code_analyzer.\n'
                'Example: {"action":"delegate","target_agent":"coder","task":"Read ./Modelfile"}'
            )
        final_ex = '\nExample: {"action":"final","response":"Hello!"}'
        return (
            "/no_think\n\n"
            f"Output ONE JSON object with action from: {tools_csv}."
            f"{delegate_ex}"
            f"{final_ex}"
            "\nNo markdown. No prose. Only JSON."
        )

    examples = []
    if "delegate" in allowed_tools:
        examples.append(
            'Valid target_agent names: coordinator, planner, researcher, executor, coder, '
            'tool-runner, reviewer, debugger, tool-maker, code_analyzer.\n'
            '{"thought": "Need code", "action": "delegate", "target_agent": "coder", '
            '"task": "Write the function"}'
        )
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
        "/no_think\n\n"
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
        "IMPORTANT for action=final: Describe ONLY completed work and factual findings in 'response'. Do NOT promise future steps or say 'Next, I will...' unless another step is scheduled.\n"
        "DO NOT use XML tags like <tool_call> or <tool_code>. Do NOT wrap your JSON in any tags.\n"
        "DO NOT output anything other than the JSON object."
    )
