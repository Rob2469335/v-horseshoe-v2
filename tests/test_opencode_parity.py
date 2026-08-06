"""Tests for the opencode-parity behaviors:

- Project map (AGENTS.md) is injected into analysis-agent system prompts
- Deterministic glob discovery finds real files (no path guessing)
- Read-before-write guard blocks patches on unseen files
- Todo tracking persists a working checklist across turns
- Verify-after-change rejects final until code edits are tested
"""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock

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
    async for _ in gen:
        pass
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
async def test_coder_fix_intent_final_blocked_without_code_change():
    """The `coder` is an EDIT agent: a fix-intent goal (write/implement/patch/
    modify/solve/repair) finalizing with NO file change restates the goal instead
    of doing it (the /upgrade autonomous loop failed 5/5 attempts with "No file
    changes detected"). Every such final must be rejected until a file is actually
    written or patched."""
    service = AgentServiceV2()
    state = _CallState()
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "analyze codebase for improvements"},
        "coder", "m", "p",
        messages, 0.0,
        "analyze the codebase and implement SOTA upgrades", True, state)
    async for _ in gen:
        pass
    assert state.handler_status == "CONTINUE"
    assert not state.did_code_change
    assert any("operation=write" in m.get("content", "") or "operation=patch" in m.get("content", "")
               for m in messages)


@pytest.mark.asyncio
async def test_coder_fix_intent_final_allowed_after_code_change():
    """Once the coder has actually modified a file (did_code_change), the
    edit-invariant guard no longer blocks the final."""
    service = AgentServiceV2()
    state = _CallState()
    state.did_code_change = True
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "coder", "m", "p",
        messages, 0.0, "implement the fix", True, state)
    async for _ in gen:
        pass
    assert state.handler_status != "CONTINUE"


@pytest.mark.asyncio
async def test_coder_non_fix_final_not_blocked_by_edit_invariant():
    """A non-fix-intent coder task (e.g. a pure explanation) is NOT forced to edit
    code — the edit-invariant only fires on fix-intent goals."""
    service = AgentServiceV2()
    state = _CallState()
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "explained"}, "coder", "m", "p",
        messages, 0.0, "explain what this function does", True, state)
    async for _ in gen:
        pass
    assert state.handler_status != "CONTINUE"


@pytest.mark.asyncio
async def test_internet_goal_blocks_final_without_web_search():
    service = AgentServiceV2()
    state = _CallState()
    messages = [{"role": "user", "content": "hi"}]
    # code_analyzer (ANALYSIS_AGENT) with an internet goal, no web_search done.
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, "analyze my codebase and search internet for improvements", True, state)
    async for _ in gen:
        pass
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
    async for _ in gen:
        pass
    assert state.handler_status == "CONTINUE"
    assert state._web_final_rejected is True
    # ...and a SECOND final is rejected too (the one-shot latch let the agent
    # "complete" the goal without ever running web_search before).
    gen2 = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, prompt, True, state)
    async for _ in gen2:
        pass
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
    async for _ in gen:
        pass
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
    async for _ in gen:
        pass
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
    asyncio.run(service._get_decision(
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


@pytest.mark.asyncio
async def test_coder_not_forced_into_web_search_on_turn_0():
    """The turn-0 web_search injection is for REPORT agents (ANALYSIS_AGENTS)
    only. The `coder` is an EDIT agent — forcing web_search at turn 0 derailed it
    into pure research so it never edited (the /upgrade dead-loop). coder instead
    gets the deterministic FILESYSTEM warmup (read AGENTS.md, glob runtime_v2) so
    it grounds in the real codebase before deciding, then the LLM takes over."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    service = AgentServiceV2()
    service._call_llm = AsyncMock(return_value={"action": "final", "response": "x"})
    service._agents = {"coder": {}}
    decision = await service._get_decision(
        "coder", "m",
        [{"role": "user", "content": "hi"}],
        ["web_search", "web_fetch", "filesystem", "final"],
        "analyze the codebase and implement SOTA upgrades via web search",
        0,
    )
    # NOT the injected web_search — coder grounds with the filesystem warmup
    # (read AGENTS.md), which prevents the research-first derail.
    assert decision["action"] == "filesystem"
    assert decision.get("operation") == "read"
    assert not service._call_llm.called
    # Turn past the warmup hands control back to the LLM.
    service._call_llm.reset_mock()
    await service._get_decision(
        "coder", "m",
        [{"role": "user", "content": "hi"}],
        ["web_search", "web_fetch", "filesystem", "final"],
        "analyze the codebase and implement SOTA upgrades via web search",
        3,
    )
    assert service._call_llm.called


@pytest.mark.asyncio
async def test_coordinator_routes_after_ask_user_answer_no_requery():
    """POST-ASK_USER GUARD: once the user has answered an ask_user (the CLI feeds
    it back as an `Observation:` history turn), the stateless coordinator must
    DELEGATE on that answer — never re-ask the same question. Regression for the
    /upgrade infinite 'which upgrades?' loop where every answer was ignored."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    service = AgentServiceV2()
    service._call_llm = AsyncMock(return_value={"action": "ask_user", "question": "which upgrades?"})
    service._agents = {"coordinator": {}}
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "analyze codebase and implement SOTA upgrades via web search"},
        {"role": "assistant", "content": "which upgrades?"},
        {"role": "user", "content": 'Observation: {"answer": "all"}'},
    ]
    decision = await service._get_decision(
        "coordinator", "m", messages, ["delegate", "ask_user", "final"], "", 0)
    assert decision["action"] == "delegate"
    # "all"/"everything" maps to no specific route → falls back to the goal's
    # route, which is compound (implement + web search) → executor. Crucially
    # NOT ask_user and not a bare coder (which would double-bind).
    assert decision["target_agent"] == "executor"
    assert not service._call_llm.called


@pytest.mark.asyncio
async def test_coordinator_still_asks_without_answer_in_history():
    """The guard must not suppress a legitimate first ask_user — with no
    Observation answer in history, the coordinator falls through to the LLM."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    service = AgentServiceV2()
    service._call_llm = AsyncMock(return_value={"action": "ask_user", "question": "which upgrades?"})
    service._agents = {"coordinator": {}}
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "analyze codebase and implement SOTA upgrades via web search"},
    ]
    decision = await service._get_decision(
        "coordinator", "m", messages, ["delegate", "ask_user", "final"], "", 0)
    assert decision["action"] == "ask_user"
    assert service._call_llm.called


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
    from runtime_v2.api._agent_routing import best_route_target
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


def test_compound_goal_routes_to_executor():
    """A goal needing BOTH internet research AND code changes (the /upgrade case)
    routes to `executor` — the orchestrator that chains researcher -> coder ->
    tool-runner — NOT collapsing onto coder (which loop-tripped trying to satisfy
    the edit + web_search + web_fetch obligations inside MAX_TURNS)."""
    for goal in (
        "research SOTA upgrades via web search and implement them in the codebase",
        "analyze the codebase, search the internet for improvements, and fix them",
        "find best practices online and implement the improvements",
    ):
        decision = fast_route_coordinator(goal)
        assert decision is not None, goal
        assert decision["target_agent"] == "executor", goal


def test_compound_goal_does_not_hijack_single_intent_goals():
    """Pure fix and pure research goals still route to their specialist agents."""
    assert fast_route_coordinator("write a new function in stream_runner")["target_agent"] == "coder"
    assert fast_route_coordinator("search the internet for the latest python version")["target_agent"] == "researcher"
    assert fast_route_coordinator("run the tests")["target_agent"] == "tool-runner"


def test_tool_runner_is_reachable():
    """tool-runner had no keyword route in _ROUTES — the only agent with zero
    reachability. Test-running/verification goals must now reach it."""
    for goal in ("run the tests", "run tests", "run the test suite", "check the tests"):
        decision = fast_route_coordinator(goal)
        assert decision is not None, goal
        assert decision["target_agent"] == "tool-runner", goal


def test_best_route_target_compound_returns_executor():
    from runtime_v2.api._agent_routing import best_route_target
    assert best_route_target("research SOTA upgrades via web search and implement them") == "executor"
    assert best_route_target("analyze my codebase for bugs and fix them") == "coder"
    assert best_route_target("run the tests") == "tool-runner"


@pytest.mark.asyncio
async def test_executor_compound_goal_delegates_researcher_first():
    """The executor orchestrator, handed a compound internet+fix goal, must
    deterministically delegate research to `researcher` on turn 0 — so the code
    changes downstream are grounded in real findings (research-first chaining)."""
    from runtime_v2.api.agent_service_v2 import AgentServiceV2
    service = AgentServiceV2()
    service._call_llm = AsyncMock(return_value={"action": "final", "response": "x"})
    service._agents = {"executor": {}}
    decision = await service._get_decision(
        "executor", "m",
        [{"role": "user", "content": "hi"}],
        ["delegate", "sandbox_repl", "final"],
        "research SOTA upgrades via web search and implement them in the codebase",
        0,
    )
    assert decision["action"] == "delegate"
    assert decision["target_agent"] == "researcher"
    assert not service._call_llm.called
    # Non-internet executor task does NOT force a research delegate.
    service._call_llm.reset_mock()
    await service._get_decision(
        "executor", "m",
        [{"role": "user", "content": "hi"}],
        ["delegate", "sandbox_repl", "final"],
        "run this script and report the output",
        0,
    )
    assert service._call_llm.called


@pytest.mark.asyncio
async def test_coder_fix_deliverable_skips_internet_final_guard():
    """FIX-DELIVERABLE RELAXATION: once coder has actually edited a file on a
    fix-intent goal, the edit IS the deliverable — the internet-goal guards
    (web_search + web_fetch) must NOT reject the final. Requiring research on top
    of a completed edit is the double-bind that loop-tripped the /upgrade cycle."""
    service = AgentServiceV2()
    state = _CallState()
    state.did_code_change = True
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "implemented"}, "coder", "m", "p",
        messages, 0.0, "analyze the codebase and implement SOTA upgrades via web search", True, state)
    async for _ in gen:
        pass
    # Not rejected by the fix-intent invariant (code changed) NOR the internet
    # guards (fix deliverable) — goes through to response handling.
    assert state.handler_status != "CONTINUE"
    assert state.handler_status != "ABORT"


@pytest.mark.asyncio
async def test_coder_internet_guard_still_applies_before_edit():
    """The relaxation only fires AFTER coder edits. A fix-intent internet goal with
    NO code change is still rejected — coder cannot skip research AND skip editing."""
    service = AgentServiceV2()
    state = _CallState()
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "no edit"}, "coder", "m", "p",
        messages, 0.0, "analyze the codebase and implement SOTA upgrades via web search", True, state)
    async for _ in gen:
        pass
    assert state.handler_status == "CONTINUE"
    assert any("operation=write" in m.get("content", "") or "operation=patch" in m.get("content", "")
               for m in messages)


@pytest.mark.asyncio
async def test_report_agent_internet_guard_unaffected_by_fix_deliverable():
    """The relaxation is coder-only. A report agent (code_analyzer) with an
    internet goal still must web_search — even if the goal text has fix verbs."""
    service = AgentServiceV2()
    state = _CallState()
    state.did_code_change = True  # would be meaningless for code_analyzer
    messages = [{"role": "user", "content": "hi"}]
    gen = service._handle_final(
        {"action": "final", "response": "done"}, "code_analyzer", "m", "p",
        messages, 0.0, "analyze my codebase and search internet for improvements", True, state)
    async for _ in gen:
        pass
    assert state.handler_status == "CONTINUE"
    assert any("web_search" in m.get("content", "") for m in messages)

