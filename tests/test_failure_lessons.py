"""Tests for the tool-failure -> ReflexionMemory lesson wiring.

A failed tool call (e.g. "File not found") must be persisted as a structured
ReflexionMemory rule (not just episodic memory) so check_for_past_mistakes()
can inject a [PAST-MISTAKE WARNING] into a future run's system prompt.
"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch

from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState


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
               return_value=AsyncMock(store_reflexion=store_mock)):
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


@pytest.mark.asyncio
async def test_past_mistake_warning_injected_into_tool_decision_prompt():
    """Reviewer item #1 (b): prove the [PAST-MISTAKE WARNING] actually lands in the
    system prompt handed to the LLM for a tool decision — not just that a rule was
    stored. A stored ReflexionMemory hint must appear in the final messages sent to
    complete_for_tool_decision."""
    import runtime_v2.services.stream_runner as SR

    captured = {}

    class FakeMsg:
        content = '{"action": "final", "response": "ok"}'

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    async def fake_complete(model, messages, fallbacks):
        captured["messages"] = messages
        return FakeResp()

    class FakeReflectionService:
        async def check_for_past_mistakes(self, task_context):
            return (
                "WARNING: A similar approach previously failed. Advice: Before reading "
                "a file, use filesystem operation=list or glob to confirm it exists. "
                "Do NOT repeat: Do NOT guess file paths"
            )

    async def fake_live_fallbacks(mode="auto"):
        return []

    with patch.object(SR, "complete_for_tool_decision", side_effect=fake_complete), \
         patch("swarm_os.services.reflection_loop.get_reflection_service",
               return_value=FakeReflectionService()), \
         patch("runtime_v2.services._semantic_decision_cache.get_semantic_cached_decision",
               new=None), \
         patch("runtime_v2.services.memory_core.get_relevant_memories", return_value=""), \
         patch("runtime_v2.services.fallback_manager.get_live_fallbacks",
               side_effect=fake_live_fallbacks):
        result = await SR.get_tool_decision(
            model="qwen3.5-4b",
            messages=[{"role": "user", "content": "analyze the codebase for bugs"},
                      {"role": "user", "content": "fix the failing filesystem read"}],
            agent_id="coder",
            allowed_tools=["filesystem", "final"],
        )

    assert result is not None
    sent = captured["messages"]
    system_text = "\n".join(str(m.get("content", "")) for m in sent if m.get("role") == "system")
    assert "[PAST-MISTAKE WARNING]" in system_text, "warning must be injected into the system prompt"
    assert "Before reading a file, use filesystem operation=list or glob" in system_text
    assert "Do NOT guess file paths" in system_text


@pytest.mark.asyncio
async def test_malformed_json_retry_uses_json_repair_prompt():
    """The malformed-JSON retry prompt must reuse the canonical JSON_REPAIR_PROMPT
    (no markdown / no code fences / no XML tags / action key), not a weaker
    hand-built variant. The empty-XML-tag output makes extract_json raise, driving
    the retry branch."""
    import runtime_v2.services.stream_runner as SR

    captured = []

    class FakeMsg:
        content = '{"action": "final", "response": "ok"}'

    class FakeChoice:
        message = FakeMsg()

    class FakeResp:
        choices = [FakeChoice()]

    async def fake_complete(model, messages, fallbacks):
        captured.append(messages)
        if len(captured) == 1:
            return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": "<tool_call></tool_call>"})()})()]})()
        return FakeResp()

    async def fake_live_fallbacks(mode="auto"):
        return []

    class FakeReflectionService:
        async def check_for_past_mistakes(self, task_context):
            return ""

        async def store_reflexion(self, **kwargs):
            return None

    with patch.object(SR, "complete_for_tool_decision", side_effect=fake_complete), \
         patch("runtime_v2.services._semantic_decision_cache.get_semantic_cached_decision",
               new=None), \
         patch("runtime_v2.services.memory_core.get_relevant_memories", return_value=""), \
         patch("swarm_os.services.reflection_loop.get_reflection_service",
               return_value=FakeReflectionService()), \
         patch("runtime_v2.services.fallback_manager.get_live_fallbacks",
               side_effect=fake_live_fallbacks):
        result = await SR.get_tool_decision(
            model="qwen3.5-4b",
            messages=[{"role": "user", "content": "analyze the codebase"}],
            agent_id="coder",
            allowed_tools=["filesystem", "final"],
        )

    assert result is not None
    assert len(captured) == 2, "malformed output must trigger exactly one retry"
    retry_messages = captured[1]
    retry_prompt = "\n".join(str(m.get("content", "")) for m in retry_messages if m.get("role") == "user")
    assert "exactly one valid JSON object" in retry_prompt
    assert "No code fences" in retry_prompt
    assert "DO NOT use XML tags" in retry_prompt
    assert "Use an 'action' key" in retry_prompt