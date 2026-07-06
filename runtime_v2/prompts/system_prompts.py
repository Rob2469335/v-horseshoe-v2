"""System prompt builder."""

_ROLE_RULES: dict[str, str] = {
    "coordinator": """YOUR ONLY JOB: Orchestrate the high-level workflow.
You are the MANAGER. DO NOT perform actual tasks (like coding, analysis, or verifying) yourself.
If the user asks for a complex task (like "analyze my codebase"), YOU MUST delegate it. Do not refuse claiming you lack tools.
First, delegate to the planner to create a plan in .swarm_brain/plan.md. Wait for it to return via action=final.
Then, delegate to the executor to accomplish the plan. Wait for it to return via action=final.
When everything is complete, use action=final to return the overall result to the user.
For trivial greetings or questions not needing tasks, use action=final directly.""",

    "planner": """YOUR JOB: create a structured implementation plan.
You MUST write your implementation plan to .swarm_brain/plan.md using the filesystem tool.
When the plan is written, use action=final to return the plan back to the coordinator. Do NOT use delegate.""",

    "researcher": """YOUR JOB: gather context and synthesize it for the downstream agent.
Use web_search to find external information, and filesystem to read/grep local codebase files.
DO NOT mutate any files (no write/patch). Only gather information.
When finished, if you were delegated to, use action=final to return your findings. NEVER use delegate unless you are stuck.""",

    "executor": """YOUR JOB: Coordinate the team to accomplish the user's objective.
You are the top-level orchestrator.
Use the delegate tool to hand off sub-tasks to specialists (researcher, coder, tool-runner, reviewer, debugger) ONE AT A TIME.
Wait for them to return their results via action=final. Then delegate the next step.
When the entire goal is completed and verified, use action=final to return the final answer to the user.
NEVER set target_agent=executor.""",

    "coder": """YOUR JOB: write or patch code using the filesystem tool.
If a file needs to be created or modified, use action=filesystem immediately.
Do not delegate tasks to other agents unless you are physically unable to complete them.
When finished writing code, use action=final to return results back to the caller. Do NOT use delegate.""",

    "tool-runner": """YOUR JOB: verify work using tools.
Run tests with sandbox_repl, check files with filesystem.
When finished verifying, use action=final to return results back to the caller. Do NOT use delegate.""",

    "reviewer": """YOUR JOB: review all work and give final verdict.
Read files if needed using filesystem tool.
When finished reviewing, use action=final to return the verdict to the caller.
If passing, set response to VERDICT: PASS.
BEFORE passing a complex task, if you observed a non-obvious solution or bug fix, you MUST use the `remember` tool to store a self-reflection trace (category: "self_reflection") of how the team solved it, so future agents can learn from it.
If failing, set response to VERDICT: FAIL followed by FIXES_NEEDED: and a detailed list of required changes. Do NOT delegate.""",

    "debugger": """YOUR JOB: troubleshoot bugs and fix failures.
Use filesystem and sandbox_repl to diagnose errors.
When the bug is diagnosed or fixed, use action=final to return your findings back to the caller. Do NOT use delegate."""
}

_TOOL_DEFINITIONS = {
    "delegate": "- action=delegate  requires: target_agent (planner|researcher|executor|coder|tool-runner|reviewer|debugger), task",
    "web_search": "- action=web_search  requires: query",
    "filesystem": "- action=filesystem  requires: operation (read|write|patch|list|grep), path (relative to workspace); optional: content, old, new",
    "sandbox_repl": "- action=sandbox_repl  requires: language (python|powershell|pytest), code or command or path",
    "vscode_automation": "- action=vscode_automation  requires: command (cat|grep|ls|find|lint|find_symbol), args",
    "semantic_search": "- action=semantic_search  requires: query (natural language codebase query)",
    "remember": "- action=remember  requires: fact, category (user_preference|bug_fix|architecture_rule)",
    "ask_user": "- action=ask_user  requires: question",
    "final": "- action=final  requires: response",
}

_AGENT_TOOLS = {
    "coordinator": ["delegate", "ask_user", "final"],
    "planner": ["delegate", "filesystem", "semantic_search", "remember", "web_search", "ask_user", "final"],
    "researcher": ["delegate", "filesystem", "semantic_search", "remember", "web_search", "vscode_automation", "final"],
    "executor": ["delegate", "filesystem", "semantic_search", "remember", "web_search", "sandbox_repl", "vscode_automation", "final"],
    "coder": ["delegate", "filesystem", "semantic_search", "remember", "vscode_automation", "final"],
    "tool-runner": ["delegate", "sandbox_repl", "vscode_automation", "filesystem", "remember", "final"],
    "reviewer": ["final", "filesystem", "semantic_search", "remember", "vscode_automation"],
    "debugger": ["delegate", "filesystem", "semantic_search", "remember", "sandbox_repl", "vscode_automation", "final"]
}

_BASE = "You are Zenith agent ({agent_id}). Act immediately.\n\n{role_rules}\n\nAVAILABLE ACTIONS (pick exactly one):\n{tools}\n\nCRITICAL: You MUST respond with ONLY a valid JSON object matching the chosen action's parameters. Do not wrap it in markdown block quotes. Provide no other text."

def _get_tree_structure(root_dir: str) -> str:
    import os
    tree = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Exclude hidden dirs, venv, cache
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ['__pycache__', 'node_modules', 'venv', '.venv']]
        rel_path = os.path.relpath(dirpath, root_dir)
        if rel_path == ".":
            tree.append("/")
        else:
            tree.append(f"/{rel_path}")
    return "\n".join(tree)

def build(agent_id: str) -> str:
    rules = _ROLE_RULES.get(agent_id, "Complete the task using available actions.")
    allowed_tools = _AGENT_TOOLS.get(agent_id, ["delegate", "final", "filesystem"])
    tools_str = "\n".join([_TOOL_DEFINITIONS[t] for t in allowed_tools if t in _TOOL_DEFINITIONS])
    prompt = _BASE.format(agent_id=agent_id, role_rules=rules, tools=tools_str)
    
    # Inject Lightweight Codebase Directory Tree
    if agent_id in ["planner", "researcher", "coder", "debugger", "executor"]:
        import os
        tree_content = _get_tree_structure(os.getcwd())
        prompt += f"\n\n*** REPOSITORY DIRECTORY TREE ***\n{tree_content}\n*** END OF TREE ***\nUse `semantic_search` or `filesystem list` to explore contents.\n"
                
    return prompt
