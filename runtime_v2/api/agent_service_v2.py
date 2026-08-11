from __future__ import annotations
import json, logging
import asyncio
import time
import re
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

log = logging.getLogger(__name__)

# Internet-involving goal keywords (used to force web_search-first on analysis
# agents, so the warmup's filesystem reads never starve the web portion).
_INTERNET_GOAL_RE = re.compile(
    r"search (the )?(internet|web)|on the internet|via web|web ?research|"
    r"improvements?|upgrades?|latest|sota|best practices|current state of|"
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


def _is_fix_intent(text: str) -> bool:
    """True when the goal directs the agent to EDIT code (not just research a
    'how to fix' question)."""
    low = (text or "").lower()
    if "how to fix" in low or "how do i fix" in low or "how do you fix" in low:
        return False
    return bool(_FIX_INTENT_RE.search(low))


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


def _is_placeholder_final(text: str) -> bool:
    """True when a final response is a bare completion/template placeholder with
    no substantive content (e.g. 'Task completed.' / 'Done.' / 'No changes.'),
    even when it is a complete sentence — the structural-verifier signal that
    the agent finished without doing/describing the requested work."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return True
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
    for marker in ("*** CRITICAL INSTRUCTION ***", "CRITICAL INSTRUCTION", "\n\nYou are the"):
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
    r"\b(implement|fix|patch|write|create|modify|change|update|refactor|"
    r"edit|solve|repair|correct)\b|use filesystem|analyze the codebase|"
    r"rewrite broken code",
    re.IGNORECASE,
)


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
        if has_implement:
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


# Bounded dedup cache for reflexion lessons (agent, action, error) -> last store
# time. Prevents identical repeated failures from spamming ReflexionMemory.
_failure_lessons_seen: dict = {}


@dataclass
class _CallState:
    """Per-invocation mutable state. The AgentServiceV2 singleton is shared across
    concurrent /step/stream requests — keeping counters/status here prevents
    cross-run contamination (wrong premature-final aborts, mixed tool results)."""
    handler_status: str = ""
    premature_finals: int = 0
    reviewer_fails: int = 0
    tool_success: bool = False
    tool_action: str = ""
    tool_payload: dict = field(default_factory=dict)
    tool_result: Any = None
    tool_result_str: str = ""
    # Outcome-fitness: real-task-outcome signal fed to the evolutionary kernel.
    _start_time: float = 0.0
    _tool_attempts: int = 0
    _tool_successes: int = 0
    _turn: int = 0
    # Todo tracking (multi-step task state, like a human agent's checklist)
    todos: list = field(default_factory=list)
    todo_id: int = 0
    # Verify-after-change: set after a successful patch/write on a code file;
    # a final while pending_verify is rejected once so the agent tests first.
    pending_verify: bool = False
    _verify_final_rejected: bool = False
    # Internet-research tracking: set when the agent runs web_search successfully,
    # so internet-involving goals cannot short-circuit to final without it.
    did_web_search: bool = False
    # Set when the agent deep-reads at least one page via web_fetch — an
    # internet goal needs actual fetched content, not just search snippets.
    did_web_fetch: bool = False
    _web_final_rejected: bool = False
    # Top URLs from the last successful web_search, captured so the loop can
    # deterministically inject a web_fetch (the model — even cloud deepseek —
    # repeatedly re-selects web_search instead of web_fetch, loop-tripping the
    # circuit breaker before any page is ever read).
    last_search_urls: list = field(default_factory=list)
    _web_fetch_injected: bool = False
    # Executor compound-goal chaining phase flags: research was delegated (and
    # returned) so the implementation phase must now go to coder — deterministically,
    # because the executor LLM has been observed to re-loop instead of delegating.
    _executor_research_delegated: bool = False
    _executor_impl_delegated: bool = False
    # Set when the editing agent (`coder`) successfully writes or patches a
    # file this run. Used as a hard invariant: an editing agent that is handed a
    # fix-intent goal may NOT finalize without having actually modified code
    # (it otherwise just runs web_search and restates the goal — the repeated
    # /upgrade autonomous-loop failure where every attempt ended with
    # "No file changes detected").
    did_code_change: bool = False
    # L1 (2026 structural verifier): files actually READ this run (via filesystem
    # operation=read/read_all) vs merely LISTED/GLOBbed. An analysis agent may
    # not reference a file path in its final that it never actually read — only
    # seeing the name in a listing is not "read the content", so a vague final
    # that cites never-read paths fails closed (treated like a system failure).
    # L1 (2026 structural verifier): file paths the agent genuinely grounded on
    # this run. Populated from BOTH filesystem read/read_all AND real
    # semantic_search hits (whose returned chunks carry `File: <path>` lines) —
    # a semantic_search hit is real content grounding, not a placeholder dodge.
    # An analysis agent may not cite a .py path in its final unless it actually
    # saw that file's content this run (via read or a real search hit).
    read_paths: set = field(default_factory=set)
    # L1: number of times a final was rejected for a contract violation
    # (placeholder / unreferenced-read). Mirrors `premature_finals`: after 2
    # strikes the run aborts instead of looping forever under MAX_TURNS.
    _contract_finals: int = 0
    # L3 (2026 real-test signal): actual test outcome after a coder code change.
    # None = not yet run / no test suite; else 1.0 (exit 0) or 0.0 (failed).
    # Replaces the completion-proxy of outcome_fitness's test_pass when set.
    test_pass_result: float | None = None
    # Guard so the in-sandbox test run happens at most once per step_agent_stream
    # (it is expensive — a full DangerRoom copy + pytest).
    _tests_ran: bool = False

from runtime_v2.api._agent_config import (
    MAX_TURNS, MAX_DEPTH, ANALYSIS_AGENTS, INTERNET_GOAL_AGENTS,
    _DEFAULTS, MAX_HISTORY_TURNS, MAX_RESULT_CHARS,
)
from runtime_v2.api._agent_routing import (
    lookup_model, fast_route_coordinator, fast_start_for_agent,
    matches_task_keywords, best_route_target, _RESEARCHER_FIRST_TURNS,
    is_compound_goal,
)


class AgentServiceV2:

    def __init__(self, orchestrator: Any = None, cache: Any = None, settings: Any = None, event_store: Any = None) -> None:
        self.orchestrator = orchestrator
        self.event_store = event_store
        from swarm_os.agent_runtime import AgentRuntime
        from runtime_v2.services.learning.evolving_critic import EvolvingCritic
        self.agent_runtime = AgentRuntime()
        self.learning_critic = EvolvingCritic()
        self._background_tasks = set()
        self._agents: Dict[str, dict] = {
            k: {"id": k, "role": r, "description": d, "model_role": m, "config": {}}
            for k, (r, d, m) in _DEFAULTS.items()
        }

    def _record_event(self, event_type: str, source: str, payload: dict[str, Any]) -> None:
        store = getattr(self, "event_store", None) or getattr(self.orchestrator, "events", None)
        if store and hasattr(store, "append"):
            try:
                from swarm_os.events.envelope import EventEnvelope
                store.append(EventEnvelope.create(event_type=event_type, source=source, payload=payload))
            except Exception as e:
                log.warning("Failed to record event to EventStore: %s", e)

    def list_agents(self) -> List[dict]: return list(self._agents.values())
    def get_agent(self, agent_id: str) -> dict:
        if agent_id not in self._agents: raise KeyError(f"Unknown agent: {agent_id}")
        return self._agents[agent_id]
    def register_agent(self, agent_id: str, config: Optional[dict] = None) -> None:
        c = config or {}
        self._agents[agent_id] = {"id": agent_id, "role": c.get("role", "generalist"),
            "description": c.get("description", ""), "model_role": c.get("model_role", "fast"), "config": c}
    def remove_agent(self, agent_id: str) -> None: self._agents.pop(agent_id, None)

    async def run_tool(self, agent_id: str, tool_name: str, payload: dict) -> dict:
        from runtime_v2.services.tool_executor import run
        return await run(tool_name, payload)

    # -----------------------------------------------------------------------
    # Helpers: record performance metrics & memory after completion
    # -----------------------------------------------------------------------
    def _record_success(self, model: str, start_time: float):
        if not start_time or not model:
            return
        if self.orchestrator and hasattr(self.orchestrator, "router"):
            try:
                self.orchestrator.router.record_success(model, (time.time() - start_time) * 1000)
            except Exception as e:
                log.debug("Failed to record router success: %s", e)
                pass
        self._record_event("generation_completed", "agent_service_v2", {
            "model": model, "duration_ms": (time.time() - start_time) * 1000,
            "status": "success", "learning_outcome": {"result": "success"}})

    def _fitness_gate_on(self) -> bool:
        try:
            from swarm_os.services.outcome_fitness import _fitness_env_enabled
            return _fitness_env_enabled()
        except Exception:
            return False

    def _find_related_tests(self, file_path: str) -> list:
        """Locate test files that exercise a changed module (by name, then content)."""
        try:
            from pathlib import Path
            repo = Path(__file__).resolve().parent.parent.parent.parent
            tests_dir = repo / "tests"
            if not tests_dir.exists():
                return []
            base = Path(file_path).stem
            mod = str(file_path).replace("\\", "/").replace(".py", "")
            out = []
            for t in sorted(tests_dir.glob("test_*.py")):
                if base in t.name or t.name.replace("test_", "").replace(".py", "") in base:
                    out.append(t)
                    if len(out) >= 5:
                        break
            if not out:
                for t in sorted(tests_dir.glob("test_*.py")):
                    try:
                        head = t.read_text(encoding="utf-8", errors="ignore")[:4000]
                    except Exception:
                        continue
                    if mod in head or base in head:
                        out.append(t)
                        if len(out) >= 5:
                            break
            return out
        except Exception:
            return []

    def _structural_verify(self, file_path: str, repo=None) -> bool:
        """Minimal non-transcriptional verification of an edited Python file:
        it must exist, be non-trivial, and parse (ast.parse). This is the L3
        fallback when no related test suite exists — NOT a free pass. A file that
        fails this (doesn't exist / empty / syntax-broken) is scored 0.0, and a
        merely-untested-but-sounding file still only earns a DISCOUNTED 0.5 so it
        can never out-compete a genuinely verified (tested) 1.0. `repo` overrides
        the project root (test seam)."""
        try:
            from pathlib import Path
            if repo is None:
                repo = Path(__file__).resolve().parent.parent.parent.parent
            p = repo / (file_path or "")
            if not p.exists() or p.is_dir():
                return False
            src = p.read_text(encoding="utf-8", errors="ignore")
            if not src.strip():
                return False
            try:
                import ast as _ast
                _ast.parse(src)
            except SyntaxError:
                return False
            return True
        except Exception:
            return False

    async def _run_change_tests(self, state: _CallState, changed_file: str):
        """L3 (2026): run the changed module's related tests in a DangerRoom
        sandbox and record the REAL test_pass.

        - Poll tests exist: run them in-sandbox -> 1.0 (exit 0) / 0.0 (fail).
        - No related test found: NOT an automatic pass (that would be the
          Self-Repair Trap). Structurally verify the edited file via
          `_structural_verify` (ast.parse + non-empty). Raw score:
              broken/unparseable           -> 0.0
              sound but UNVERIFIED by test -> 0.5 (discounted — cannot out-compete
                                              a verified 1.0 in selection)
        Never raises; an error records None (treated as unverified downstream)."""
        try:
            tests = self._find_related_tests(changed_file or "")
            if not tests:
                if self._structural_verify(changed_file):
                    state.test_pass_result = 0.5  # untested but sound -> discounted
                    log.info("[coder] L3: no related tests for %s; unverified change scored 0.5 (discounted)", changed_file)
                else:
                    state.test_pass_result = 0.0  # broken / unparseable -> fail
                    log.warning("[coder] L3: no tests AND structural verify FAILED for %s -> 0.0", changed_file)
                return
            from pathlib import Path
            from swarm_os.services.danger_room import DangerRoom
            root = Path(__file__).resolve().parent.parent.parent.parent
            async with DangerRoom(root) as dr:
                targets = [dr.sandbox_dir / "tests" / t.name for t in tests]
                targets = [t for t in targets if t.exists()]
                res = await dr.run_tests(targets, timeout=120.0)
                state.test_pass_result = 1.0 if res.get("ok") else 0.0
                log.info("[coder] L3 real-test signal: ok=%s exit=%s targets=%d",
                         res.get("ok"), res.get("exit_code"), len(targets))
        except Exception as exc:
            state.test_pass_result = None
            log.debug("[coder] L3 test run skipped: %s", exc)

    # --- Durable checkpoint helpers (2026 autonomy move 3) ---
    # The critical _CallState fields that L1-L6 correctness depends on. These MUST
    # round-trip through a checkpoint; dropping any one on resume would silently
    # reintroduce the bug that pattern was built to close (e.g. reset read_paths
    # -> L1 falsely rejects an already-grounded final; reset _tests_ran ->
    # re-runs the expensive sandbox test and double-feeds the outcome).
    _CHECKPOINT_STATE_FIELDS = (
        "read_paths", "test_pass_result", "_tests_ran",
        "_contract_finals", "premature_finals", "reviewer_fails",
        "did_code_change", "pending_verify", "_verify_final_rejected",
        "did_web_search", "did_web_fetch", "_web_final_rejected",
        "last_search_urls", "_web_fetch_injected",
        "_executor_research_delegated", "_executor_impl_delegated",
        "todos", "todo_id",
        "_tool_attempts", "_tool_successes", "_turn",
        "genome_id",
    )

    def _state_to_dict(self, state: _CallState) -> dict:
        out = {}
        for f in self._CHECKPOINT_STATE_FIELDS:
            v = getattr(state, f, None)
            if isinstance(v, set):
                v = sorted(v)
            out[f] = v
        return out

    def _state_from_dict(self, state: _CallState, d: dict) -> _CallState:
        if not isinstance(d, dict):
            return state
        for f in self._CHECKPOINT_STATE_FIELDS:
            if f not in d:
                continue
            v = d[f]
            if f == "read_paths" and isinstance(v, list):
                v = set(v)
            try:
                setattr(state, f, v)
            except Exception:
                pass
        return state

    def _feed_outcome(self, agent_id: str, prompt: str, state: _CallState,
                      completed: bool = True, tool_success_rate: float = 0.0,
                      turns_used: int = 0, genome_id: str = ""):
        """Feed a REAL agent outcome to the outcome-fitness store so the
        evolutionary kernel can select on grounded fitness instead of LLM noise.
        Gated by SWARM_EVOLUTION=1 (zero overhead otherwise). Deterministic
        signals only — never self-judged by the proposing model."""
        try:
            from swarm_os.services.outcome_fitness import _fitness_env_enabled
            if not _fitness_env_enabled():
                return
            # Efficiency = baseline(8 turns) / actual, clipped to [0,1].
            efficiency = min(1.0, 8.0 / max(1, turns_used or 8)) if completed else 0.0
            # L3 (2026 real-test signal): if a real in-sandbox test run happened
            # (coder code change), use its true outcome — NOT the completion proxy.
            # A broken fix that merely completes without erroring is now scored as
            # a test FAIL, not a pass. Falls back to the completion proxy only when
            # no real test result exists.
            if state.test_pass_result is not None:
                test_pass = state.test_pass_result
            else:
                test_pass = 1.0 if completed else 0.0
            from swarm_os.services.outcome_fitness import record_outcome
            record_outcome(
                genome_id=genome_id or f"agent:{agent_id}",
                completion=1.0 if completed else 0.0,
                test_pass=test_pass,
                tool_success=tool_success_rate,
                efficiency=efficiency,
                task=str(prompt)[:200],
                agent_id=agent_id,
            )
        except Exception as exc:
            log.debug("[%s] outcome feed skipped: %s", agent_id, exc)

    async def _remember(self, text: str, category: str = "general"):
        try:
            from runtime_v2.services.memory_core import remember_fact
            await asyncio.to_thread(remember_fact, text, category=category)
        except Exception as e:
            log.debug("Failed to remember fact: %s", e)
            pass

    async def _remember_failure(self, agent_id: str, action: str, tool_payload: dict,
                                error: str, category: str = "self_reflection"):
        """Persist a tool-execution failure BOTH as episodic memory AND as a
        structured ReflexionMemory rule so check_for_past_mistakes() can steer a
        future run with a [PAST-MISTAKE WARNING] (was: only episodic memory, so
        the lesson never reached the decision loop)."""
        try:
            await self._remember(
                f"Agent {agent_id} called {action} which failed with {error}. Strategy needs adjustment.",
                category=category)
        except Exception as e:
            log.debug("Failed to remember fact: %s", e)
            pass
        try:
            # Dedup guard: skip if this exact agent+action+error was already
            # recorded within the last 5 minutes — repeated identical failures
            # would otherwise spam the reflexion store with duplicate points.
            key = (agent_id, action, str(error)[:200])
            now = time.time()
            if key in _failure_lessons_seen:
                if now - _failure_lessons_seen[key] < 300:
                    return
            _failure_lessons_seen[key] = now
            # Prune stale dedup entries so the dict can't grow unboundedly.
            if len(_failure_lessons_seen) > 200:
                stale = [k for k, ts in _failure_lessons_seen.items() if now - ts >= 300]
                for k in stale:
                    _failure_lessons_seen.pop(k, None)

            from swarm_os.services.reflection_loop import get_reflection_service
            correction, do_not = self._failure_lesson(action, tool_payload, error)
            # Task text is embedded and later matched against
            # `agent:{agent_id} {user_message}` queries — lead with the agent +
            # a generalizable "analyzing/auditing the codebase" trigger so the
            # lesson surfaces on future codebase-analysis runs, then the concrete
            # error for precision.
            task_hint = (
                f"agent:{agent_id} analyzing auditing codebase {action} failed "
                f"{str(error)[:120]} — check filesystem paths before reading"
            )
            await get_reflection_service().store_reflexion(
                task=task_hint,
                action=action,
                failure_reason=str(error)[:300],
                correction=correction,
                do_not_repeat=do_not,
                component=agent_id,
                confidence=0.75,
            )
            # Also write the failure to the organism diary so run_reflection()'s
            # LLM distiller sees REAL agent failures (it previously only read the
            # genetic-kernel diary, whose entries carry no component — producing the
            # 137 component:"unknown" noise rules that swamped ReflexionMemory).
            # The entry carries `component` so get_latest_failure() prefers it.
            try:
                from swarm_os.services.reflection_loop import DIARY_PATH
                diary_path = DIARY_PATH
                diary_path.parent.mkdir(parents=True, exist_ok=True)
                import time as _t
                record = {
                    "ts": _t.time(),
                    "event": "tool_failure",
                    "component": agent_id,
                    "agent": agent_id,
                    "task": str(task_hint)[:300],
                    "content_preview": str(tool_payload)[:200],
                    "error": str(error)[:300],
                    "action": action,
                }
                with open(diary_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception as diary_err:
                log.debug("[%s] diary write skipped: %s", agent_id, diary_err)
        except Exception as exc:
            log.debug("[%s] reflexion store skipped: %s", agent_id, exc)

    @staticmethod
    def _failure_lesson(action: str, payload: dict, error: str) -> tuple[str, str]:
        """Build a targeted, agent-actionable correction + do-not-repeat for a
        failed tool call (grounded, not an LLM guess)."""
        if action == "filesystem" and "File not found" in error:
            path = payload.get("path", "")
            parent = "/".join(str(path).split("/")[:-1]) if path else ""
            return (
                f"Path '{path}' does not exist. Use filesystem list on '{parent or '.'}' "
                "first to discover real file paths before reading — the module map in "
                "AGENTS.md and the directory listing are the ground truth.",
                f"Do NOT guess file paths; always list the parent directory first (path '{path}' does not exist).",
            )
        if action == "web_search" and ("timed out" in error.lower() or "timeout" in error.lower()):
            return (
                "Web search timed out. Retry with a shorter, more specific query or "
                "proceed with what is known from the codebase.",
                "Do NOT spam repeated web_search calls with the same query.",
            )
        return (
            f"Tool '{action}' failed ({str(error)[:200]}). Check the tool contract in "
            "_TOOL_DEFINITIONS and verify parameters before retrying.",
            f"Do NOT repeat the exact same failed '{action}' call.",
        )

    # -----------------------------------------------------------------------
    # Todo tracking (multi-step task checklist)
    # -----------------------------------------------------------------------
    def _handle_todo(self, decision: dict, agent_id: str, state: _CallState) -> dict:
        """Add/complete/list todos. Returned dict becomes the tool_result the LLM
        sees next turn, so it can keep a working checklist across turns."""
        op = str(decision.get("operation", "list")).lower()
        items = decision.get("items") or []
        if isinstance(items, str):
            items = [items]

        if op == "add":
            for it in items:
                state.todo_id += 1
                state.todos.append({"id": state.todo_id, "text": str(it), "done": False})
            return {"ok": True, "result": f"Added {len(items)} todo(s). Current todos: {self._todos_preview(state)}"}
        if op == "done":
            target = decision.get("item_id") or decision.get("item")
            found = False
            for t in state.todos:
                if (target is not None and str(t["id"]) == str(target)) or str(target) in t["text"]:
                    t["done"] = True
                    found = True
                    break
            if not found:
                return {"ok": False, "error": f"Todo '{target}' not found. Current todos: {self._todos_preview(state)}"}
            return {"ok": True, "result": f"Completed: {self._todos_preview(state)}"}
        return {"ok": True, "result": self._todos_preview(state)}

    @staticmethod
    def _todos_preview(state: _CallState) -> str:
        if not state.todos:
            return "(empty)"
        return "; ".join(
            ("[x] " if t["done"] else "[ ] ") + f"{t['id']}. {t['text']}" for t in state.todos
        )

    # -----------------------------------------------------------------------
    # Decision fetching: fast-route, warmup, or LLM call
    # -----------------------------------------------------------------------
    async def _get_decision(self, agent_id: str, model: str, trimmed_messages: list,
                            allowed_tools: list, prompt: str, turn: int,
                            state: "_CallState" = None,
                            research_discharged: bool = False) -> Optional[dict]:
        if state is None:
            state = _CallState()
        if agent_id == "coordinator" and turn == 0:
            fast = fast_route_coordinator(prompt)
            if fast is not None:
                return fast
            # POST-ASK_USER GUARD: a coordinator re-invoked with the user's answer
            # already in history (an `Observation` from a prior ask_user) must NOT
            # re-ask the same question. The caller (CLI live_stream) drives the
            # loop by re-calling with the answer appended — the coordinator is
            # stateless, so without this it just re-asks forever (the /upgrade
            # "which upgrades?" infinite loop). Route deterministically now that
            # the user has answered.
            answer = _answer_from_history(trimmed_messages)
            if answer is not None:
                goal = _original_goal(trimmed_messages) or prompt
                goal_target = best_route_target(goal)
                answer_target = best_route_target(answer)
                # Honor a specific answer route (non-default); otherwise fall back
                # to the goal's own best route (e.g. "upgrade everything" → coder).
                target = answer_target if answer_target != "planner" else goal_target
                log.info("[coordinator] ask_user answered '%s' → delegating to %s (goal='%s')",
                         answer, target, goal[:80])
                return {"action": "delegate", "target_agent": target, "task": goal}
            decision = await self._call_llm(model, trimmed_messages, agent_id, allowed_tools)
            # COMPOUND-GOAL GUARD: a goal needing BOTH internet research AND code
            # changes must go to `executor` (the orchestrator that chains
            # researcher → coder → tool-runner), NOT collapse onto coder — which
            # then has to satisfy the edit + web_search + web_fetch obligations
            # inside MAX_TURNS and loop-trips (the /upgrade dead-loop).
            if decision and decision.get("action") == "delegate":
                if is_compound_goal(prompt) and decision.get("target_agent") not in ("executor", "coder", "debugger"):
                    log.info("[coordinator] compound goal → forcing delegate target executor (was %s)",
                             decision.get("target_agent"))
                    return {"action": "delegate", "target_agent": "executor",
                            "task": decision.get("task") or prompt}
                # FIX-INTENT GUARD: if the goal implies editing/fixing code, force the
                # delegate target to `coder` (edit-capable) even when the LLM coordinator
                # picks the report-only code_analyzer. A compound "analyze + fix bugs"
                # goal must actually FIX (like a human maintainer / opencode), not just
                # return an analysis report.
                if _is_fix_intent(prompt) and decision.get("target_agent") not in ("coder", "debugger"):
                    log.info("[coordinator] fix-intent goal → forcing delegate target coder (was %s)",
                             decision.get("target_agent"))
                    return {"action": "delegate", "target_agent": "coder", "task": decision.get("task") or prompt}
            # HARD GUARD (not just a prompt rule): a coordinator may NEVER answer a
            # real task with action=final. Stale episodic memory ("this task was
            # already completed") routinely fools small local models into
            # short-circuiting, and the prompt rule 4 is not reliably followed.
            # If the goal contains action keywords, force a delegate to the
            # highest-priority route instead.
            if decision and decision.get("action") == "final" and matches_task_keywords(prompt):
                target = best_route_target(prompt)
                log.warning("[coordinator] Blocked short-circuit final on task goal → delegating to %s", target)
                return {"action": "delegate", "target_agent": target, "task": prompt}
            return decision

        fast = fast_start_for_agent(agent_id, turn)
        
        # INTERNET-GOAL FIX: if an analysis agent was handed an internet goal
        # (codebase + web, or pure web), inject web_search BEFORE the
        # filesystem warmup. The warmup consumed all 8 turns with file reads
        # (4 warmup + repeated reads) and the agent hit "max turns reached"
        # without ever calling web_search — the guard rejected the final but
        # there was no budget left to search. Searching first guarantees the
        # internet portion of the goal is actually done.
        # NOTE: applied to REPORT agents (ANALYSIS_AGENTS) only — NOT coder.
        # coder is an EDIT agent; forcing web_search at turn 0 derails it into
        # pure research and it never edits (the repeated /upgrade dead-loop).
        # coder reads/edits first and uses web_search at its own discretion
        # (+ is still bound by the internet-goal web_fetch final guard).
        if turn == 0 and agent_id in ANALYSIS_AGENTS and not research_discharged:
            query = (prompt or "").strip()
            internet_goal = bool(_INTERNET_GOAL_RE.search(query)) if query else False
            if internet_goal:
                log.info("[%s] internet goal — fast-start turn %d → web_search (before warmup)", agent_id, turn)
                return {"action": "web_search", "query": _clean_search_query(query)}

        # EXECUTOR COMPOUND-GOAL CHAINING: executor is the orchestrator that splits a
        # compound internet+fix goal across the team (researcher → coder → tool-runner).
        # Its FIRST action must be research-first — deterministically delegate to
        # researcher so the code-changes downstream are grounded in real findings,
        # instead of coder researching itself and loop-tripping the /upgrade dead-loop.
        # COMPOUND-GOAL DECOMPOSITION (2026-08-06): the delegated researcher task is
        # ONLY the research phase — NOT the full "analyze + implement" goal. Handing
        # the whole compound goal to researcher made it try all three jobs inside
        # MAX_TURNS=8 and burn out on the search phase (turn_budget_exhausted). The
        # implementation phase stays with executor, which delegates it to coder after
        # research returns.
        if turn == 0 and agent_id == "executor":
            query = (prompt or "").strip()
            if query and bool(_INTERNET_GOAL_RE.search(query)):
                research_part, _impl_part = _split_compound_goal(query)
                # RESEARCHER IS WEB-ONLY: strip the codebase-analysis half so
                # researcher does NOT browse the filesystem (observed: it read 5
                # files + web_search, then turn_budget_exhausted on a compound
                # "analyze codebase + search internet" goal). The codebase
                # analysis is delegated to code_analyzer in a later step.
                researcher_task = _research_only_task(research_part or query)
                log.info("[executor] compound goal — fast-start turn %d → delegate researcher (web-research only)", turn)
                return {"action": "delegate", "target_agent": "researcher",
                        "task": researcher_task}

        # EXECUTOR PHASE-2 CHAINING: once the research delegation has returned,
        # deterministically hand the IMPLEMENTATION phase to coder — the executor
        # LLM has been observed re-looping (re-delegating/asking) instead of
        # delegating the code work. The goal was split at turn 0, so coder gets
        # only the implement portion, scoped to its own MAX_TURNS budget.
        # GUARD: if the goal had NO implementation-intent sentence (research-only
        # compound like "analyze + search internet"), impl_part is empty — there
        # is no edit phase to delegate. Handing coder the full vague goal made it
        # explore for MAX_TURNS without editing (turn_budget_exhausted). In that
        # case the research IS the deliverable; let the LLM executor finalize
        # rather than force an edit task that doesn't exist.
        if agent_id == "executor" and state._executor_research_delegated and not state._executor_impl_delegated:
            query = (prompt or "").strip()
            _r_part, impl_part = _split_compound_goal(query)
            if impl_part:
                state._executor_impl_delegated = True
                log.info("[executor] research returned — delegating implementation phase → coder")
                return {"action": "delegate", "target_agent": "coder",
                        "task": impl_part}

        if fast is not None:
            return fast
        # Research-only goals: the FIRST action is deterministically web_search,
        # never filesystem. Without this, the LLM's codebase bias reads files /
        # memory first and either never searches or burns the turn budget before
        # web_search. The user's goal is the query; the LLM takes over next turn.
        if agent_id == "researcher" and turn < _RESEARCHER_FIRST_TURNS:
            query = (prompt or "").strip()
            if query:
                log.info("[researcher] fast-start turn %d → web_search", turn)
                return {"action": "web_search", "query": _clean_search_query(query)}

        # INTERNET-GOAL WEB_FETCH INJECTION (deterministic, not prompt-only):
        # once web_search has actually succeeded on an internet goal, the NEXT
        # decision is forced to deep-read the top result. The model — even the
        # cloud deepseek analysis hop — repeatedly re-selects web_search
        # (observed live: 3× web_search, each final rejected by the web_fetch
        # guard, then the loop detector tripped). Relying on the LLM to pick
        # web_fetch is unreliable, so mirror the turn-0 web_search injection.
        if (
            state.did_web_search
            and not state.did_web_fetch
            and not state._web_fetch_injected
            and state.last_search_urls
            and not research_discharged
        ):
            state._web_fetch_injected = True
            url = state.last_search_urls[0]
            log.info("[%s] internet goal — web_search done, injecting web_fetch → %s", agent_id, url)
            return {"action": "web_fetch", "url": url}

        return await self._call_llm(model, trimmed_messages, agent_id, allowed_tools)

    async def _call_llm(self, model: str, messages: list, agent_id: str, allowed_tools: list) -> Optional[dict]:
        from runtime_v2.services.stream_runner import get_tool_decision
        try:
            # UPGRADE: asyncio.timeout() — composable context manager.
            # 300s must be >= _STEP_TIMEOUT (180s) so the step-level budget in
            # stream_runner is the binding constraint, not this outer wrapper.
            async with asyncio.timeout(300.0):
                decision = await get_tool_decision(model, messages, agent_id, allowed_tools=allowed_tools)
            # CIRCUIT-BREAKER BYPASS FIX: get_tool_decision swallows LLM failures
            # internally and returns a fallback dict {"action": "final",
            # "ok": False, "system_failure": ...} instead of raising. If we pass
            # that dict up, the loop's `except` around _get_decision NEVER fires
            # and consecutive_errors never increments — the breaker (which hands
            # off to the debugger to heal a down backend after 3 failures) is
            # bypassed, and every run just ends with a failed final. Raise here so
            # the decision failure travels the same exception path as a real LLM
            # error: counted, retried, and after 3 the debugger heals the backend.
            if isinstance(decision, dict) and decision.get("ok") is False:
                reason = decision.get("system_failure") or "decision_failed"
                raise RuntimeError(f"LLM decision failed ({reason}): {str(decision.get('response', ''))[:200]}")
            return decision
        except asyncio.TimeoutError:
            log.warning("[%s] LLM timeout >300s", agent_id)
            raise
        except Exception as exc:
            log.warning("[%s] LLM error: %s", agent_id, exc)
            raise

    # -----------------------------------------------------------------------
    # Loop guard: detect repeated/cyclical actions
    # -----------------------------------------------------------------------
    def _check_loop(self, decision: dict, decision_counts: dict, history_actions: list) -> Optional[str]:
        _essential_keys = ("action", "operation", "target_agent", "path", "query",
                           "language", "code", "server_name", "tool", "question",
                           "item_id", "item", "name", "args", "items")
        _dup_sig = json.dumps({k: v for k, v in decision.items() if k in _essential_keys and v}, sort_keys=True)
        decision_counts[_dup_sig] = decision_counts.get(_dup_sig, 0) + 1
        history_actions.append(_dup_sig)

        recent_window = history_actions[-8:]
        repeated_in_window = recent_window.count(_dup_sig) >= 3
        is_sequence_loop = False
        for cycle_len in range(2, min(8, len(history_actions) // 2) + 1):
            if history_actions[-cycle_len:] == history_actions[-2*cycle_len:-cycle_len]:
                is_sequence_loop = True
                break

        if decision_counts[_dup_sig] >= 3 or repeated_in_window or is_sequence_loop:
            return _dup_sig
        if decision_counts[_dup_sig] >= 2:
            return "WARN"  # duplicate, not loop
        return None

    # -----------------------------------------------------------------------
    # Handler: final
    # -----------------------------------------------------------------------
    async def _handle_final(self, decision: dict, agent_id: str, model: str, provider: str,
                            messages: list, start_time: float, prompt: str,
                            _fetched_content: bool, state: _CallState,
                            research_discharged: bool = False):
        # System-failure finals (LLM unreachable / empty / malformed / decision
        # loop exhausted): these are NOT real completions. Feed a failed outcome,
        # record a tool_result-style failure event, and exit as a failure so the
        # evolutionary kernel and telemetry see the truth instead of a perfect
        # completion (the run never produced a single real decision).
        if decision.get("ok") is False:
            failure_reason = decision.get("system_failure") or "llm_failure"
            response_text = str(decision.get("response", "[SYSTEM: decision loop failed]"))
            log.warning("[%s] System-failure final (%s): %s", agent_id, failure_reason, response_text[:120])
            yield {"agent_id": agent_id, "type": "error", "content": response_text}
            yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": response_text}
            try:
                self._record_event(agent_id, "tool_result", {
                    "tool": "llm_decision", "arguments": {"system_failure": failure_reason},
                    "ok": False, "error": response_text[:300],
                })
            except Exception as _e:
                log.debug("Failed to record system-failure event: %s", _e)
            tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
            self._feed_outcome(agent_id, prompt, state, completed=False,
                               tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
            state.handler_status = "DONE"
            return

        # Verify-after-change: reject a final call when a code file was edited
        # but never tested, forcing the agent to run sandbox_repl before reporting
        # done. Rejects on EVERY final while pending_verify stays set (the
        # MAX_TURNS loop bounds the rejection); only a SUCCESSFUL sandbox_repl
        # clears pending_verify (below). A one-shot latch (reject once, then let
        # a second final through) let the agent skip testing after a single nudge.
        if state.pending_verify:
            state._verify_final_rejected = True
            state.handler_status = "CONTINUE"
            log.warning("[%s] Rejected final: pending code verification.", agent_id)
            messages.append({"role": "user", "content": (
                "SYSTEM: You edited a code file but have not verified it runs. "
                "Use action=sandbox_repl (language=pytest or language=python) to test the "
                "change before calling action=final. Do NOT report success without a test run."
            )})
            return

        # Editing-agent invariant: the `coder` is an EDIT agent. If it was handed a
        # fix-intent goal (write/fix/implement/patch/modify/solve/repair/...) but
        # finalizes WITHOUT modifying any file, it did not do the task — it ran a
        # few web_searches and restated the goal. This reproduced as the /upgrade
        # autonomous loop failing 5/5 attempts with "No file changes detected".
        # Reject the final on EVERY such attempt until a file is written or patched.
        # (Complementary to pending_verify above: that fires only AFTER an edit; this
        # fires when NO edit has happened yet.)
        if (
            agent_id == "coder"
            and _is_fix_intent(prompt)
            and not state.did_code_change
        ):
            state.handler_status = "CONTINUE"
            log.warning("[coder] Rejected final: fix-intent goal with no code change.")
            messages.append({"role": "user", "content": (
                "SYSTEM: Your task requires EDITING CODE (the goal uses "
                "write/fix/implement/patch/modify/solve/repair), but you called "
                "action=final without modifying any file. Use action=filesystem with "
                "operation=write (new file) or operation=patch (modify an existing file; "
                "read it first) to make the actual code change, then run sandbox_repl "
                "to verify it. Do NOT finalize until you have written or patched a file."
            )})
            return

        # Reject premature final from analysis agents with no content fetched
        if agent_id in ANALYSIS_AGENTS and not _fetched_content:
            state.premature_finals += 1
            if state.premature_finals >= 2:
                log.error("[%s] Aborting: agent called final twice without reading any files.", agent_id)
                error_msg = f"Task FAILED: {agent_id} called final without reading any files to analyze."
                yield {"agent_id": agent_id, "type": "error", "content": error_msg}
                yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": error_msg}
                state.handler_status = "ABORT"
                return
            log.warning("[%s] Rejected premature final (no files read).", agent_id)
            allowed = self._get_allowed_tools(agent_id)
            messages.append({"role": "user", "content": (
                "SYSTEM: You called action=final without reading any files first. "
                "You MUST read at least one file using filesystem (operation=read) or "
                "semantic_search/web_search before you can produce a meaningful analysis. "
                "Allowed actions: {0}. Example: {{"
                "\"action\":\"filesystem\",\"operation\":\"read\","
                "\"path\":\"runtime_v2/agent_service_v2.py\"}}").format(allowed)})
            state.handler_status = "CONTINUE"
            return

        # Reject final from internet-involving goals if the agent never ran web_search
        # Internet-goal guard: only applies to agents that actually HAVE the
        # web_search tool (reviewer does not — its tool list is read/search/repl
        # only, so demanding web_search would block a legit final). Keywords are
        # internet-INTENT phrases only; bare "modern"/"latest" are dropped (they
        # frequently appear in purely-local goals and caused false positives).
        query = (prompt or "").strip()
        internet_goal = bool(_INTERNET_GOAL_RE.search(query)) if query else False
        has_web_search_tool = "web_search" in self._get_allowed_tools(agent_id)
        # FIX-DELIVERABLE RELAXATION: when `coder` has ACTUALLY edited a file on a
        # fix-intent goal, the edit IS the deliverable — the web research was either
        # discharged upstream (executor chain: researcher → coder) or is a *means*
        # to the fix, not an end. Requiring web_search AND web_fetch on top of a
        # completed edit is the double-bind that loop-tripped the /upgrade cycle
        # ("coder caught in a loop" after editing, then the fix-intent + internet
        # obligations collided inside MAX_TURNS). The internet guard still fully
        # applies to REPORT agents (code_analyzer/researcher) and to coder BEFORE it
        # has edited (i.e. it cannot skip research and final with no code change).
        fix_deliverable = (
            agent_id == "coder"
            and _is_fix_intent(prompt)
            and state.did_code_change
        )
        # REQUIRE both web_search AND web_fetch for internet goals on analysis
        # agents. Searching is not enough — the agent must deep-read at least one
        # authoritative page (docs/SO/GitHub) so the final answer is grounded in
        # actual fetched content, not just search snippets. This matches how a
        # human researcher (or opencode) works: search → fetch → read → synthesize.
        # RESEARCH-DISCHARGED RELAXATION: when the executor chain ALREADY ran the
        # research phase (researcher did web_search + web_fetch before delegating
        # implementation to coder / analysis to code_analyzer), the downstream agent
        # must NOT be re-forced to re-search the same handful of URLs. The guard is
        # scoped per-agent-role-in-chain, not per-goal-text (the goal text still
        # contains "internet/upgrades" — re-checking it against the downstream agent
        # wastes turns and cloud calls on research that was already done once).
        needs_fetch = "web_fetch" in self._get_allowed_tools(agent_id)
        if internet_goal and not state.did_web_search and has_web_search_tool and agent_id in INTERNET_GOAL_AGENTS and not fix_deliverable and not research_discharged:
            # Reject on EVERY final until web_search actually runs — not just the
            # first (a one-shot latch let the agent call final a second time and
            # "complete" the goal without ever doing the internet research it was
            # explicitly asked for).
            state._web_final_rejected = True
            state.handler_status = "CONTINUE"
            log.warning("[%s] Rejected final: internet goal without web_search.", agent_id)
            messages.append({"role": "user", "content": (
                "SYSTEM: The goal explicitly asks you to search the internet, but you called "
                "action=final without running action=web_search. You MUST run web_search with "
                "concrete queries about the technologies/issues you found, then web_fetch at "
                "least one result, before you can call action=final. Do NOT skip this step."
            )})
            return
        if internet_goal and needs_fetch and not state.did_web_fetch and agent_id in INTERNET_GOAL_AGENTS and not fix_deliverable and not research_discharged:
            state._web_final_rejected = True
            state.handler_status = "CONTINUE"
            log.warning("[%s] Rejected final: internet goal without web_fetch.", agent_id)
            messages.append({"role": "user", "content": (
                "SYSTEM: You searched the web, but you called action=final without deep-reading "
                "any result. You MUST use action=web_fetch to read at least one authoritative "
                "page (official docs, Stack Overflow, GitHub) about the topic, extract the "
                "relevant details, and THEN implement the necessary changes or synthesize your final answer from that content. "
                "Do NOT finalize from search snippets alone."
            )})
            return

        response_text = str(decision.get("response", "Task complete."))

        # 2026 L1 structural verifier: fail closed on a final that violates the
        # task contract, independent of whether tests exist. Two checks:
        #   (a) placeholder/template finals — a bare "Task completed." / "Done."
        #       is a short-circuit signal and is rejected as a failed run (same
        #       treatment as a system-failure final: NOT fed as a success).
        #   (b) analysis agents that reference specific .py file paths in their
        #       final which were NEVER actually read this run (only listed/globbed
        #       or mentioned) — the final would be claiming analysis of files whose
        #       content the agent never saw.
        # These run on EVERY final, fail closed, and only for report/analysis
        # agents (coder's fix-intent invariant already forces a code edit).
        contract_error = None
        if agent_id in ANALYSIS_AGENTS:
            if _is_placeholder_final(response_text):
                contract_error = (
                    "SYSTEM (L1 contract): you called action=final with only a placeholder response "
                    f"({response_text!r}). This is a short-circuit, not a completed task. Re-run the goal "
                    "and produce a substantive final that states the actual findings/fixes. Do NOT finalize "
                    "with a bare completion sentence."
                )
            else:
                refs = set(re.findall(r"[\w./\\-]+\.py", response_text))
                read_paths = {r.replace("\\", "/").lstrip("./") for r in state.read_paths}
                read_basenames = {p.rsplit("/", 1)[-1] for p in read_paths}
                unread = {
                    p for p in refs
                    if p.replace("\\", "/").lstrip("./") not in read_paths
                    and p.replace("\\", "/").rsplit("/", 1)[-1] not in read_basenames
                }
                if unread:
                    contract_error = (
                        "SYSTEM (L1 contract): your final references file paths that were never actually "
                        f"read this run: {sorted(unread)[:5]}. Merely listing or mentioning a file is not "
                        "reading its content. Use action=filesystem (operation=read) on the files you are "
                        "reporting on, then produce a grounded final."
                    )
        if contract_error:
            state._contract_finals += 1
            if state._contract_finals >= 2:
                log.error("[%s] Aborting: final rejected twice for contract violations.", agent_id)
                err_txt = f"Task FAILED: {agent_id} could not produce a substantive, grounded final."
                yield {"agent_id": agent_id, "type": "error", "content": err_txt}
                yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": err_txt}
                tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
                self._feed_outcome(agent_id, prompt, state, completed=False,
                                   tool_success_rate=tsr, turns_used=state._turn,
                                   genome_id=getattr(state, 'genome_id', ''))
                state.handler_status = "ABORT"
                return
            state.handler_status = "CONTINUE"
            log.warning("[%s] L1 contract violation: %s", agent_id, contract_error[:160])
            messages.append({"role": "user", "content": contract_error})
            return

        if agent_id == "reviewer" and ("FAIL" in response_text.upper() or str(decision.get("verdict", "")).upper() == "FAIL"):
            state.reviewer_fails += 1
            if state.reviewer_fails >= 3:
                yield {"agent_id": agent_id, "content": response_text, "model": model}
                yield {"agent_id": agent_id, "type": "error", "content": "Reviewer failed too many times. Aborting delegation loop."}
                state.handler_status = "ABORT"
                return

        yield {"agent_id": agent_id, "content": response_text, "model": model}
        yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": response_text}
        self._record_success(model, start_time)
        tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 1.0
        self._feed_outcome(agent_id, prompt, state, completed=True,
                           tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
        await self._remember(f"[{agent_id}] task completed: {str(prompt)[:80]} -> {response_text[:120]}", category="general")
        state.handler_status = "DONE"

    # -----------------------------------------------------------------------
    # Handler: delegate
    # -----------------------------------------------------------------------
    async def _handle_delegate(self, decision: dict, agent_id: str, chain: list,
                               model: str, provider: str, messages: list,
                               prompt: str, start_time: float, state: _CallState,
                               research_discharged: bool = False):
        target = decision.get("target_agent", "executor")
        task = decision.get("task", prompt)

        if target == agent_id:
            if agent_id == "planner":
                target = "executor"
            else:
                state.handler_status = "SELF_DELEGATION_ERROR"
                return

        if target in chain:
            for m in reversed(messages):
                if m.get("role") == "user" and "TOOL RESULT (delegate)" in str(m.get("content", "")):
                    content = str(m.get("content", ""))
                    if f"{target} responded:" in content:
                        match = re.search(rf"{target} responded: (.*?)(?:\n\n|$)", content, re.DOTALL)
                        if match:
                            yield {"agent_id": agent_id, "type": "tool_result", "tool": "delegate",
                                   "result": {"ok": True, "result": f"[Recovered] Using previous result from {target}: {match.group(1).strip()}"}}
                            yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider,
                                   "content": f"[Recovered from circular delegation] {match.group(1).strip()}"}
                            self._record_success(model, start_time)
                            state.handler_status = "RECOVERED"
                            return
            state.handler_status = "CIRCULAR_ERROR"
            return

        if target not in self._agents:
            state.handler_status = "AGENT_NOT_FOUND"
            return

        yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": target, "task": task}

        child_final_response = f"Task completed by {target}."
        child_history = []
        for m in messages[-6:]:
            if m.get("role") == "system": continue
            content_str = str(m.get("content", ""))
            if m.get("role") == "assistant" and "delegate" in content_str: continue
            if m.get("role") == "user" and "TOOL RESULT (delegate)" in content_str: continue
            # RESEARCHER IS WEB-ONLY: when the executor delegates the web-research
            # phase to researcher, do NOT pass the original compound goal in
            # history — the model sees "analyze my codebase" in context and
            # browses the filesystem on top of web research, exhausting MAX_TURNS
            # (the observed turn_budget_exhausted). The web-only task is already
            # the researcher's prompt; the history should not re-introduce the
            # codebase-analysis half.
            if agent_id == "executor" and target == "researcher":
                continue
            child_history.append(m)

        if agent_id == "executor" and target == "researcher":
            state._executor_research_delegated = True

        # RESEARCH-DISCHARGED PROPAGATION: when the executor hands the IMPLEMENTATION
        # phase to coder AFTER the researcher already did web_search + web_fetch, pass
        # research_discharged=True so coder's final is not re-forced through the
        # internet-goal guards (research was done upstream — re-searching the same
        # handful of URLs burns turns and cloud calls for nothing).
        child_research_discharged = research_discharged
        if agent_id == "executor" and target == "coder" and state._executor_research_delegated:
            child_research_discharged = True

        async for chunk in self.step_agent_stream(target, task, history=child_history, delegation_chain=chain + [target], research_discharged=child_research_discharged):
            chunk.setdefault("delegated_by", agent_id)
            if chunk.get("type") == "final":
                child_final_response = str(chunk.get("content", f"Task completed by {target}."))
                continue
            if chunk.get("type") == "error":
                child_final_response = f"Task FAILED with error: {chunk.get('content')}"
            yield chunk

        self._record_success(model, start_time)

        if agent_id == "coordinator":
            yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": child_final_response}
            state.handler_status = "COORDINATOR_DONE"
            return

        messages.append({"role": "assistant", "content": json.dumps({"action": "delegate", "target_agent": target, "task": task})})
        messages.append({"role": "user", "content": f"TOOL RESULT (delegate)\n{target} responded: {child_final_response}\n\nReview this result and decide the next step."})
        state.handler_status = "SUBROUTINE_OK"

    # -----------------------------------------------------------------------
    # Handler: tool execution
    # -----------------------------------------------------------------------
    async def _handle_tool(self, decision: dict, agent_id: str, messages: list,
                           _fetched_content: bool, turn: int, consecutive_errors: int,
                           state: _CallState) -> tuple[int, bool]:
        from runtime_v2.services.tool_executor import run as run_tool
        action = decision.get("action", "final").strip()
        tool_payload = {k: v for k, v in decision.items()
                        if k not in ["action", "response", "thought", "target_agent", "task", "verdict"]}

        state.tool_success = False
        state.tool_action = action
        state.tool_payload = tool_payload
        try:
            result = await run_tool(action, tool_payload)
        except Exception as exc:
            log.exception("[%s] Tool %s execution raised unhandled exception: %s", agent_id, action, exc)
            result = {"ok": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        log.info("[%s] %s ok=%s", agent_id, action, result.get("ok"))
        state.tool_success = result.get("ok", False)
        state.tool_result = result
        state._tool_attempts += 1
        if state.tool_success:
            state._tool_successes += 1

        if not state.tool_success:
            consecutive_errors += 1
            extracted_err = result.get('error') or result.get('stderr') or 'unknown error'
            await self._remember_failure(agent_id, action, tool_payload, str(extracted_err))
            # Persist the tool_result failure to the event store so downstream
            # consumers that tail events.jsonl (RepairWatchman, /autofix, the goal
            # loop's verification) actually SEE tool failures. Previously only
            # generation_completed/agent_action/stream_completed were recorded and
            # tool failures existed only in the SSE stream — the whole repair +
            # reflexion-from-events path was starved (events.jsonl had 0 tool_result
            # lines).
            try:
                self._record_event("tool_result", agent_id, {
                    "tool": action,
                    "arguments": tool_payload,
                    "result": {"ok": False, "error": str(extracted_err)[:500]},
                    "agent_id": agent_id,
                })
            except Exception as evt_err:
                log.debug("[%s] tool_result event skipped: %s", agent_id, evt_err)
        else:
            # BUG FIX: Decay rather than hard-reset so a failure→success→failure
            # oscillation still reaches the breaker. Previously a success zeroed the
            # counter, letting intermittent failures evade healing forever.
            consecutive_errors = max(0, consecutive_errors - 1)
            if not _fetched_content:
                if action == "filesystem" and tool_payload.get("operation") in ("read", "read_all"):
                    _fetched_content = True
                    p = str(tool_payload.get("path", "")).replace("\\", "/")
                    if p:
                        state.read_paths.add(p)
                if action in ("semantic_search", "web_search", "web_fetch", "lsp"):
                    _fetched_content = True
                if action in ("system", "screen"):
                    _fetched_content = True

            # L1 grounding: a successful semantic_search returns real code-chunk
            # hits whose formatted text carries `File: <path>` lines. Those are
            # genuine content grounding (paths the agent actually saw), so they
            # populate read_paths too — a substantive final citing a search-hit
            # file must NOT be falsely rejected as "never read".
            if action == "semantic_search" and state.tool_success:
                for hit_path in re.findall(r"(?im)^File:\s*([\w./\\-]+\.py)", str(result.get("result", ""))):
                    hit_path = hit_path.replace("\\", "/").lstrip("./")
                    if hit_path:
                        state.read_paths.add(hit_path)

            if action == "web_search" and state.tool_success:
                state.did_web_search = True
                state.last_search_urls = self._extract_search_urls(result)
            if action == "web_fetch" and state.tool_success:
                state.did_web_fetch = True

            # Verify-after-change: editing a code file must be followed by a test
            # run before the agent may finalize (mirrors running tests/lint before
            # declaring done).
            if action == "filesystem" and tool_payload.get("operation") in ("write", "patch"):
                state.did_code_change = True
                path = str(tool_payload.get("path", ""))
                if path.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs")):
                    state.pending_verify = True
                    state._verify_final_rejected = False
                # L3 (2026 real-test signal): after a REAL code change by the edit
                # agent, actually run the related tests IN THE SANDBOX and record the
                # true test_pass (not the completion-proxy). At most once per stream
                # (expensive: DangerRoom copy + pytest). Gated by SWARM_EVOLUTION=1 —
                # zero overhead when evolution is off.
                if (agent_id == "coder" and not state._tests_ran
                        and self._fitness_gate_on()):
                    state._tests_ran = True
                    await self._run_change_tests(state, path)
            if action == "sandbox_repl" and state.tool_success:
                state.pending_verify = False
                state._verify_final_rejected = False

        _result_str = json.dumps(result, ensure_ascii=False)
        # Web fetch/search results get a MUCH larger budget than generic tool
        # output. The whole point of web_fetch is to deep-read a page (up to
        # 20KB fetched); capping it at MAX_RESULT_CHARS (1200) threw away the
        # fetched body, so the analysis agent saw only a sliver and produced
        # "Internet search: Not performed" even after web_fetch succeeded (it
        # genuinely had nothing to summarize). Filesystem listings stay at the
        # small cap (path lists). Uses the same 20000 default the fetcher caps
        # at, so no double-truncation loss beyond the fetch itself.
        _WEB_RESULT_CHARS = 20000
        _cap = _WEB_RESULT_CHARS if action in ("web_fetch", "web_search") else MAX_RESULT_CHARS
        if len(_result_str) > _cap:
            _result_str = _result_str[:_cap] + f"... [TRUNCATED: {len(_result_str)} total chars. Use targeted reads for details.]"
        state.tool_result_str = _result_str

        messages.append({"role": "assistant", "content": json.dumps({"action": action, **tool_payload}, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"TOOL RESULT ({action}):\n{_result_str}\n\nContinue."})

        return consecutive_errors, _fetched_content

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _extract_search_urls(self, result: dict) -> list:
        """Pull up to 3 result URLs from a successful web_search tool result so
        the loop can deterministically deep-read the top hit."""
        try:
            urls = []
            results = result.get("results") if isinstance(result, dict) else None
            if isinstance(results, list):
                for item in results:
                    u = item.get("url") if isinstance(item, dict) else None
                    if isinstance(u, str) and u.startswith("http") and u not in urls:
                        urls.append(u)
                    if len(urls) >= 3:
                        break
            return urls
        except Exception:
            return []

    def _get_allowed_tools(self, agent_id: str, genome_weights: dict = None) -> list:
        from runtime_v2.prompts.system_prompts import _AGENT_TOOLS
        allowed = _AGENT_TOOLS.get(agent_id, ["delegate", "final", "filesystem"])
        if genome_weights:
            allowed = sorted(allowed, key=lambda t: -genome_weights.get(t, 0.0))
        return allowed

    # -----------------------------------------------------------------------
    # Main agent loop
    # -----------------------------------------------------------------------
    async def step_agent_stream(
        self, agent_id: str, prompt: str,
        history: Optional[List[dict]] = None,
        delegation_chain: Optional[List[str]] = None,
        research_discharged: bool = False,
        resume: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        try:
            import runtime_v2.services.tool_executor as _te
            # Give THIS run a fresh exploration scope. The read-before-write
            # guard state is task-local (contextvars) so a concurrent
            # step_agent_stream in another task is never cleared/restored by
            # this run finishing.
            _te.reset_exploration_state()
        except Exception:
            _te = None

        async for chunk in self._step_agent_stream_inner(agent_id, prompt, history, delegation_chain, research_discharged, resume=resume):
            yield chunk

    async def _step_agent_stream_inner(
        self, agent_id: str, prompt: str,
        history: Optional[List[dict]] = None,
        delegation_chain: Optional[List[str]] = None,
        research_discharged: bool = False,
        resume: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        from runtime_v2.prompts.system_prompts import build
        from runtime_v2.services.memory_core import get_relevant_memories

        genome_id, genome_weights = "", {}
        try:
            import os as _os
            if _os.environ.get("SWARM_EVOLUTION", "").strip() == "1":
                from swarm_os.services.evolution_daemon import get_active_genome
                genome_id, genome_weights = get_active_genome(explore=True)
        except Exception:
            pass

        history = list(history or [])
        chain = list(delegation_chain or [agent_id])

        if len(chain) >= MAX_DEPTH:
            yield {"agent_id": agent_id, "type": "error", "content": f"Max delegation depth: {' -> '.join(chain)}"}
            self._feed_outcome(agent_id, prompt, _CallState(), completed=False, genome_id=genome_id)
            return

        model, provider = await lookup_model(agent_id)

        if not history and prompt:
            try:
                memories = await asyncio.to_thread(get_relevant_memories, prompt)
                if memories:
                    prompt = f"{prompt}\n\n{memories}"
            except Exception as exc:
                log.warning("Failed to fetch relevant memories: %s", exc)

        start_time = 0
        if self.orchestrator and hasattr(self.orchestrator, "router"):
            start_time = time.time()
            decision = await self.orchestrator.router.route_model(candidates=[model], role=self._agents.get(agent_id, {}).get("model_role", "fast"))
            if decision.model:
                model = decision.model
            provider = "router"

        sys_prompt = build(agent_id)
        if len(chain) > 1:
            sys_prompt += f"\n\nAlready visited: {' -> '.join(chain)}. Do NOT re-delegate to these agents."

        messages = [{"role": "system", "content": sys_prompt}] + history
        if prompt:
            messages.append({"role": "user", "content": prompt})

        resolved_model = None
        try:
            from runtime_v2.services._llm_client import get_litellm_model
            resolved_model = get_litellm_model(agent_id, model)
        except Exception:
            pass
        yield {"agent_id": agent_id, "type": "model_selected", "model": model, "provider": provider,
               "resolved_model": resolved_model,
               "requested_role": self._agents.get(agent_id, {}).get("model_role", "fast"),
               "attempt": 1, "temperature": 0.1}

        allowed_tools = self._get_allowed_tools(agent_id, genome_weights=genome_weights)
        consecutive_errors = 0
        unauthorized_tool_errors = 0
        decision_counts = {}
        history_actions = []
        healing_attempts = 0
        _fetched_content = agent_id not in ANALYSIS_AGENTS
        _reviewer_fails = 0
        state = _CallState()
        state.genome_id = genome_id
        state._start_time = start_time if start_time else time.time()
        initial_messages_len = len(messages)

        # --- Resume from a durable checkpoint (2026 autonomy move 3) ---
        # When a resume id is supplied, load the stored run state so an
        # interrupted run continues from its last consistent turn boundary
        # instead of replaying from the top — preserving everything L1-L6
        # verified/tested/reinforced (read_paths, test_pass_result, _tests_ran,
        # _contract_finals, the guards, the fitness counters). The prompt/history
        # passed on the resume call are REPLACED by the stored ones (the stored id
        # is authoritative). If the checkpoint can't be found/loaded, fail safe by
        # starting fresh (never a silently-wrong partial resume).
        start_turn = 0
        if resume:
            try:
                from runtime_v2.services.checkpointing import load_checkpoint
                ckpt = load_checkpoint(resume)
                if ckpt:
                    prompt = str(ckpt.get("prompt") or prompt)
                    messages = list(ckpt.get("messages") or messages)
                    chain = list(ckpt.get("delegation_chain") or chain)
                    research_discharged = bool(ckpt.get("research_discharged", research_discharged))
                    genome_id = str(ckpt.get("genome_id") or genome_id)
                    genome_weights = dict(ckpt.get("genome_weights") or {})
                    self._state_from_dict(state, ckpt.get("state") or {})
                    lg = ckpt.get("loop_guards") or {}
                    consecutive_errors = int(lg.get("consecutive_errors", 0) or 0)
                    unauthorized_tool_errors = int(lg.get("unauthorized_tool_errors", 0) or 0)
                    healing_attempts = int(lg.get("healing_attempts", 0) or 0)
                    _fetched_content = bool(lg.get("_fetched_content", _fetched_content))
                    decision_counts = dict(lg.get("decision_counts") or {})
                    history_actions = list(lg.get("history_actions") or [])
                    start_turn = int(ckpt.get("turn", 0) or 0)
                    initial_messages_len = int(ckpt.get("initial_messages_len", len(messages)) or len(messages))
                    yield {"agent_id": agent_id, "type": "resumed", "from_turn": start_turn,
                           "prompt": str(prompt)[:80]}
                    log.info("[%s] resumed from checkpoint %s at turn %d", agent_id, resume, start_turn)
                else:
                    log.warning("[%s] resume id %s not found; starting fresh", agent_id, resume)
                    yield {"agent_id": agent_id, "type": "resumed", "from_turn": 0, "fresh": True}
            except Exception as ckpt_err:
                log.warning("[%s] resume failed (%s); starting fresh", agent_id, ckpt_err)

        for turn in range(start_turn, MAX_TURNS):
            state._turn = turn + 1
            # --- Durable checkpoint (2026 autonomy move 3) ---
            # Persist at the TOP of each turn, BEFORE the decision is fetched, so a
            # crash mid-turn resumes from a consistent prior boundary. Written
            # every turn (atomic overwrite-latest) so the most recent state is what
            # a resume loads. NOT deleted here — delete happens only when the final
            # is ACCEPTED by L1 (handler_status == DONE), never on a rejected/
            # aborted/max-turns exit.
            try:
                from runtime_v2.services.checkpointing import write_checkpoint, checkpoint_id
                ckpt = {
                    "checkpoint_id": checkpoint_id(agent_id, prompt),
                    "agent_id": agent_id,
                    "prompt": prompt,
                    "turn": state._turn,
                    "messages": messages,
                    "state": self._state_to_dict(state),
                    "loop_guards": {
                        "consecutive_errors": consecutive_errors,
                        "unauthorized_tool_errors": unauthorized_tool_errors,
                        "healing_attempts": healing_attempts,
                        "_fetched_content": _fetched_content,
                        "decision_counts": decision_counts,
                        "history_actions": history_actions,
                    },
                    "delegation_chain": chain,
                    "research_discharged": research_discharged,
                    "genome_id": genome_id,
                    "genome_weights": genome_weights,
                    "resolved_model": resolved_model,
                    "initial_messages_len": initial_messages_len,
                }
                # Blocking file I/O (FileLock acquire + write_text + os.replace)
                # must not stall the single-threaded event loop — offload it.
                await asyncio.to_thread(write_checkpoint, ckpt["checkpoint_id"], ckpt)
            except Exception as ckpt_err:
                log.debug("[%s] checkpoint write skipped: %s", agent_id, ckpt_err)
            # --- Context window trim ---
            sys_msgs = [m for m in messages if m.get("role") == "system"]
            non_sys_msgs = [m for m in messages if m.get("role") != "system"]
            if len(non_sys_msgs) > MAX_HISTORY_TURNS * 2:
                non_sys_msgs = non_sys_msgs[-(MAX_HISTORY_TURNS * 2):]
            trimmed_messages = sys_msgs + non_sys_msgs

            # Inject the working todo list every turn so the agent's checklist is
            # always in context (survives message compaction).
            if state.todos:
                todos_block = (
                    f"\n\n[CURRENT TODO LIST]\n{self._todos_preview(state)}\n"
                    "Keep working through these items. Use action=todo with operation=done "
                    "when you finish one. Only call action=final when all items are done "
                    "or the task is genuinely complete."
                )
                trimmed_messages = trimmed_messages + [{"role": "user", "content": todos_block}]

            # --- Get decision (fast-route, warmup, or LLM) ---
            try:
                decision = await self._get_decision(agent_id, model, trimmed_messages, allowed_tools, prompt, turn, state, research_discharged)
            except (asyncio.TimeoutError, Exception) as exc:
                # BUG FIX: Don't abort the run on a single LLM failure — count it and
                # let the circuit breaker trigger the debugger, so a down backend heals
                # instead of silently terminating every agent run.
                is_timeout = isinstance(exc, asyncio.TimeoutError)
                consecutive_errors += 1
                yield {"agent_id": agent_id, "type": "error",
                       "content": f"[{agent_id}] LLM decision {'timed out' if is_timeout else 'error'}: {exc}"}
                if consecutive_errors >= 3:
                    if agent_id != "debugger" and healing_attempts < 1:
                        yield {"agent_id": agent_id, "type": "error", "content": f"Circuit Breaker Tripped! {consecutive_errors} consecutive LLM failures. Initiating Autonomous Self-Healing Sequence..."}
                        heal_task = (f"The agent '{agent_id}' has failed {consecutive_errors} times consecutively while talking to the LLM backend. "
                                     "The LLM is likely down or overloaded. Diagnose and fix.")
                        yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": "debugger", "task": heal_task}
                        async for chunk in self.step_agent_stream("debugger", heal_task, history=messages[-6:], delegation_chain=chain + ["debugger"]):
                            chunk.setdefault("delegated_by", agent_id)
                            yield chunk
                        healing_attempts += 1
                        consecutive_errors = 0
                        messages.append({"role": "user", "content": "The autonomous self-healing cycle has finished. Please retry your last action."})
                        continue
                    yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider,
                           "content": f"Task aborted after {consecutive_errors} LLM failures: {exc}"}
                    tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
                    self._feed_outcome(agent_id, prompt, state, completed=False, tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
                    return
                yield {"agent_id": agent_id, "type": "tool_result", "tool": "llm", "result": {"error": f"LLM decision failed: {exc}"}}
                messages.append({"role": "user", "content": f"Result: {{\"error\": \"LLM decision failed: {exc}\"}}. Retry with a different approach."})
                continue

            # --- Loop guard ---
            loop_status = self._check_loop(decision, decision_counts, history_actions) if decision and isinstance(decision, dict) else None
            if loop_status == "WARN":
                messages.append({"role": "assistant", "content": json.dumps({"action": decision.get('action'), "note": "duplicate action called before"})})
                messages.append({"role": "user", "content": "SYSTEM: You already made this exact call and have the result. Do NOT repeat it. Choose a different file/tool, or call action=final if you have enough information."})
                continue
            if loop_status:
                yield {"agent_id": agent_id, "type": "error", "content": f"Agent {agent_id} caught in a loop. Tripping circuit breaker."}
                # LEARN FROM THE LOOP (fix #5): a repeated-action loop is the
                # single highest-frequency agent failure mode — but it never fed
                # the reflexion pipeline, only handed off to a debugger that wrote
                # a prose healing plan. Store a structured rule so future runs get
                # a [PAST-MISTAKE WARNING] instead of re-walking the same dead-end
                # sequence of identical tool calls.
                try:
                    from swarm_os.services.reflection_loop import get_reflection_service
                    _loop_sig = json.dumps({k: v for k, v in (decision or {}).items() if k in ("action", "operation", "path", "query", "target_agent") and v}, ensure_ascii=False)[:200]
                    await get_reflection_service().store_reflexion(
                        task=f"agent:{agent_id} looping on repeated tool call {_loop_sig} goal {str(prompt)[:120]}",
                        action="loop_detected",
                        failure_reason=f"agent repeated the same tool decision >=3 times within the last 8 actions ({_loop_sig}) and tripped the circuit breaker.",
                        correction="Do NOT repeat the same tool call with identical arguments. If a tool failed, read the error, change the approach (different file/path/query/operation), or delegate. A repeated identical call will never produce a different result.",
                        do_not_repeat=f"agent:{agent_id} must not call the same tool with the same arguments more than twice.",
                        component=agent_id,
                        confidence=0.8,
                    )
                except Exception as loop_refl_err:
                    log.debug("[%s] loop reflexion skipped: %s", agent_id, loop_refl_err)
                if agent_id != "debugger" and healing_attempts < 1:
                    yield {"agent_id": agent_id, "type": "error", "content": "Circuit Breaker Tripped! Initiating Autonomous Self-Healing Sequence..."}
                    heal_task = (f"The agent '{agent_id}' is stuck in an infinite loop. Review the history and write a plan to fix the code or environment so they can succeed without looping. Provide a 'final' action when healed.")
                    yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": "debugger", "task": heal_task}
                    async for chunk in self.step_agent_stream("debugger", heal_task, history=messages[-6:], delegation_chain=chain + ["debugger"]):
                        chunk.setdefault("delegated_by", agent_id)
                        yield chunk
                    healing_attempts += 1
                    consecutive_errors = 0
                    decision_counts = {}
                    history_actions = []
                    messages.append({"role": "user", "content": "The autonomous self-healing cycle has finished. Please formulate a DIFFERENT action to avoid looping."})
                    continue
                yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": "Healing failed. Loop aborted."}
                tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
                self._feed_outcome(agent_id, prompt, state, completed=False, tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
                return

            # --- Validate decision ---
            if not decision or not isinstance(decision, dict) or "action" not in decision:
                log.warning("[%s] Invalid decision, using final action", agent_id)
                decision = {"action": "final", "response": "Task completed with default handler."}
                consecutive_errors += 1

            action = decision.get("action", "final").strip()
            log.info("[%s] action=%s", agent_id, action)
            self._record_event("agent_action", agent_id, {"action": action, "turn": turn})
            yield {"agent_id": agent_id, "content": f"[{action}]", "model": model}

            # --- Route action ---
            if action == "ask_user":
                if agent_id not in ["coordinator", "planner"]:
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        yield {"agent_id": agent_id, "type": "error", "content": f"Agent {agent_id} aborted after 3 consecutive errors."}
                        tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
                        self._feed_outcome(agent_id, prompt, state, completed=False, tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
                        return
                    yield {"agent_id": agent_id, "type": "tool_result", "tool": "ask_user", "result": {"error": f"ask_user not allowed for {agent_id}"}}
                    messages.append({"role": "assistant", "content": json.dumps({"action": "ask_user"})})
                    messages.append({"role": "user", "content": f"Result: {json.dumps({'error': f'ask_user not allowed for {agent_id}'})}"})
                    continue
                question = decision.get("question", "What do you want me to do?")
                yield {"agent_id": agent_id, "content": question, "model": model}
                yield {"agent_id": agent_id, "type": "ask_user", "model": model, "provider": provider, "question": question}
                self._record_success(model, start_time)
                return

            if action == "todo":
                # Pure in-memory checklist: no tool_executor round-trip.
                result = self._handle_todo(decision, agent_id, state)
                yield {"agent_id": agent_id, "content": f"[todo] {json.dumps(result, ensure_ascii=False)}", "model": model}
                yield {"agent_id": agent_id, "type": "tool_result", "tool": "todo", "result": result}
                messages.append({"role": "assistant", "content": json.dumps({"action": "todo", "operation": decision.get("operation", "list")})})
                messages.append({"role": "user", "content": f"TOOL RESULT (todo):\n{json.dumps(result, ensure_ascii=False)}\n\nContinue."})
                continue

            if action not in allowed_tools:
                unauthorized_tool_errors += 1
                if unauthorized_tool_errors >= 3:
                    yield {"agent_id": agent_id, "type": "error", "content": f"Agent {agent_id} aborted after 3 consecutive unauthorized tool errors."}
                    tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
                    self._feed_outcome(agent_id, prompt, state, completed=False, tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
                    return
                error_msg = f"Unauthorized tool '{action}' for role '{agent_id}'. Allowed: {allowed_tools}"
                yield {"agent_id": agent_id, "type": "tool_result", "tool": action, "result": {"error": error_msg}}
                messages.append({"role": "assistant", "content": json.dumps({"action": action})})
                messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                continue
            unauthorized_tool_errors = 0

            if action == "final":
                async for _ in self._handle_final(decision, agent_id, model, provider, messages, start_time, prompt, _fetched_content, state, research_discharged):
                    yield _
                # Delete the checkpoint ONLY when the final was ACCEPTED by L1
                # (handler_status == DONE). An L1-rejected final (CONTINUE) or any
                # failed-exit path must keep the checkpoint — the run isn't done.
                if state.handler_status == "DONE":
                    try:
                        from runtime_v2.services.checkpointing import delete_checkpoint, checkpoint_id
                        delete_checkpoint(checkpoint_id(agent_id, prompt))
                    except Exception as ckpt_err:
                        log.debug("[%s] checkpoint delete skipped: %s", agent_id, ckpt_err)
                if state.handler_status in ("DONE", "ABORT"):
                    return
                continue

            if action == "delegate":
                async for _ in self._handle_delegate(decision, agent_id, chain, model, provider, messages, prompt, start_time, state, research_discharged):
                    yield _
                if state.handler_status in ("COORDINATOR_DONE", "RECOVERED"):
                    return
                if state.handler_status == "SUBROUTINE_OK":
                    continue
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    yield {"agent_id": agent_id, "type": "error", "content": f"Agent {agent_id} aborted after 3 consecutive delegation errors."}
                    tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
                    self._feed_outcome(agent_id, prompt, state, completed=False, tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
                    return
                error_msg = (f"Self-delegation blocked: {agent_id}." if state.handler_status == "SELF_DELEGATION_ERROR"
                            else f"Circular delegation blocked. '{decision.get('target_agent')}' is already active in the chain ({' -> '.join(chain)}). Use 'final' to return results."
                            if state.handler_status == "CIRCULAR_ERROR"
                            else f"Agent '{decision.get('target_agent')}' not found.")
                yield {"agent_id": agent_id, "type": "tool_result", "tool": "delegate", "result": {"error": error_msg}}
                messages.append({"role": "assistant", "content": json.dumps({"action": "delegate", "target_agent": decision.get("target_agent"), "task": decision.get("task", prompt)})})
                messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                continue

            # --- Tool execution ---
            action = decision.get("action", "final").strip()
            yield {"agent_id": agent_id, "type": "tool_start", "tool": action, "result": "executing..."}
            consecutive_errors, _fetched_content = await self._handle_tool(
                decision, agent_id, messages, _fetched_content, turn, consecutive_errors, state)
            adjusted_weights = self.learning_critic.score(success=state.tool_success, confidence=0.8)
            yield {"agent_id": agent_id, "type": "critic_update", "weights": adjusted_weights}
            yield {"agent_id": agent_id, "type": "tool_result", "tool": action, "result": state.tool_result}
            yield {"agent_id": agent_id, "content": f"\nI called {action} with {json.dumps(state.tool_payload)} and got: {json.dumps(state.tool_result, ensure_ascii=False)}\n"}

            # --- Healing circuit breaker ---
            if consecutive_errors >= 3:
                if healing_attempts < 1 and agent_id != "debugger":
                    yield {"agent_id": agent_id, "type": "error", "content": f"Circuit Breaker Tripped! {consecutive_errors} consecutive failures. Initiating Autonomous Self-Healing Sequence..."}
                    heal_task = (f"The agent '{agent_id}' has failed {consecutive_errors} times consecutively while executing tools. "
                                 "Review the last few tool errors and write a plan to fix the code, syntax, or environment so they can succeed. "
                                 "Provide a 'final' action when healed.")
                    yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": "debugger", "task": heal_task}
                    async for chunk in self.step_agent_stream("debugger", heal_task, history=messages[-6:], delegation_chain=chain + ["debugger"]):
                        chunk.setdefault("delegated_by", agent_id)
                        yield chunk
                    healing_attempts += 1
                    consecutive_errors = 0
                    messages.append({"role": "user", "content": "The autonomous self-healing cycle has finished. Please retry your last action with the newly fixed system."})
                    continue
                yield {"agent_id": agent_id, "type": "error", "content": "Healing failed or aborted to prevent infinite loop."}
                yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": "Healing failed. Manual intervention required."}
                tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
                self._feed_outcome(agent_id, prompt, state, completed=False, tool_success_rate=tsr, turns_used=state._turn, genome_id=getattr(state, 'genome_id', ''))
                return

            # --- Message compaction: keep only last 2 tool turns ---
            new_messages = messages[initial_messages_len:]
            new_tool_turns = [i for i, m in enumerate(new_messages) if m["role"] == "assistant" and ("action" in str(m["content"]) or str(m["content"]).startswith("I called"))]
            if len(new_tool_turns) > 2:
                first_to_keep = new_tool_turns[-2]
                messages = messages[:initial_messages_len] + new_messages[first_to_keep:]

        yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": "[System: max turns reached]"}
        # Feed the FAILED outcome (completion=0) to the fitness store so the
        # kernel learns turn-exhaustion is a bad strategy — completion-gated at 0.4.
        tsr = (state._tool_successes / state._tool_attempts) if state._tool_attempts else 0.0
        self._feed_outcome(agent_id, prompt, state, completed=False,
                           tool_success_rate=tsr, turns_used=MAX_TURNS, genome_id=getattr(state, 'genome_id', ''))
        # Give the healing/learning system VISIBILITY into turn-budget exhaustion.
        # Previously max-turns just yielded a string — no event, no reflexion, so
        # a compound goal (filesystem + web_search) that ran out of turns left no
        # trace the circuit breaker / ReflectionDaemon / RepairWatchman could act on.
        try:
            self._record_event("turn_budget_exhausted", agent_id, {
                "agent_id": agent_id,
                "turns_used": MAX_TURNS,
                "prompt": str(prompt)[:300],
                "actions": [json.dumps(a, ensure_ascii=False)[:100] for a in history_actions][-6:],
            })
        except Exception as evt_err:
            log.debug("[%s] turn_budget event skipped: %s", agent_id, evt_err)
        try:
            from swarm_os.services.reflection_loop import get_reflection_service
            await get_reflection_service().store_reflexion(
                task=f"agent:{agent_id} compound goal {str(prompt)[:150]} exhausted {MAX_TURNS} turns",
                action="max_turns_reached",
                failure_reason="agent ran out of turns before completing the goal (likely a compound goal needing filesystem + web_search, or a slow LLM).",
                correction="Prefer completing the goal with the FEWEST tool calls. If a compound goal requires both codebase reads and web research, interleave them — do not spend all turns on exploration. Consider delegating to a specialized agent.",
                do_not_repeat=f"agent:{agent_id} must not burn all {MAX_TURNS} turns on exploration before the required tool (web_search/filesystem) is used.",
                component=agent_id,
                confidence=0.6,
            )
        except Exception as refl_err:
            log.debug("[%s] max-turns reflexion skipped: %s", agent_id, refl_err)
        self._record_success(model, start_time)
