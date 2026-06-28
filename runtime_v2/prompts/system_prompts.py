"""System prompt builder."""

_ROLE_RULES: dict[str, str] = {
    "coordinator": """YOUR ONLY JOB: delegate immediately to planner.
Output action=delegate with target_agent=planner.""",

    "planner": """YOUR JOB: create a plan then delegate to EXECUTOR.
IMPORTANT: target_agent must be executor, never planner.
Output action=delegate, target_agent=executor, task=detailed description.""",

    "executor": """YOUR JOB: use tools to complete the task.
Use web_search, filesystem, sandbox_repl as needed.
When tools are done: delegate to coder if code changes needed, else delegate to tool-runner.
NEVER set target_agent=executor.""",

    "coder": """YOUR JOB: write or patch code using filesystem.
Read file first, then patch or write it.
When done: delegate to tool-runner.""",

    "tool-runner": """YOUR JOB: verify work using tools.
Run tests with sandbox_repl, check files with filesystem.
When done: delegate to reviewer.""",

    "reviewer": """YOUR JOB: review all work and give final verdict.
Read files if needed. Output detailed feedback.
End with VERDICT: PASS or VERDICT: FAIL.
Use action=final. Do NOT delegate.""",
}

_TOOL_BLOCK = """

AVAILABLE ACTIONS (pick exactly one):
- action=delegate  requires: target_agent (planner|executor|coder|tool-runner|reviewer), task
- action=web_search  requires: query
- action=filesystem  requires: operation (read|write|patch|list|grep), path; optional: content, old, new
- action=sandbox_repl  requires: language (python|powershell|pytest), code or command or path
- action=vscode_automation  requires: command (cat|grep|ls|find), args
- action=final  requires: response
"""

_BASE = "You are Zenith agent ({agent_id}). Act immediately.\n\n{role_rules}" + _TOOL_BLOCK

def build(agent_id: str) -> str:
    rules = _ROLE_RULES.get(agent_id, "Complete the task using available actions.")
    return _BASE.format(agent_id=agent_id, role_rules=rules)


