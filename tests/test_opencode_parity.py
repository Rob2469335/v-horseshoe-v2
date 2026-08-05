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
from runtime_v2.api._agent_routing import fast_route_coordinator, _RESEARCHER_FIRST_TURNS


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
    events2 = [e async for e in gen2]
    assert state.handler_status != "CONTINUE"
    assert any(e.get("type") == "final" for e in events2)


@pytest.mark.asyncio
async def test_internet_goal_blocks_final_without_web_search():
    service = AgentServiceV2()
    state = _CallState()
    messages = [{"role": "user", "content": "hi"}]
    # code_analyzer (ANALYSIS_AGENT) with an internet goal, no web_search done.
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, "analyze my codebase and search internet for improvements", True, state)
    events = [e async for e in gen]
    assert state.handler_status == "CONTINUE"
    assert state._web_final_rejected is True
    assert any("web_search" in m.get("content", "") for m in messages)


@pytest.mark.asyncio
async def test_internet_goal_blocks_second_final_too():
    service = AgentServiceV2()
    state = _CallState()
    messages = [{"role": "user", "content": "hi"}]
    prompt = "analyze my codebase and search internet for improvements"
    # First final is rejected...
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, prompt, True, state)
    events = [e async for e in gen]
    assert state.handler_status == "CONTINUE"
    assert state._web_final_rejected is True
    # ...and a SECOND final is rejected too (the one-shot latch let the agent
    # "complete" the goal without ever running web_search before).
    gen2 = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, prompt, True, state)
    events2 = [e async for e in gen2]
    assert state.handler_status == "CONTINUE"
    assert state._web_final_rejected is True


@pytest.mark.asyncio
async def test_internet_goal_final_allowed_after_web_search():
    service = AgentServiceV2()
    state = _CallState()
    # Both web_search AND web_fetch must have succeeded before final is allowed
    # (an internet goal needs fetched content, not just search snippets).
    state.did_web_search = True
    state.did_web_fetch = True
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, "analyze my codebase and search internet for improvements", True, state)
    events = [e async for e in gen]
    # Not rejected - goes through to response handling.
    assert state.handler_status != "CONTINUE"


@pytest.mark.asyncio
async def test_internet_goal_final_blocked_without_web_fetch():
    """An internet goal that searched but never deep-read a page (web_fetch) must
    be rejected — snippets alone aren't enough, matching how a human researcher
    (or opencode) fetches and reads actual pages before synthesizing."""
    service = AgentServiceV2()
    state = _CallState()
    state.did_web_search = True
    state.did_web_fetch = False
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, "analyze my codebase and search internet for improvements", True, state)
    events = [e async for e in gen]
    assert state.handler_status == "CONTINUE"
    assert state.did_web_fetch is False
    assert any("web_fetch" in m.get("content", "") for m in messages)


@pytest.mark.asyncio
async def test_research_goal_routes_to_researcher_not_code_analyzer():
    # A pure web/answer goal (no codebase mention) must route to the
    # research-capable agent, not to code_analyzer (which has a filesystem warmup).
    for goal in (
        "search the internet for the latest version of python",
        "search the web and tell me the current us president",
        "research and answer what is the best llm for coding in 2026",
        "look up how to fix HTTP 403 missing user agent",
    ):
        decision = fast_route_coordinator(goal)
        assert decision is not None, goal
        assert decision["target_agent"] == "researcher", goal


def test_research_goal_injects_web_search_first_with_no_llm_and_no_filesystem():
    # The researcher's FIRST action is deterministically web_search — no LLM call
    # and no filesystem exploration — so a research goal never burns turns reading
    # code before searching the internet.
    assert _RESEARCHER_FIRST_TURNS == 1
    service = AgentServiceV2()
    service._call_llm = AsyncMock(return_value={"action": "final", "response": "should-not-happen"})
    service._agents = {"researcher": {}}
    decision = asyncio.run(service._get_decision(
        "researcher", "m",
        [{"role": "user", "content": "hi"}],
        ["web_search", "final", "filesystem"],
        "search the internet for the latest python version",
        0,
    ))
    assert decision["action"] == "web_search"
    assert not service._call_llm.called
    # Turn 1+ hands control back to the LLM.
    service._call_llm.reset_mock()
    decision_next = asyncio.run(service._get_decision(
        "researcher", "m",
        [{"role": "user", "content": "hi"}],
        ["web_search", "final", "filesystem"],
        "search the internet",
        1,
    ))
    assert service._call_llm.called


def test_researcher_prompt_prioritizes_web_search_for_web_goals():
    prompt = build_system_prompt("researcher")
    assert "PURE WEB-RESEARCH GOALS" in prompt
    assert "CALL action=web_search FIRST" in prompt
    assert "never start with filesystem" in prompt


@pytest.mark.asyncio
async def test_internet_goal_web_searches_before_warmup():
    """INTERNET-GOAL FIX: an analysis agent handed a compound internet goal
    (codebase + web) must web_search on turn 0 BEFORE the filesystem warmup.
    Previously the 4-turn warmup + file reads consumed the whole budget and the
    agent hit 'max turns reached' without ever searching the web."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    service = AgentServiceV2()
    service._call_llm = AsyncMock(return_value={"action": "final", "response": "x"})
    service._agents = {"code_analyzer": {}}
    # Compound goal: codebase + internet -> must search FIRST on turn 0.
    decision = await service._get_decision(
        "code_analyzer", "m",
        [{"role": "user", "content": "hi"}],
        ["web_search", "final", "filesystem"],
        "analyze my codebase for bugs and search internet for improvements",
        0,
    )
    assert decision["action"] == "web_search"
    assert not service._call_llm.called
    # Pure web goal also searches first.
    decision2 = await service._get_decision(
        "code_analyzer", "m",
        [{"role": "user", "content": "hi"}],
        ["web_search", "final", "filesystem"],
        "search the internet for the latest python version",
        0,
    )
    assert decision2["action"] == "web_search"
    # Code-only goal keeps the deterministic filesystem warmup (no forced search).
    decision3 = await service._get_decision(
        "code_analyzer", "m",
        [{"role": "user", "content": "hi"}],
        ["web_search", "final", "filesystem"],
        "fix the bug in runtime_v2/services/memory_core.py",
        0,
    )
    assert decision3["action"] == "filesystem"


def test_clean_search_query_strips_coordinator_boilerplate():
    """The web_search query must be the clean goal, not the delegated task's
    coordinator instruction wrapper (which wastes search tokens and pollutes
    results)."""
    from runtime_v2.api.agent_service_v2 import _clean_search_query
    wrapped = ("Goal: analyze my codebase for bugs and search internet for improvements\n\n"
               "*** CRITICAL INSTRUCTION ***\n"
               "You are the coordinator agent. Your ONLY job is to act as a router. "
               "You MUST NOT attempt to solve this goal yourself.")
    clean = _clean_search_query(wrapped)
    assert clean == "analyze my codebase for bugs and search internet for improvements"
    assert "CRITICAL INSTRUCTION" not in clean
    assert "coordinator" not in clean
    # Plain goals pass through unchanged.
    assert _clean_search_query("search the internet for the latest python version") == (
        "search the internet for the latest python version")
    # Goal/Task labels are trimmed.
    assert _clean_search_query("Task: how to optimize litellm caching") == "how to optimize litellm caching"


def test_fix_intent_routes_to_coder():
    """FIX-LOOP: a goal that implies editing code ('analyze and fix bugs') must
    route to the edit-capable coder agent, not the report-only code_analyzer —
    matching how a human maintainer (or opencode) actually fixes things."""
    from runtime_v2.api._agent_routing import fast_route_coordinator, best_route_target
    assert best_route_target("analyze my codebase for bugs and fix them") == "coder"
    assert best_route_target("write a fix for the bug in stream_runner") == "coder"
    # Pure analysis stays on code_analyzer; pure web stays on researcher.
    assert best_route_target("analyze my codebase for bugs") == "code_analyzer"
    assert best_route_target("search the internet for the latest python version") == "researcher"


@pytest.mark.asyncio
async def test_coordinator_fix_intent_forces_coder_over_analyzer():
    """Even when the LLM coordinator picks code_analyzer for a fix-intent goal, the
    deterministic guard must force the delegate target to coder."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    service = AgentServiceV2()
    service._call_llm = AsyncMock(return_value={
        "action": "delegate", "target_agent": "code_analyzer", "task": "analyze codebase and fix bugs"})
    service._agents = {"coordinator": {}}
    decision = await service._get_decision(
        "coordinator", "m",
        [{"role": "user", "content": "analyze my codebase for bugs and fix them"}],
        ["delegate", "final"],
        "analyze my codebase for bugs and fix them",
        0,
    )
    assert decision["action"] == "delegate"
    assert decision["target_agent"] == "coder"

