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

from runtime_v2.api._agent_config import (
    MAX_TURNS, MAX_DEPTH, ANALYSIS_AGENTS,
    _DEFAULTS, MAX_HISTORY_TURNS, MAX_RESULT_CHARS,
)
from runtime_v2.api._agent_routing import (
    lookup_model, fast_route_coordinator, fast_start_for_agent,
    matches_task_keywords, best_route_target, _RESEARCHER_FIRST_TURNS,
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
    async def _preload_model(self, model_name: str): pass

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

    def _feed_outcome(self, agent_id: str, prompt: str, state: _CallState,
                      completed: bool = True, tool_success_rate: float = 0.0,
                      turns_used: int = 0):
        """Feed a REAL agent outcome to the outcome-fitness store so the
        evolutionary kernel can select on grounded fitness instead of LLM noise.
        Gated by SWARM_EVOLUTION=1 (zero overhead otherwise). Deterministic
        signals only — never self-judged by the proposing model."""
        try:
            from swarm_os.services.outcome_fitness import _fitness_env_enabled
            if not _fitness_env_enabled():
                return
            import time as _t
            elapsed = max(0.01, _t.time() - (state._start_time or _t.time()))
            # Efficiency = baseline(8 turns) / actual, clipped to [0,1].
            efficiency = min(1.0, 8.0 / max(1, turns_used or 8)) if completed else 0.0
            from swarm_os.services.outcome_fitness import record_outcome
            record_outcome(
                f"agent:{agent_id}",
                completion=1.0 if completed else 0.0,
                test_pass=1.0 if completed else 0.0,
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
                            allowed_tools: list, prompt: str, turn: int) -> Optional[dict]:
        if agent_id == "coordinator" and turn == 0:
            fast = fast_route_coordinator(prompt)
            if fast is not None:
                return fast
            decision = await self._call_llm(model, trimmed_messages, agent_id, allowed_tools)
            # FIX-INTENT GUARD: if the goal implies editing/fixing code, force the
            # delegate target to `coder` (edit-capable) even when the LLM coordinator
            # picks the report-only code_analyzer. A compound "analyze + fix bugs"
            # goal must actually FIX (like a human maintainer / opencode), not just
            # return an analysis report.
            if decision and decision.get("action") == "delegate":
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
        if fast is not None:
            # INTERNET-GOAL FIX: if an analysis agent was handed an internet goal
            # (codebase + web, or pure web), inject web_search BEFORE the
            # filesystem warmup. The warmup consumed all 8 turns with file reads
            # (4 warmup + repeated reads) and the agent hit "max turns reached"
            # without ever calling web_search — the guard rejected the final but
            # there was no budget left to search. Searching first guarantees the
            # internet portion of the goal is actually done.
            if turn == 0 and agent_id in ANALYSIS_AGENTS:
                query = (prompt or "").strip()
                internet_goal = bool(_INTERNET_GOAL_RE.search(query)) if query else False
                if internet_goal:
                    log.info("[%s] internet goal — fast-start turn %d → web_search (before warmup)", agent_id, turn)
                    return {"action": "web_search", "query": _clean_search_query(query)}
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
        return await self._call_llm(model, trimmed_messages, agent_id, allowed_tools)

    async def _call_llm(self, model: str, messages: list, agent_id: str, allowed_tools: list) -> Optional[dict]:
        from runtime_v2.services.stream_runner import get_tool_decision
        try:
            # UPGRADE: asyncio.timeout() — composable context manager.
            # 300s must be >= _STEP_TIMEOUT (180s) so the step-level budget in
            # stream_runner is the binding constraint, not this outer wrapper.
            async with asyncio.timeout(300.0):
                return await get_tool_decision(model, messages, agent_id, allowed_tools=allowed_tools)
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
                           "language", "code", "server_name", "tool", "question")
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
                            _fetched_content: bool, state: _CallState):
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
                               tool_success_rate=tsr, turns_used=state._turn)
            state.handler_status = "DONE"
            return

        # Verify-after-change: reject a final call once when a code file was edited
        # but never tested, forcing the agent to run sandbox_repl before reporting done.
        if state.pending_verify and not state._verify_final_rejected:
            state._verify_final_rejected = True
            state.handler_status = "CONTINUE"
            log.warning("[%s] Rejected final: pending code verification.", agent_id)
            messages.append({"role": "user", "content": (
                "SYSTEM: You edited a code file but have not verified it runs. "
                "Use action=sandbox_repl (language=pytest or language=python) to test the "
                "change before calling action=final. Do NOT report success without a test run."
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
        prompt_lower = (prompt or "").lower()
        internet_goal = any(k in prompt_lower for k in (
            "search internet", "search the web", "search the internet", "web search",
            "search online", "research best practices", "find improvements",
            "state of the art", "latest versions", "modern best practices",
            "what's new", "what is new",
        ))
        has_web_search_tool = "web_search" in self._get_allowed_tools(agent_id)
        # REQUIRE both web_search AND web_fetch for internet goals on analysis
        # agents. Searching is not enough — the agent must deep-read at least one
        # authoritative page (docs/SO/GitHub) so the final answer is grounded in
        # actual fetched content, not just search snippets. This matches how a
        # human researcher (or opencode) works: search → fetch → read → synthesize.
        needs_fetch = "web_fetch" in self._get_allowed_tools(agent_id)
        if internet_goal and not state.did_web_search and has_web_search_tool and agent_id in ANALYSIS_AGENTS:
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
        if internet_goal and needs_fetch and not state.did_web_fetch and agent_id in ANALYSIS_AGENTS:
            state._web_final_rejected = True
            state.handler_status = "CONTINUE"
            log.warning("[%s] Rejected final: internet goal without web_fetch.", agent_id)
            messages.append({"role": "user", "content": (
                "SYSTEM: You searched the web, but you called action=final without deep-reading "
                "any result. You MUST use action=web_fetch to read at least one authoritative "
                "page (official docs, Stack Overflow, GitHub) about the topic, extract the "
                "relevant details, and THEN synthesize your final answer from that content. "
                "Do NOT finalize from search snippets alone."
            )})
            return

        response_text = str(decision.get("response", "Task complete."))

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
                           tool_success_rate=tsr, turns_used=state._turn)
        await self._remember(f"[{agent_id}] task completed: {str(prompt)[:80]} -> {response_text[:120]}", category="general")
        state.handler_status = "DONE"

    # -----------------------------------------------------------------------
    # Handler: delegate
    # -----------------------------------------------------------------------
    async def _handle_delegate(self, decision: dict, agent_id: str, chain: list,
                               model: str, provider: str, messages: list,
                               prompt: str, start_time: float, state: _CallState):
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
            child_history.append(m)

        async for chunk in self.step_agent_stream(target, task, history=child_history, delegation_chain=chain + [target]):
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
            await self._remember_failure(agent_id, action, tool_payload, result.get('error', 'unknown error'))
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
                    "result": {"ok": False, "error": str(result.get('error', 'unknown error'))[:500]},
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
                if action in ("semantic_search", "web_search", "web_fetch", "lsp"):
                    _fetched_content = True
                if action in ("system", "screen"):
                    _fetched_content = True

            if action == "web_search" and state.tool_success:
                state.did_web_search = True
            if action == "web_fetch" and state.tool_success:
                state.did_web_fetch = True

            # Verify-after-change: editing a code file must be followed by a test
            # run before the agent may finalize (mirrors running tests/lint before
            # declaring done).
            if action == "filesystem" and tool_payload.get("operation") in ("write", "patch"):
                path = str(tool_payload.get("path", ""))
                if path.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs")):
                    state.pending_verify = True
                    state._verify_final_rejected = False
            if action == "sandbox_repl" and state.tool_success:
                state.pending_verify = False
                state._verify_final_rejected = False

        _result_str = json.dumps(result, ensure_ascii=False)
        if len(_result_str) > MAX_RESULT_CHARS:
            _result_str = _result_str[:MAX_RESULT_CHARS] + f"... [TRUNCATED: {len(_result_str)} total chars. Use targeted reads for details.]"
        state.tool_result_str = _result_str

        messages.append({"role": "assistant", "content": json.dumps({"action": action, **tool_payload}, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"TOOL RESULT ({action}):\n{_result_str}\n\nContinue."})

        return consecutive_errors, _fetched_content

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _get_allowed_tools(self, agent_id: str) -> list:
        from runtime_v2.prompts.system_prompts import _AGENT_TOOLS
        allowed = _AGENT_TOOLS.get(agent_id, ["delegate", "final", "filesystem"])
        # SOTA evolution hook: when SWARM_EVOLUTION=1, order the allowed tools by
        # the best evolved genome's tool_genes (high-weight = preferred from real
        # outcomes). This makes the agent's tool-selection policy evolve from real
        # task outcomes instead of staying a fixed hand-authored list. No-op when
        # evolution is off (zero overhead).
        try:
            import os as _os
            if _os.environ.get("SWARM_EVOLUTION", "").strip() != "1":
                return allowed
            from swarm_os.services.evolution_daemon import _best_genome_tool_weights
            weights = _best_genome_tool_weights()
            if weights:
                allowed = sorted(allowed, key=lambda t: -weights.get(t, 0.0))
        except Exception as _e:
            log.debug("[%s] evolved tool ordering skipped: %s", agent_id, _e)
        return allowed

    # -----------------------------------------------------------------------
    # Main agent loop
    # -----------------------------------------------------------------------
    async def step_agent_stream(
        self, agent_id: str, prompt: str,
        history: Optional[List[dict]] = None,
        delegation_chain: Optional[List[str]] = None,
    ) -> AsyncGenerator[dict, None]:
        from runtime_v2.prompts.system_prompts import build
        from runtime_v2.services.memory_core import get_relevant_memories

        # Per-run isolation: snapshot the shared read-before-write exploration set
        # and filesystem read cache, then clear them for THIS run so it cannot
        # inherit another agent's "seen" files (which would let it patch paths it
        # never explored) or read stale cached content. Restore the prior state in
        # a finally so a concurrent run's in-flight tracking is NOT wiped mid-run
        # (clearing the global from one run destroyed another run's state).
        try:
            import runtime_v2.services.tool_executor as _te
            _explored_snapshot = set(_te._explored_paths)
            _cache_snapshot = dict(_te._filesystem_read_cache)
            _te._explored_paths.clear()
            _te._filesystem_read_cache.clear()
        except Exception:
            _te = None
            _explored_snapshot = set()
            _cache_snapshot = {}

        history = list(history or [])
        chain = list(delegation_chain or [agent_id])

        if len(chain) >= MAX_DEPTH:
            yield {"agent_id": agent_id, "type": "error", "content": f"Max delegation depth: {' -> '.join(chain)}"}
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

        allowed_tools = self._get_allowed_tools(agent_id)
        consecutive_errors = 0
        unauthorized_tool_errors = 0
        decision_counts = {}
        history_actions = []
        healing_attempts = 0
        _fetched_content = agent_id not in ANALYSIS_AGENTS
        _reviewer_fails = 0
        state = _CallState()
        state._start_time = start_time if start_time else time.time()
        initial_messages_len = len(messages)

        for turn in range(MAX_TURNS):
            state._turn = turn + 1
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
                decision = await self._get_decision(agent_id, model, trimmed_messages, allowed_tools, prompt, turn)
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
                        async for chunk in self.step_agent_stream("debugger", heal_task, history=messages[-6:], delegation_chain=["debugger"]):
                            chunk.setdefault("delegated_by", agent_id)
                            yield chunk
                        healing_attempts += 1
                        consecutive_errors = 0
                        messages.append({"role": "user", "content": "The autonomous self-healing cycle has finished. Please retry your last action."})
                        continue
                    yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider,
                           "content": f"Task aborted after {consecutive_errors} LLM failures: {exc}"}
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
                if agent_id != "debugger" and healing_attempts < 1:
                    yield {"agent_id": agent_id, "type": "error", "content": "Circuit Breaker Tripped! Initiating Autonomous Self-Healing Sequence..."}
                    heal_task = (f"The agent '{agent_id}' is stuck in an infinite loop. Review the history and write a plan to fix the code or environment so they can succeed without looping. Provide a 'final' action when healed.")
                    yield {"agent_id": agent_id, "type": "agent_handoff", "from": agent_id, "to": "debugger", "task": heal_task}
                    async for chunk in self.step_agent_stream("debugger", heal_task, history=messages[-6:], delegation_chain=["debugger"]):
                        chunk.setdefault("delegated_by", agent_id)
                        yield chunk
                    healing_attempts += 1
                    consecutive_errors = 0
                    decision_counts = {}
                    history_actions = []
                    messages.append({"role": "user", "content": "The autonomous self-healing cycle has finished. Please formulate a DIFFERENT action to avoid looping."})
                    continue
                yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": "Healing failed. Loop aborted."}
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
                    return
                error_msg = f"Unauthorized tool '{action}' for role '{agent_id}'. Allowed: {allowed_tools}"
                yield {"agent_id": agent_id, "type": "tool_result", "tool": action, "result": {"error": error_msg}}
                messages.append({"role": "assistant", "content": json.dumps({"action": action})})
                messages.append({"role": "user", "content": f"Result: {json.dumps({'error': error_msg})}"})
                continue
            unauthorized_tool_errors = 0

            if action == "final":
                async for _ in self._handle_final(decision, agent_id, model, provider, messages, start_time, prompt, _fetched_content, state):
                    yield _
                if state.handler_status in ("DONE", "ABORT"):
                    return
                continue

            if action == "delegate":
                async for _ in self._handle_delegate(decision, agent_id, chain, model, provider, messages, prompt, start_time, state):
                    yield _
                if state.handler_status in ("COORDINATOR_DONE", "RECOVERED"):
                    return
                if state.handler_status == "SUBROUTINE_OK":
                    continue
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    yield {"agent_id": agent_id, "type": "error", "content": f"Agent {agent_id} aborted after 3 consecutive delegation errors."}
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
                    async for chunk in self.step_agent_stream("debugger", heal_task, history=messages[-6:], delegation_chain=["debugger"]):
                        chunk.setdefault("delegated_by", agent_id)
                        yield chunk
                    healing_attempts += 1
                    consecutive_errors = 0
                    messages.append({"role": "user", "content": "The autonomous self-healing cycle has finished. Please retry your last action with the newly fixed system."})
                    continue
                yield {"agent_id": agent_id, "type": "error", "content": "Healing failed or aborted to prevent infinite loop."}
                yield {"agent_id": agent_id, "type": "final", "model": model, "provider": provider, "content": "Healing failed. Manual intervention required."}
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
                           tool_success_rate=tsr, turns_used=MAX_TURNS)
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

        # Restore the shared exploration state snapshotted at run entry, so
        # concurrent runs keep their own read-before-write tracking intact.
        try:
            if _te is not None:
                _te._explored_paths = _explored_snapshot
                _te._filesystem_read_cache = _cache_snapshot
        except Exception as restore_err:
            log.debug("[%s] failed to restore shared exploration state: %s", agent_id, restore_err)
