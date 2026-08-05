"""Tests for the tool-failure -> ReflexionMemory lesson wiring.

A failed tool call (e.g. "File not found") must be persisted as a structured
ReflexionMemory rule (not just episodic memory) so check_for_past_mistakes()
can inject a [PAST-MISTAKE WARNING] into a future run's system prompt.
"""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from runtime_v2.api.agent_service_v2 import AgentServiceV2, _failure_lessons_seen, _CallState


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


@pytest.mark.asyncio
async def test_remember_failure_writes_diary_with_component(tmp_path, monkeypatch):
    """Fix 2: a tool failure must land in the organism diary WITH a component so
    run_reflection()'s distiller sees real agent failures (not genetic-kernel noise).
    Previously agent failures never reached the diary, and the distiller only saw
    kernel eval entries (component=None) — producing 137 'unknown'-component noise
    rules that swamped ReflexionMemory."""
    import swarm_os.services.reflection_loop as RL
    diary = tmp_path / "diary.jsonl"
    monkeypatch.setattr(RL, "DIARY_PATH", diary)

    service = AgentServiceV2()
    service._remember = AsyncMock()
    store_mock = AsyncMock()
    with patch("runtime_v2.api.agent_service_v2.time.time", return_value=1000.0), \
         patch("runtime_v2.api.agent_service_v2._failure_lessons_seen", {}), \
         patch("swarm_os.services.reflection_loop.get_reflection_service",
               return_value=AsyncMock(store_reflexion=store_mock)):
        await service._remember_failure(
            "code_analyzer", "filesystem",
            {"operation": "read", "path": "runtime_v2/services/agent_service.py"},
            "File not found: runtime_v2/services/agent_service.py",
        )

    lines = [l for l in diary.read_text().splitlines() if l.strip()]
    assert lines
    import json
    entry = json.loads(lines[-1])
    assert entry["component"] == "code_analyzer"
    assert entry["event"] == "tool_failure"
    assert "File not found" in entry["error"]
    # get_latest_failure must now prefer this component-tagged entry
    picked = RL.get_latest_failure(diary)
    assert picked is not None
    assert picked["component"] == "code_analyzer"


def test_get_latest_failure_prefers_component_tagged_over_noise(tmp_path):
    """Fix 2: get_latest_failure() must skip genetic-kernel eval noise (no component)
    and return the real agent failure."""
    import swarm_os.services.reflection_loop as RL
    import json
    diary = tmp_path / "diary.jsonl"
    diary.write_text(
        json.dumps({"ts": 1.0, "org": "o1", "event": "evaluation", "error": "http_422"}) + "\n"
        + json.dumps({"ts": 2.0, "org": "o1", "event": "action", "error": "[WinError 10061] conn"}) + "\n"
        + json.dumps({"ts": 3.0, "event": "tool_failure", "component": "code_analyzer", "error": "File not found"}) + "\n"
    )
    picked = RL.get_latest_failure(diary)
    assert picked is not None
    assert picked.get("component") == "code_analyzer"
    assert "File not found" in picked.get("error", "")


@pytest.mark.asyncio
async def test_tool_failure_records_event_store_event(tmp_path):
    """Fix 1: a failed tool call must persist a tool_result event to the event
    store so RepairWatchman / /autofix / goal-loop verification (which tail
    events.jsonl for event_type == 'tool_result') actually see failures. Previously
    events.jsonl had zero tool_result lines — the repair path was starved."""
    from swarm_os.events.store import EventStore
    service = AgentServiceV2()
    service.event_store = EventStore(tmp_path)
    service._remember = AsyncMock()
    service._remember_failure = AsyncMock()

    async def _fake_run(action, payload):
        return {"ok": False, "error": "File not found: runtime_v2/services/agent_service.py"}

    from runtime_v2.services import tool_executor
    with patch.object(tool_executor, "run", _fake_run):
        await service._handle_tool(
            {"action": "filesystem", "operation": "read", "path": "runtime_v2/services/agent_service.py"},
            "code_analyzer", [], False, 0, 0, _CallState(),
        )

    events = service.event_store.read_all()
    tool_events = [e for e in events if e.get("event_type") == "tool_result"]
    assert len(tool_events) == 1
    assert tool_events[0]["payload"]["result"]["ok"] is False
    assert "File not found" in tool_events[0]["payload"]["result"]["error"]
