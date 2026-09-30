"""Verify delegation circular-prevention behavior.

Proves:
1. Circular delegation is detected (target in chain)
2. Recursion does not continue indefinitely
3. Prior-result recovery is attempted when possible
4. CIRCULAR_ERROR is set when no recovery is possible
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState


def _make_service():
    svc = AgentServiceV2.__new__(AgentServiceV2)
    svc.event_store = MagicMock()
    svc.event_store.append = MagicMock()
    svc.orchestrator = MagicMock()
    svc.orchestrator.events = svc.event_store
    svc.orchestrator.router = MagicMock()
    svc.orchestrator.router.route_model.return_value = ("robs4b", "llama")
    svc.orchestrator.bridge = MagicMock()
    svc.orchestrator.bridge.get_relevant_memories = AsyncMock(return_value="")
    svc.orchestrator.mcp = MagicMock()
    svc.orchestrator.mcp.get_tool_schemas_for_agent.return_value = []
    svc._prompt_repairer = MagicMock()
    svc._prompt_repairer.check_for_past_mistakes = AsyncMock(return_value="")
    svc._agents = {
        "coordinator": {"tools": ["filesystem"]},
        "coder": {"tools": ["filesystem"]},
        "debugger": {"tools": ["filesystem"]},
        "planner": {"tools": ["filesystem"]},
    }
    svc._fitness_env_enabled = MagicMock(return_value=False)
    svc._feed_outcome = MagicMock()
    svc._feed_aborted_outcome = MagicMock()
    svc._write_run_trajectory = MagicMock()
    svc._record_event = MagicMock()
    svc._record_success = MagicMock()
    svc.get_lesson_manager = MagicMock(return_value=MagicMock(
        render_active_lessons=AsyncMock(return_value=""),
        get_all=AsyncMock(return_value=[]),
    ))
    return svc


class TestCircularDelegationPrevention:
    @pytest.mark.asyncio
    async def test_self_delegation_sets_error(self):
        """A target matching agent_id is caught as self-delegation."""
        svc = _make_service()
        state = _CallState()
        messages = []
        decision = {"action": "delegate", "target_agent": "coder", "task": "test"}

        gen = svc._handle_delegate(
            decision, "coder", ["coordinator", "coder"],
            "robs4b", "llama", messages, "test", 0.0, state,
        )
        async for _ in gen:
            pass
        assert state.handler_status == "SELF_DELEGATION_ERROR"

    @pytest.mark.asyncio
    async def test_circular_delegation_sets_circular_error(self):
        """A -> B -> A (circular) sets CIRCULAR_ERROR when no recovery is found."""
        svc = _make_service()
        state = _CallState()
        messages = []
        decision = {"action": "delegate", "target_agent": "debugger", "task": "test"}

        gen = svc._handle_delegate(
            decision, "coder", ["coordinator", "debugger"],
            "robs4b", "llama", messages, "test", 0.0, state,
        )
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        assert state.handler_status == "CIRCULAR_ERROR"

    @pytest.mark.asyncio
    async def test_circular_recovery_uses_prior_result(self):
        """When a prior result from the circular target exists, recovery yields it."""
        svc = _make_service()
        state = _CallState()
        messages = [
            {"role": "assistant", "content": "Let me delegate to debugger."},
            {"role": "user", "content": "TOOL RESULT (delegate): debugger responded: prior answer here"},
        ]
        decision = {"action": "delegate", "target_agent": "debugger", "task": "test"}

        gen = svc._handle_delegate(
            decision, "coder", ["coordinator", "debugger"],
            "robs4b", "llama", messages, "test", 0.0, state,
        )
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)

        assert state.handler_status == "RECOVERED"
        final_chunks = [c for c in chunks if c.get("type") == "final"]
        assert len(final_chunks) == 1
        assert "prior answer here" in final_chunks[0]["content"]
        assert "Recovered from circular delegation" in final_chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_unknown_target_sets_error(self):
        """Delegation to an unknown agent sets AGENT_NOT_FOUND."""
        svc = _make_service()
        state = _CallState()
        messages = []
        decision = {"action": "delegate", "target_agent": "nonexistent", "task": "test"}

        gen = svc._handle_delegate(
            decision, "coder", ["coordinator"],
            "robs4b", "llama", messages, "test", 0.0, state,
        )
        async for _ in gen:
            pass
        assert state.handler_status == "AGENT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_normal_delegation_extends_chain(self):
        """Normal delegation extends the chain without triggering circular detection."""
        svc = _make_service()
        state = _CallState()
        messages = []
        decision = {"action": "delegate", "target_agent": "debugger", "task": "investigate"}

        call_args = []

        async def mock_step(agent_id, prompt, **kwargs):
            call_args.append(kwargs.get("delegation_chain", []))
            yield {"type": "final", "content": "done"}

        svc.step_agent_stream = mock_step

        gen = svc._handle_delegate(
            decision, "coder", ["coordinator", "coder"],
            "robs4b", "llama", messages, "test", 0.0, state,
        )
        async for _ in gen:
            pass

        assert len(call_args) == 1
        assert "debugger" in call_args[0]
        assert call_args[0] == ["coordinator", "coder", "debugger"]
