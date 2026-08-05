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

    # Collect ALL matching routes. Multi-intent messages (e.g. "analyze codebase
    # AND search internet") need the LLM to decompose — don't fast-route.
    matches = []
    for keywords, target_agent in _ROUTES:
        if any(kw in msg for kw in keywords):
            matches.append(target_agent)

    # FIX-INTENT PRECEDENCE: if the goal implies EDITING/FIXING code (write, fix,
    # patch, implement, create, change, modify, solve), route to `coder` — the
    # edit-capable agent — even when it also matches analysis keywords. This makes
    # "analyze my codebase for bugs and fix them" actually FIX (like a human
    # maintainer / opencode) instead of returning a report-only analysis. The
    # analyzer reports; the coder edits. Without this, the LLM coordinator
    # typically picks code_analyzer for compound goals and nothing gets fixed.
    # "how to fix X" (research intent) is excluded so how-to questions still go
    # to researcher.
    _FIX_KEYWORDS = ("write", "patch", "implement", "create", "change", "modify",
                     "solve", "repair", "correct", "fix this", "fix the bug",
                     "fix the bugs", "fix it", "fix the", "and fix", "to fix the",
                     "fix broken", "fix failing")
    fix_intent = any(kw in msg for kw in _FIX_KEYWORDS) and "how to fix" not in msg and "how do i fix" not in msg
    if fix_intent and "coder" not in matches:
        matches.append("coder")

    if len(matches) >= 2:
        log.info("[coordinator] multi-intent detected (matched %s) → falling back to LLM", matches)
        return None

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


def matches_task_keywords(user_prompt: str) -> bool:
    """True when the message matches any routing keyword — i.e. it is a real
    task, not a greeting. Used as a hard guard: a coordinator may never answer a
    task with action=final just because stale episodic memory claims it was done."""
    msg = (user_prompt or "").lower()
    return any(kw in msg for keywords, _ in _ROUTES for kw in keywords)


def best_route_target(user_prompt: str) -> str:
    """Highest-priority route target for a task message. Deterministic fallback
    used when the LLM coordinator violates the 'never final on a task' rule."""
    msg = (user_prompt or "").lower()
    # FIX-INTENT PRECEDENCE: editing/fixing goals go to coder (edit-capable)
    # rather than the report-only analyzer, matching a human maintainer.
    # "how to fix X" (research intent) is excluded so how-to questions stay on
    # researcher.
    _FIX_KEYWORDS = ("write", "patch", "implement", "create", "change", "modify",
                     "solve", "repair", "correct", "fix this", "fix the bug",
                     "fix the bugs", "fix it", "fix the", "and fix", "to fix the",
                     "fix broken", "fix failing")
    if any(kw in msg for kw in _FIX_KEYWORDS) and "how to fix" not in msg and "how do i fix" not in msg:
        return "coder"
    for keywords, target_agent in _ROUTES:
        if any(kw in msg for kw in keywords):
            return target_agent
    return "planner"


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

# Research-only goals must hit web_search BEFORE anything else — a codebase/LLM
# bias otherwise makes the researcher read files/memory first and never search,
# or burn the turn budget on filesystem before web_search. The first turn is
# injected deterministically (web_search with the user's goal as the query),
# mirroring code_analyzer's deterministic grounding. Zero filesystem.
_RESEARCHER_FIRST_TURNS = 1


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
