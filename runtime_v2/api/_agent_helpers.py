"""Pure helpers, constants, and goal-routing utilities for the agent loop.

Extracted from `agent_service_v2.py` (2026-09-10 refactor, step 1/2). Every
symbol here is a pure function or a module-level constant with NO dependency on
`AgentServiceV2` or `_CallState`, so the extraction is a pure move — no behavior
change. `agent_service_v2.py` re-exports each name (`X as X`) so existing callers
(`from runtime_v2.api.agent_service_v2 import X`) keep working unchanged.

NOTE: mutable runtime state (`_failure_lessons_seen` and its lock) deliberately
stays in `agent_service_v2.py` — moving a mutable that tests patch by path
(`patch("...agent_service_v2._failure_lessons_seen")`) into this module would
rebind the name in the old module while the consumer reads the object here,
silently turning the patch into a no-op (a false pass).
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


# Internet-involving goal keywords (used to force web_search-first on analysis
# agents, so the warmup's filesystem reads never starve the web portion).
_INTERNET_GOAL_RE = re.compile(
    r"search (the )?(internet|web)|on the internet|via web|web ?research|"
    r"latest|sota|best practices|current state of|"
    r"how(-| )to|what.s the (newest|latest)",
    re.IGNORECASE,
)

# Fix-intent keywords: a goal containing these implies the agent should EDIT code,
# not just report on it. Used to force routing to the edit-capable `coder` agent.
# "how to fix X" (research intent) is excluded so how-to questions stay on
# researcher.
_FIX_INTENT_RE = re.compile(
    r"\bfix(es|ed|ing)?\b|\bpatch\b|\bwrite\b|\bimplement\b|\bcreate\b|"
    r"\bchange\b|\bmodify\b|\bsolve\b|\brepair\b|\bcorrect\b|\bupdate\b",
    re.IGNORECASE,
)


def _append_diary_line(diary_path, record: dict) -> None:
    """Append one diary record (runs inside asyncio.to_thread — never on the
    event loop)."""
    import json as _json

    with open(diary_path, "a", encoding="utf-8") as f:
        f.write(_json.dumps(record, ensure_ascii=False) + "\n")


def _strip_web_tools_for_local_analysis(
    agent_id: str, allowed: list, goal: str
) -> list:
    """2026-09-10: for a code_analyzer run on a NON-internet goal, drop
    web_search/web_fetch from the decision tool surface. Otherwise the (trained)
    model invents a research step — observed live: fabricating
    github.com/runtime-bridge/runtime-v2 (a nonexistent repo) on the read-only
    goal 'analyze my codebase for bugs and upgrades' instead of producing the
    codebase report. Matches the system's own internet classification: goals the
    goal-loop considers local must not be able to drift online. Internet-flagged
    goals and other agents keep their full tool surface."""
    if (
        agent_id == "code_analyzer"
        and allowed
        and (goal or "").strip()
        and not _INTERNET_GOAL_RE.search(goal)
    ):
        return [t for t in allowed if t not in ("web_search", "web_fetch")]
    return allowed


def _is_fix_intent(text: str) -> bool:
    """True when the goal directs the agent to EDIT code (not just research a
    'how to fix' question)."""
    low = (text or "").lower()
    if "how to fix" in low or "how do i fix" in low or "how do you fix" in low:
        return False
    return bool(_FIX_INTENT_RE.search(low))


def _is_authorization_denial(result: dict | None) -> bool:
    """True when a tool result is an authorization DENIAL (expired/used/denied
    pending approval) rather than a normal tool-execution error.

    This is the load-bearing discriminator for the 2026-09-06 coordinator
    fabrication fix: a denied call means the tool did NOT execute, so the agent
    must not be allowed to retry-and-continue into a fabricated 'successful'
    final. It must ONLY match denial-type results — generic `ok: False` (real
    tool errors) keep the normal retry path.
    """
    if not result:
        return False
    if result.get("authorization") == "DENY":
        return True
    err = str(result.get("error", ""))
    return "pending approval no longer valid" in err or "Authorization DENIED" in err


# L1 (2026 structural verifier): template / placeholder finals. These are the
# "the agent short-circuited instead of doing the work" responses — a bare
# completion sentence with no substantive content. The goal-loop also checks
# these, but the agent loop must fail-closed on them too (before the final is
# accepted / fed to outcome/remember).
_PLACEHOLDER_RE = re.compile(
    r"^\s*(task\s+(completed|complete|done)|all\s+done|done|completed|finished|"
    r"success|goal\s+achieved|ok|okay|no\s+(changes|issues|errors|improvements))\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _is_placeholder_final(text: str, goal: str = "") -> bool:
    """True when a final response is a bare completion/template placeholder with
    no substantive content (e.g. 'Task completed.' / 'Done.' / 'No changes.'),
    even when it is a complete sentence — the structural-verifier signal that
    the agent finished without doing/describing the requested work."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return True

    if goal:
        g = re.sub(r"^(Goal|Task|Task Goal|ORIGINAL GOAL)\s*[:：]\s*", "", goal.strip(), flags=re.IGNORECASE)
        for marker in ("*** CRITICAL INSTRUCTION ***", "CRITICAL INSTRUCTION", "\n\nYou are the", "<EPHEMERAL_MESSAGE>"):
            idx = g.find(marker)
            if idx > 0:
                g = g[:idx].strip()
        
        stopwords = {"the", "a", "an", "this", "that", "it", "is", "are", "was", "were", "to", "for", "of", "in", "and", "or", "on", "with", "as", "by", "be", "at"}
        t_words = set(w for w in re.findall(r"\b\w+\b", t.lower()) if w not in stopwords)
        g_words = set(w for w in re.findall(r"\b\w+\b", g.lower()) if w not in stopwords)
        if len(g_words) >= 3:
            overlap = len(g_words.intersection(t_words))
            # If the response is basically just echoing the goal words,
            # and it's suspiciously short (not a real findings report)
            if overlap / len(g_words) >= 0.75 and len(t) < max(200, len(g) * 2):
                return True
        elif len(g_words) > 0 and len(g_words) < 3:
            # For very short prompts, don't use the overlap heuristic because 
            # common structural words (like "review", "patch") can easily trigger it.
            pass

    if len(t) > 120:
        return False  # long responses are substantive enough to not be a template
    return bool(_PLACEHOLDER_RE.match(t))


def _clean_search_query(prompt: str, max_len: int = 300) -> str:
    """Extract a clean web-search query from a delegated goal prompt.

    The prompt passed to a delegated agent includes the coordinator's system
    wrapper ("CRITICAL INSTRUCTION ... You are the coordinator agent ... your
    ONLY job is to route..."). Sending that whole block as the web_search query
    wastes tokens and pollutes the results. Strip the instruction boilerplate and
    keep only the actual goal line.
    """
    text = (prompt or "").strip()
    # If it looks like a wrapped/delegated goal, cut at the first instruction marker.
    for marker in (
        "*** CRITICAL INSTRUCTION ***",
        "CRITICAL INSTRUCTION",
        "\n\nYou are the",
    ):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx].strip()
            break
    # Trim any leading "Goal:" / "Task:" labels.
    text = re.sub(r"^(Goal|Task|Task Goal)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


# Sentence-level intent keywords used by `_split_compound_goal`. Research
# sentences ask the agent to FIND external information; implementation
# sentences direct the agent to EDIT the codebase.
_RESEARCH_SENT_RE = re.compile(
    r"\b(research|search|find|investigate|look (up|into)|browse|survey)\b|"
    r"sota|state of the art|best practices|latest|github|arxiv|huggingface|"
    r"\bweb[- ]?research\b|via web|on the internet",
    re.IGNORECASE,
)
_IMPLEMENT_SENT_RE = re.compile(
    r"\b(implement|fix(?:es|ed|ing)?|patch|write|create|modify|change|"
    r"update|refactor|edit|solve|repair|correct|apply)\b|"
    r"use filesystem|analyze the codebase|rewrite broken code",
    re.IGNORECASE,
)


def _carve_implementation_clause(sentence: str):
    """Split a SINGLE run-on sentence that carries BOTH web-research and
    implementation intent into (research_part, implementation_part).

    Sentence-splitting alone cannot separate "analyze my codebase for bugs and
    search internet for improvements and upgrades always read agent md first
    (and apply the fixes)" — it is one sentence with no terminal punctuation, so
    the old code classified the whole thing as implementation (research and
    implementation both = the full goal, and "apply the fixes" flooded back into
    the web-only researcher task).

    Approach: find the RIGHTMOST implementation keyword and split there — the
    trailing edit clause becomes implementation, the prefix stays research. This
    is only safe when the prefix does NOT itself contain an edit keyword (a goal
    like "fix the bug and search the web for the best approach" has "fix" in its
    research prefix — splitting at the rightmost keyword "approach"/"apply" would
    misclassify the fix as research). When the prefix still shows edit intent,
    return None so the caller keeps the WHOLE sentence as implementation (nothing
    dropped, and the edit keyword is never lost).
    """
    impl_m = _IMPLEMENT_SENT_RE.search(sentence)
    if not impl_m:
        return None
    # Splitting at the FIRST implementation keyword gives (research_prefix,
    # edit_suffix): "analyze my codebase for bugs and search internet for
    # improvements... (and apply the fixes)" -> prefix = all the research, suffix
    # = "apply the fixes". Splitting at the LAST keyword instead is wrong: the
    # prefix would then contain the earlier "apply" of the same edit clause and
    # the safety guard would reject the carve (or mis-bucket a multi-word edit
    # phrase like "apply the fixes").
    split_at = impl_m.start()
    if split_at <= 0:
        # Edit intent leads the sentence ("fix the bug and search the web") —
        # the prefix is empty so there is no research clause to carve; keep the
        # whole sentence as implementation.
        return None
    prefix = sentence[:split_at].strip(" ,;:(()[]")
    suffix = sentence[split_at:].strip(" ,;:()[]")
    # Drop a trailing conjunction / parenthetical connector left over from the
    # carve ("... first (and apply the fixes)" -> prefix ends with "(and").
    prefix = re.sub(r"[\s(]*\b(?:and|then|also|to)\b[\s(]*$", "", prefix).strip(" ,;:()")
    # The prefix must be a genuine research clause — if it still contains an
    # implementation keyword we cannot carve safely; keep the whole sentence as
    # implementation instead.
    if _IMPLEMENT_SENT_RE.search(prefix):
        return None
    if not suffix:
        return None
    return (prefix, suffix)


def _split_compound_goal(goal: str):
    """Split a compound 'research THEN implement' goal into its two phases so the
    executor can delegate each phase to the agent whose turn budget matches its
    job — instead of handing the whole thing to researcher, which then burns all
    MAX_TURNS on the search phase before ever reaching analysis/implementation
    (the observed /upgrade turn_budget_exhausted for researcher).

    Returns (research_part, implementation_part). Sentences are classified by
    intent; implementation keywords take precedence when a sentence contains both
    (e.g. "Analyze the codebase and use filesystem to implement upgrades.").
    A phase with no matching sentences falls back to the full goal so nothing is
    silently dropped.
    """
    text = _clean_search_query(goal)
    if not text:
        return "", ""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    research_parts, implement_parts = [], []
    for s in sentences:
        has_research = bool(_RESEARCH_SENT_RE.search(s))
        has_implement = bool(_IMPLEMENT_SENT_RE.search(s))
        if has_implement and has_research:
            carved = _carve_implementation_clause(s)
            if carved is not None:
                # A single run-on sentence with BOTH web-research AND edit intent
                # ("analyze my codebase for bugs and search internet for
                # improvements and upgrades always read agent md first (and apply
                # the fixes)") — split it in place so the researcher gets only the
                # web half and coder gets the edit half, instead of the whole
                # sentence collapsing into implementation (research=implementation=
                # the full goal) and the edit keyword being flooded back into the
                # web-only researcher task.
                research_part_s, impl_part_s = carved
                if impl_part_s:
                    implement_parts.append(impl_part_s)
                if research_part_s:
                    research_parts.append(research_part_s)
                continue
            implement_parts.append(s)
        elif has_implement:
            # Implementation wins on ambiguity (compound "analyze the codebase
            # AND use filesystem to implement upgrades" is an EDIT directive).
            implement_parts.append(s)
        elif has_research:
            research_parts.append(s)
        else:
            # Neutral sentence: keep it with the phase that has content so far.
            (research_parts if not implement_parts else implement_parts).append(s)
    research = " ".join(research_parts).strip()
    implementation = " ".join(implement_parts).strip()
    if not research:
        research = text if not implementation else implementation
    # NOTE: NO fallback that copies `research` into `implementation`. A goal with
    # no implementation-intent sentence (e.g. "analyze my codebase for bugs and
    # search internet for improvements") is RESEARCH-ONLY — fabricating an
    # implementation phase out of the research text hands coder the full vague
    # goal, which it explores for MAX_TURNS without ever editing (the observed
    # turn_budget_exhausted in the /goal loop). An empty implementation part
    # tells the executor there is no edit phase to delegate; research is the
    # deliverable.
    return research, implementation


# Phrase patterns in a compound goal that are CODEBASE-ANALYSIS intent, NOT web
# research. When the executor hands the research phase to the `researcher`
# agent, these must be stripped out — researcher's role is PURE WEB research
# (its own prompt says "Do NOT read project files... unless the question
# specifically asks about THIS codebase"). Handing researcher the full
# "analyze my codebase AND search internet" task made it browse the filesystem
# (5 reads) on top of web_search, exhausting MAX_TURNS before finalizing.
_CODEEBASE_ANALYSIS_RE = re.compile(
    r"(analy[sz]e|audit|scan|inspect|review|find|identify|look (?:for|at))"
    r"\s+(?:the\s+)?(?:entire\s+)?(?:my\s+|our\s+|the\s+|your\s+)?"
    r"(?:codebase|code\s+base|code|project|repo(?:sitory)?|source)"
    r"|(?:find|identify|look\s+for)\s+(?:bugs?|issues?|problems?|vulnerabilities?)"
    # A dangling codebase-analysis fragment like "for bugs" (left after stripping
    # "analyze my codebase") still re-imports the find-bugs intent — the LLM sees
    # "for bugs" and browses the filesystem to hunt bugs. Strip the bare
    # bug/issue reference too, so the researcher task is unambiguously web-only.
    r"|for\s+(?:bugs?|issues?|problems?|vulnerabilities?|security\s+issues?)\b"
    # "find bugs in the codebase" leaves a dangling "in the codebase" after the
    # verb+bug phrase is removed — strip a trailing "in the codebase" residue.
    r"|\bin\s+(?:the\s+)?(?:codebase|code\s+base)\b",
    re.IGNORECASE,
)


def _research_only_task(goal: str) -> str:
    """Extract JUST the web-research portion of a compound goal for the
    `researcher` agent. Strips codebase-analysis phrases (analyze/audit/scan/
    inspect/review/find + codebase/code/project) so researcher does not browse
    the filesystem — it deep-reads the web and returns, and the codebase
    analysis is delegated to code_analyzer separately. Falls back to the
    cleaned goal when nothing is stripped."""
    text = _clean_search_query(goal)
    stripped = _CODEEBASE_ANALYSIS_RE.sub("", text)
    # Stripping "analyze my codebase for bugs" leaves a dangling "and search..."
    # — drop a leading conjunction so the task reads "search the internet for
    # improvements and upgrades", not "and search the internet...".
    stripped = re.sub(r"^\s*(?:and|to|then|also)\s+", "", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" ,;:.")
    if len(stripped) < 12:
        # Stripping removed nearly everything — keep the original (safer than
        # sending an empty task).
        return text
    return stripped


# When the executor spawns the web-RESEARCH phase, the child researcher is
# handed ONLY the tools for the job. The cloud researcher LLM has been observed
# burning turns on filesystem reads even under a web-only task (live trace: 4
# reads + 2 web_searches → turn_budget_exhausted, then the downstream code_analyzer
# produced a thin final with a fabricated "Not performed" excuse because it had no
# research content). The system prompt already tells researcher "do NOT read
# project files" — the tool list makes it impossible, not just discouraged.
_RESEARCHER_WEB_ONLY_TOOLS = ("web_search", "web_fetch", "final")


# The CLI (organism_console/ui/live_stream.py) feeds a user's typed answer to an
# `ask_user` back as a user message beginning with `Observation:` containing
# {"answer": "<text>"}. These helpers detect that continuation turn.
_OBSERVATION_ANSWER_RE = re.compile(r'"answer"\s*:\s*"([^"]*)"')


def _answer_from_history(messages: list) -> str | None:
    """Return the user's typed answer if the history is a post-ask_user
    continuation; otherwise None."""
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        content = str(m.get("content", ""))
        if content.strip().startswith("Observation:"):
            match = _OBSERVATION_ANSWER_RE.search(content)
            if match:
                return match.group(1).strip()
    return None


# The CLI feeds an approval decision back as:
#   Observation: {"approval": {"pending_id": "...", "approved": true|false}}
_OBSERVATION_APPROVAL_RE = re.compile(
    r'"approval"\s*:\s*\{[^}]*"pending_id"\s*:\s*"([^"]+)"[^}]*"approved"\s*:\s*(true|false)\s*[^}]*\}'
)


def _approval_from_history(messages: list) -> dict | None:
    """Return {"pending_id": str, "approved": bool} if the history carries an
    approval Observation; otherwise None. Used by the deterministic
    approval-resolution hook (the approval decision is a CODE decision, never
    left to the LLM)."""
    if not messages:
        return None
    last_msg = messages[-1]
    if last_msg.get("role") != "user":
        return None
    content = str(last_msg.get("content", ""))
    if content.strip().startswith("Observation:"):
        match = _OBSERVATION_APPROVAL_RE.search(content)
        if match:
            return {
                "pending_id": match.group(1),
                "approved": match.group(2) == "true",
            }
    return None


def _is_control_observation(message) -> bool:
    """True for control-plane user messages the CLI emits to resolve an approval
    or an ask_user answer in-run ('Observation: {"approval": ...}' /
    'Observation: {"answer": ...}'). These are one-shot, in-process control keys
    — not conversation content — and must never be treated as a real decision
    when replayed from durable history (CLAUDE_GOAL_HANDOFF Layer 1)."""
    if not isinstance(message, dict):
        return False
    content = str(message.get("content", ""))
    if not content.strip().startswith("Observation:"):
        return False
    return '"approval"' in content or '"answer"' in content


def _original_goal(messages: list) -> str:
    """The first real (non-Observation) user message — the goal the coordinator
    asked about before the ask_user continuation."""
    for m in messages:
        if m.get("role") != "user":
            continue
        content = str(m.get("content", ""))
        if not content.strip().startswith("Observation:"):
            return content.strip()
    return ""


def _trim_context_messages(
    messages: list, initial_messages_len: int, budget: int
) -> list:
    """Trim the context window for the next LLM decision call.

    The INITIAL messages (messages[:initial_messages_len] — the user's task and
    any delegated findings from child_history) are ALWAYS preserved. Only the
    NEW messages generated by tool turns since this run's start are windowed to
    `budget` (MAX_HISTORY_TURNS*2). The old trim blindly kept the LAST `budget`
    messages, so a 4-step tool warmup (8 messages) pushed the inherited
    researcher findings completely out of the window — the model then had no
    web research in context and hallucinated "Internet search: Not performed".
    System messages are prepended unchanged."""
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    initial_non_sys = [
        m for m in messages[:initial_messages_len] if m.get("role") != "system"
    ]
    new_non_sys = [
        m for m in messages[initial_messages_len:] if m.get("role") != "system"
    ]
    if len(new_non_sys) > budget:
        new_non_sys = new_non_sys[-budget:]
    return sys_msgs + initial_non_sys + new_non_sys
