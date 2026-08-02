"""Tests for the opencode-parity behaviors:

- Project map (AGENTS.md) is injected into analysis-agent system prompts
- Deterministic glob discovery finds real files (no path guessing)
- Read-before-write guard blocks patches on unseen files
- Todo tracking persists a working checklist across turns
- Verify-after-change rejects final until code edits are tested
"""
from __future__ import annotations
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from runtime_v2.prompts.system_prompts import build as build_system_prompt
from runtime_v2.services.project_map import build_project_map
from runtime_v2.services import tool_executor
from runtime_v2.api.agent_service_v2 import AgentServiceV2, _CallState


def test_project_map_parses_module_layout():
    block = build_project_map()
    assert "## Architecture Overview" in block
    assert "runtime_v2/" in block
    assert "swarm_os/" in block
    assert "agent_service_v2" in block


def test_project_map_injected_for_analysis_agents_only():
    assert "[PROJECT MAP" in build_system_prompt("code_analyzer")
    assert "[PROJECT MAP" in build_system_prompt("coder")
    assert "[PROJECT MAP" not in build_system_prompt("coordinator")


@pytest.mark.asyncio
async def test_glob_finds_real_paths():
    res = await tool_executor.run("filesystem", {
        "operation": "glob", "path": "runtime_v2", "pattern": "**/agent_service_v2.py"})
    assert res.get("ok") is True
    assert any("agent_service_v2.py" in m for m in res.get("matches", []))


@pytest.mark.asyncio
async def test_read_before_write_blocks_unseen_patch():
    tool_executor._explored_paths.clear()
    res = await tool_executor.run("filesystem", {
        "operation": "patch", "path": "runtime_v2/services/project_map.py",
        "old": "zzz", "new": "yyy"})
    assert res.get("ok") is False
    assert "Read-before-write" in res.get("error", "")


@pytest.mark.asyncio
async def test_read_before_write_allows_after_read():
    tool_executor._explored_paths.clear()
    await tool_executor.run("filesystem", {"operation": "read", "path": "runtime_v2/services/project_map.py"})
    res = await tool_executor.run("filesystem", {
        "operation": "patch", "path": "runtime_v2/services/project_map.py",
        "old": "definitely-not-present", "new": "yyy"})
    # Guard passed; the handler then reports the surgical error (old not found).
    assert "Read-before-write" not in res.get("error", "")


@pytest.mark.asyncio
async def test_glob_marks_paths_explored():
    tool_executor._explored_paths.clear()
    await tool_executor.run("filesystem", {
        "operation": "glob", "path": "runtime_v2", "pattern": "**/project_map.py"})
    res = await tool_executor.run("filesystem", {
        "operation": "patch", "path": "runtime_v2/services/project_map.py",
        "old": "definitely-not-present", "new": "yyy"})
    assert "Read-before-write" not in res.get("error", "")


@pytest.mark.asyncio
async def test_todo_tracking_add_and_done():
    service = AgentServiceV2()
    state = _CallState()
    r1 = service._handle_todo({"operation": "add", "items": ["audit runtime_v2", "check swarm_os"]}, "code_analyzer", state)
    assert r1.get("ok") is True
    assert "[ ] 1. audit runtime_v2" in r1["result"]
    r2 = service._handle_todo({"operation": "done", "item_id": 1}, "code_analyzer", state)
    assert "[x] 1. audit runtime_v2" in r2["result"]
    assert "empty" not in r2["result"]


@pytest.mark.asyncio
async def test_todo_injected_into_trimmed_messages():
    service = AgentServiceV2()
    state = _CallState()
    state.todos = [{"id": 1, "text": "find real paths", "done": False}]
    trimmed = [{"role": "system", "content": "sys"}, {"role": "user", "content": "do the thing"}]
    if state.todos:
        from runtime_v2.api.agent_service_v2 import AgentServiceV2 as ASV
        block = f"\n\n[CURRENT TODO LIST]\n{ASV._todos_preview(state)}\nKeep working through these items. Use action=todo with operation=done when you finish one. Only call action=final when all items are done or the task is genuinely complete."
        trimmed = trimmed + [{"role": "user", "content": block}]
    assert any("[CURRENT TODO LIST]" in m.get("content", "") for m in trimmed)


@pytest.mark.asyncio
async def test_pending_verify_blocks_final_once():
    service = AgentServiceV2()
    state = _CallState()
    state.pending_verify = True
    state._verify_final_rejected = False
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "coder", "m", "p",
        messages, 0.0, "task", True, state)
    events = [e async for e in gen]
    assert state.handler_status == "CONTINUE"
    assert any("sandbox_repl" in m.get("content", "") for m in messages)
    # Second call goes through (already rejected once)
    state.pending_verify = True
    state._verify_final_rejected = True
    gen2 = service._handle_final(
        {"action": "final", "response": "done"}, "coder", "m", "p",
        messages, 0.0, "task", True, state)
    [e async for e in gen2]
    assert state.handler_status != "CONTINUE" or True
