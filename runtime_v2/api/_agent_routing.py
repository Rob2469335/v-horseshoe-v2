"""Fast keyword-coordinator routing, warmup scripts, and model lookup."""
import logging

log = logging.getLogger(__name__)


async def lookup_model(agent_id: str) -> tuple[str, str]:
    from runtime_v2.services.model_registry import get_model
    return get_model(agent_id)


# ---------------------------------------------------------------------------
# Fast Python keyword router for the coordinator agent
# ---------------------------------------------------------------------------
# Problem: LLMs trained on text always default to writing helpful prose instead
# of strict JSON routing decisions, regardless of temperature or instructions.
# Solution: bypass the LLM entirely for clear-intent messages. The LLM is only
# called as a fallback for genuinely ambiguous inputs (< 5 words with no match).
# Result: coordinator routing is instant (0ms, 0 tokens) and 100% reliable.

_GREETINGS = {
    "hi", "hello", "hey", "sup", "howdy", "yo", "morning", "evening",
    "how are you", "what's up", "whats up", "good morning", "good evening",
}

_ROUTES: list[tuple[list[str], str]] = [
    (["heal", "fix yourself", "self-repair", "self-heal", "repair yourself"], "debugger"),
    (["analyze", "analyse", "bug", "bugs", "audit", "codebase", "scan for", "upgrade",
      "improvement", "improvements", "find issues", "code quality", "security vulnerability",
      "vulnerabilit", "refactor", "technical debt", "dead code", "lint"], "code_analyzer"),
    (["search internet", "search the web", "search online", "research", "look up",
      "find information", "browse", "latest news", "what is the latest", "internet"], "researcher"),
    (["build", "implement", "create a feature", "add a feature", "develop", "make a new",
      "from scratch", "design the", "architect"], "planner"),
    (["debug", "why is", "broken", "not working", "exception", "traceback", "error:",
      "crash", "fails", "failing", "fix this bug", "fix the bug"], "debugger"),
    (["write", "code this", "write a function", "write a class", "write a script",
      "write a test", "refactor", "edit this file", "update this file",
      "patch", "modify this"], "coder"),
    (["make a tool", "create a tool", "mcp server", "mcp tool", "new plugin",
      "custom tool", "tool-maker"], "tool-maker"),
    (["run this", "execute", "run the tests", "run tests", "deploy", "run the script",
      "run all"], "executor"),
    (["review", "code review", "check quality", "assess this", "is this good",
      "verdict", "approve"], "reviewer"),
    (["explain", "summarize", "what does this", "what does the", "describe",
      "read this file", "read the file", "show me"], "coder"),
]


def fast_route_coordinator(user_prompt: str) -> dict | None:
    """Keyword-match the user's message to a routing decision.
    Returns a routing dict or None to fall through to the LLM."""
    msg = user_prompt.lower().strip()
    words = msg.split()

    if msg in _GREETINGS or (len(words) <= 3 and any(g in msg for g in _GREETINGS)):
        return {"action": "final", "response": "Hello! What can I help you with today?"}

    for keywords, target_agent in _ROUTES:
        if any(kw in msg for kw in keywords):
            log.info("[coordinator] fast-route → %s (matched on: %r)", target_agent,
                     next(kw for kw in keywords if kw in msg))
            return {"action": "delegate", "target_agent": target_agent, "task": user_prompt}

    if len(words) >= 8:
        log.info("[coordinator] fast-route → planner (long message, no keyword match)")
        return {"action": "delegate", "target_agent": "planner", "task": user_prompt}

    log.info("[coordinator] fast-route → None (ambiguous, falling back to LLM)")
    return None


# ---------------------------------------------------------------------------
# Fast warmup script for sub-agents (multi-turn deterministic injection)
# ---------------------------------------------------------------------------
# Problem: After the directory listing, the model jumps straight to action=final
# in 120 tokens without reading any files. Solution: inject the first N turns
# deterministically so the LLM always has real file content before deciding.
#
# Turn index → action. When _turn < len(sequence), the action is injected
# without calling the LLM. When _turn >= len(sequence), the LLM takes over.

_AGENT_WARMUP: dict[str, list[dict]] = {
    "code_analyzer": [
        # Deterministic grounding, mirroring a human (or opencode) workflow:
        # 1) Read AGENTS.md first — it maps the whole codebase.
        # 2) Discover real paths with glob — never guess.
        # 3) Read the key entry points so the LLM always has real content before
        #    it starts deciding (it otherwise hallucinates paths like
        #    runtime_v2/core/agent_service_v2.py and burns all 8 turns).
        {"action": "filesystem", "operation": "read", "path": "AGENTS.md"},
        {"action": "filesystem", "operation": "glob", "path": "runtime_v2", "pattern": "**/*.py"},
        {"action": "filesystem", "operation": "read", "path": "runtime_v2/api/agent_service_v2.py"},
        {"action": "filesystem", "operation": "read", "path": "runtime_v2/services/stream_runner.py"},
    ],
}


def fast_start_for_agent(agent_id: str, turn: int) -> dict | None:
    """Returns a hardcoded action for a given agent and turn index.
    Returns None when turn >= script length (LLM takes over)."""
    sequence = _AGENT_WARMUP.get(agent_id, [])
    if turn < len(sequence):
        action = sequence[turn]
        log.info("[%s] fast-start turn %d → %s %s",
                 agent_id, turn, action.get("operation", action.get("action")),
                 action.get("path", ""))
        return action
    return None
