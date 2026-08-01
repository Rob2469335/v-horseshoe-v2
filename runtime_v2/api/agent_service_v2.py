from __future__ import annotations
import swarm_os.bootstrap
import json, logging
import asyncio
import time
import re
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

log = logging.getLogger(__name__)


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

from runtime_v2.api._agent_config import (
    MAX_TURNS, MAX_DEPTH, ANALYSIS_AGENTS,
    _DEFAULTS, MAX_HISTORY_TURNS, MAX_RESULT_CHARS,
)
from runtime_v2.api._agent_routing import (
    lookup_model, fast_route_coordinator, fast_start_for_agent,
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
            except Exception:
                pass
        self._record_event("generation_completed", "agent_service_v2", {
            "model": model, "duration_ms": (time.time() - start_time) * 1000,
            "status": "success", "learning_outcome": {"result": "success"}})

    async def _remember(self, text: str, category: str = "general"):
        try:
            from runtime_v2.services.memory_core import remember_fact
            await asyncio.to_thread(remember_fact, text, category=category)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Decision fetching: fast-route, warmup, or LLM call
    # -----------------------------------------------------------------------
    async def _get_decision(self, agent_id: str, model: str, trimmed_messages: list,
                            allowed_tools: list, prompt: str, turn: int) -> Optional[dict]:
        if agent_id == "coordinator" and turn == 0:
            fast = fast_route_coordinator(prompt)
            if fast is not None:
                return fast
            return await self._call_llm(model, trimmed_messages, agent_id, allowed_tools)

        fast = fast_start_for_agent(agent_id, turn)
        if fast is not None:
            return fast
        return await self._call_llm(model, trimmed_messages, agent_id, allowed_tools)

    async def _call_llm(self, model: str, messages: list, agent_id: str, allowed_tools: list) -> Optional[dict]:
        from runtime_v2.services.stream_runner import get_tool_decision
        try:
            # UPGRADE: asyncio.timeout() — composable context manager
            async with asyncio.timeout(120.0):
                return await get_tool_decision(model, messages, agent_id, allowed_tools=allowed_tools)
        except asyncio.TimeoutError:
            log.warning("[%s] LLM timeout >120s", agent_id)
            raise
        except Exception as exc:
            log.warning("[%s] LLM error: %s", agent_id, exc)
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

        if not state.tool_success:
            consecutive_errors += 1
            await self._remember(f"Agent {agent_id} called {action} which failed with {result.get('error', 'unknown error')}. Strategy needs adjustment.", category="self_reflection")
        else:
            # BUG FIX: Decay rather than hard-reset so a failure→success→failure
            # oscillation still reaches the breaker. Previously a success zeroed the
            # counter, letting intermittent failures evade healing forever.
            consecutive_errors = max(0, consecutive_errors - 1)
            if not _fetched_content:
                if action == "filesystem" and tool_payload.get("operation") in ("read", "read_all"):
                    _fetched_content = True
                if action in ("semantic_search", "web_search", "lsp"):
                    _fetched_content = True

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
        return _AGENT_TOOLS.get(agent_id, ["delegate", "final", "filesystem"])

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

        yield {"agent_id": agent_id, "type": "model_selected", "model": model, "provider": provider,
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
        initial_messages_len = len(messages)

        for turn in range(MAX_TURNS):
            # --- Context window trim ---
            sys_msgs = [m for m in messages if m.get("role") == "system"]
            non_sys_msgs = [m for m in messages if m.get("role") != "system"]
            if len(non_sys_msgs) > MAX_HISTORY_TURNS * 2:
                non_sys_msgs = non_sys_msgs[-(MAX_HISTORY_TURNS * 2):]
            trimmed_messages = sys_msgs + non_sys_msgs

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
                    yield {"agent_id": agent_id, "type": "error", "content": f"Circuit Breaker Tripped! Initiating Autonomous Self-Healing Sequence..."}
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
        self._record_success(model, start_time)
