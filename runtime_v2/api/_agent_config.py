"""Agent configuration constants and defaults."""

from typing import Dict, Tuple

MAX_TURNS = 8
MAX_DEPTH = 15

# Agents that must fetch content before finalizing
ANALYSIS_AGENTS = ("code_analyzer", "reviewer", "researcher")

# Agents that must actually search the internet before finalizing when the
# goal is internet-flagged. Broader than ANALYSIS_AGENTS, which also gates
# the separate _fetched_content report-only check that doesn't apply to
# coder (a file-editing agent, not a report-only one).
INTERNET_GOAL_AGENTS = ANALYSIS_AGENTS + ("coder",)

# Default metadata for each agent
_DEFAULTS: Dict[str, Tuple[str, str, str]] = {
    "coordinator": ("coordinator", "Delegates work to planner.", "reasoning"),
    "planner": ("planner", "Breaks tasks into steps.", "reasoning"),
    "researcher": (
        "researcher",
        "Gathers context via web and codebase search.",
        "fast",
    ),
    "executor": ("executor", "Executes steps with tools.", "fast"),
    "coder": ("coder", "Writes and patches code.", "coding"),
    "tool-runner": ("tool-runner", "Runs tests and verifications.", "fast"),
    "reviewer": ("reviewer", "Reviews work and gives verdict.", "reasoning"),
    "debugger": ("debugger", "Diagnoses failures and routes fixes.", "coding"),
    "tool-maker": ("tool-maker", "Creates custom MCP servers in Python.", "coding"),
    "code_analyzer": (
        "code_analyzer",
        "Systematically finds bugs and proposes improvements.",
        "reasoning",
    ),
}

# PERF: Maximum conversational turns kept in context window.
# At 6 turns the context hit 1324 tokens causing 36s prefill and 85s timeouts.
# At 4 turns stays under ~800 tokens keeping prefill under 15s at 60 t/s.
MAX_HISTORY_TURNS = 4

# Cap tool results so filesystem listings don't overflow the KV cache
MAX_RESULT_CHARS = 1200
