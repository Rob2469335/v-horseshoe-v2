"""System prompt builder."""

_ROLE_RULES: dict[str, str] = {
    "coordinator": """YOUR ONLY JOB: delegate immediately to planner.
Output action=delegate with target_agent=planner.""",

    "planner": """YOUR JOB: create a plan then delegate to EXECUTOR.
IMPORTANT: target_agent must be executor, never planner.
You MUST write your implementation plan to .swarm_brain/plan.md using the filesystem tool before delegating.
Output action=delegate, target_agent=executor, task=detailed description.""",

    "executor": """YOUR JOB: use tools to complete the task.
You MUST read .swarm_brain/plan.md using the filesystem tool before taking any other action.
Use web_search, filesystem, sandbox_repl as needed.
When tools are done: delegate to coder if code changes needed, else delegate to tool-runner.
NEVER set target_agent=executor.""",

    "coder": """YOUR JOB: write or patch code using filesystem.
You MUST read .swarm_brain/plan.md before patching or writing code.
When done: delegate to tool-runner.""",

    "tool-runner": """YOUR JOB: verify work using tools.
Run tests with sandbox_repl, check files with filesystem.
When done: delegate to reviewer.""",

    "reviewer": """YOUR JOB: review all work and give final verdict.
Read files if needed. Output detailed feedback.
End with VERDICT: PASS or VERDICT: FAIL.
Use action=final. Do NOT delegate.""",
}

_TOOL_DEFINITIONS = {
    "delegate": "- action=delegate  requires: target_agent (planner|executor|coder|tool-runner|reviewer), task",
    "web_search": "- action=web_search  requires: query",
    "filesystem": "- action=filesystem  requires: operation (read|write|patch|list|grep), path (relative to workspace); optional: content, old, new",
    "sandbox_repl": "- action=sandbox_repl  requires: language (python|powershell|pytest), code or command or path",
    "vscode_automation": "- action=vscode_automation  requires: command (cat|grep|ls|find|lint|find_symbol), args",
    "ask_user": "- action=ask_user  requires: question",
    "final": "- action=final  requires: response",
}

_AGENT_TOOLS = {
    "coordinator": ["delegate", "ask_user"],
    "planner": ["delegate", "filesystem", "web_search", "ask_user"],
    "executor": ["delegate", "filesystem", "web_search", "sandbox_repl", "vscode_automation"],
    "coder": ["delegate", "filesystem", "vscode_automation"],
    "tool-runner": ["delegate", "sandbox_repl", "vscode_automation", "filesystem"],
    "reviewer": ["final", "filesystem", "vscode_automation"]
}

_BASE = "You are Zenith agent ({agent_id}). Act immediately.\n\n{role_rules}\n\nAVAILABLE ACTIONS (pick exactly one):\n{tools}"

def build(agent_id: str) -> str:
    rules = _ROLE_RULES.get(agent_id, "Complete the task using available actions.")
    allowed_tools = _AGENT_TOOLS.get(agent_id, ["delegate", "final", "filesystem"])
    tools_str = "\n".join([_TOOL_DEFINITIONS[t] for t in allowed_tools if t in _TOOL_DEFINITIONS])
    return _BASE.format(agent_id=agent_id, role_rules=rules, tools=tools_str)
