"""Tests for the tool-failure -> ReflexionMemory lesson wiring.

A failed tool call (e.g. "File not found") must be persisted as a structured
ReflexionMemory rule (not just episodic memory) so check_for_past_mistakes()
can inject a [PAST-MISTAKE WARNING] into a future run's system prompt.
"""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from runtime_v2.api.agent_service_v2 import AgentServiceV2, _failure_lessons_seen


@pytest.mark.asyncio
async def test_failure_lesson_generates_file_not_found_guidance():
    service = AgentServiceV2()
    correction, do_not = service._failure_lesson(
        "filesystem", {"operation": "read", "path": "runtime_v2/core/agent_service_v2.py"},
        "File not found: runtime_v2/core/agent_service_v2.py",
    )
    assert "list" in correction.lower() and "runtime_v2" in correction.lower()
    assert "Do NOT guess file paths" in do_not


@pytest.mark.asyncio
async def test_failure_lesson_falls_back_to_generic_guidance():
    service = AgentServiceV2()
    correction, do_not = service._failure_lesson(
        "sandbox_repl", {"language": "python"}, "syntax error"
    )
    assert "sandbox_repl" in correction
    assert "Do NOT repeat" in do_not


@pytest.mark.asyncio
async def test_remember_failure_stores_reflexion_with_do_not_repeat():
    service = AgentServiceV2()
    service._remember = AsyncMock()
    store_mock = AsyncMock()
    with patch("runtime_v2.api.agent_service_v2.time.time", return_value=1000.0), \
         patch("runtime_v2.api.agent_service_v2._failure_lessons_seen", {}), \
         patch("swarm_os.services.reflection_loop.get_reflection_service",
               return_value=AsyncMock(store_reflexion=store_mock)) as get_svc:
        await service._remember_failure(
            "code_analyzer", "filesystem",
            {"operation": "read", "path": "runtime_v2/core/agent_service_v2.py"},
            "File not found: runtime_v2/core/agent_service_v2.py",
        )
    assert store_mock.await_count == 1
    kwargs = store_mock.await_args.kwargs
    assert kwargs["component"] == "code_analyzer"
    assert kwargs["do_not_repeat"]
    assert "analyzing auditing codebase" in kwargs["task"]
    assert kwargs["failure_reason"].startswith("File not found")


@pytest.mark.asyncio
async def test_remember_failure_dedupes_identical_errors():
    service = AgentServiceV2()
    service._remember = AsyncMock()
    store_mock = AsyncMock()
    with patch("runtime_v2.api.agent_service_v2.time.time", return_value=1000.0), \
         patch("runtime_v2.api.agent_service_v2._failure_lessons_seen", {}), \
         patch("swarm_os.services.reflection_loop.get_reflection_service",
               return_value=AsyncMock(store_reflexion=store_mock)):
        await service._remember_failure(
            "code_analyzer", "filesystem", {"path": "x.py"}, "File not found: x.py")
        await service._remember_failure(
            "code_analyzer", "filesystem", {"path": "x.py"}, "File not found: x.py")
    assert store_mock.await_count == 1
