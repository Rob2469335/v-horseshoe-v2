"""Read-budget cap: a code_analyzer that keeps reading must be nudged to stop
and synthesize a final, instead of burning the whole turn budget on reads."""

import pytest


def _svc():
    from runtime_v2.api import agent_service_v2 as agent

    return agent.AgentServiceV2.__new__(agent.AgentServiceV2)


@pytest.mark.asyncio
async def test_read_budget_nudge_fires_once(monkeypatch):
    # 2026-09-10: unbounded filesystem reads on the read-only codebase goal
    # exhausted MAX_TURNS with no final ("max turns reached"). Once the per-run
    # read budget is crossed, a ONE-TIME nudge must be appended to `messages`.
    # Revert-proof: pre-fix (no cap) `messages` never receives the nudge.
    import runtime_v2.services.tool_executor as te

    async def _fake_run(tool, payload, **kwargs):
        return {"ok": True, "content": "file body"}

    monkeypatch.setattr(te, "run", _fake_run)
    monkeypatch.setenv("SWARM_MAX_FS_READS", "2")

    svc = _svc()
    state = _svc_state()
    messages: list = []

    for _ in range(3):
        await svc._handle_tool(
            {"action": "filesystem", "operation": "read", "path": "runtime_v2/a.py"},
            "code_analyzer",
            messages,
            True,
            turn=1,
            consecutive_errors=0,
            state=state,
        )

    nudges = [
        m
        for m in messages
        if m.get("role") == "user" and "STOP reading" in str(m.get("content", ""))
    ]
    assert state._filesystem_reads == 3
    assert len(nudges) == 1  # fired exactly once, not every turn


@pytest.mark.asyncio
async def test_read_budget_nudge_not_sent_below_budget(monkeypatch):
    import runtime_v2.services.tool_executor as te

    async def _fake_run(tool, payload, **kwargs):
        return {"ok": True, "content": "file body"}

    monkeypatch.setattr(te, "run", _fake_run)
    monkeypatch.setenv("SWARM_MAX_FS_READS", "6")

    svc = _svc()
    state = _svc_state()
    messages: list = []
    await svc._handle_tool(
        {"action": "filesystem", "operation": "read", "path": "runtime_v2/a.py"},
        "code_analyzer",
        messages,
        True,
        turn=1,
        consecutive_errors=0,
        state=state,
    )
    assert not any("STOP reading" in str(m.get("content", "")) for m in messages)


def _svc_state():
    from runtime_v2.api.agent_service_v2 import _CallState

    return _CallState()


@pytest.mark.asyncio
async def test_forced_final_strips_filesystem_at_read_budget(monkeypatch):
    # 2026-09-10 (deeper fix): the soft nudge was IGNORED — the model kept
    # reading until max turns. Once the read budget is crossed, the decision
    # surface must be HARD-restricted to `final` (no filesystem), the
    # browser-use `_force_done_after_last_step` pattern. Revert-proof: pre-fix
    # the captured allowed_tools still contain "filesystem".
    monkeypatch.setenv("SWARM_MAX_FS_READS", "6")
    # Deep goal -> pin the deep cap to 6 so this stays an "at the budget" test.
    monkeypatch.setenv("SWARM_DEEP_FS_READS", "6")

    svc = _svc()
    state = _svc_state()
    state._filesystem_reads = 6  # at budget
    captured: dict = {}

    async def _fake_call_llm(model, messages, agent_id, allowed_tools):
        captured["tools"] = list(allowed_tools)
        captured["messages"] = list(messages)
        return {"action": "final", "response": "findings report"}

    monkeypatch.setattr(svc, "_call_llm", _fake_call_llm)

    decision = await svc._get_decision(
        "code_analyzer",
        "robs4b",
        [{"role": "user", "content": "analyze my codebase for bugs and upgrades"}],
        ["filesystem", "semantic_search", "final", "remember", "git", "system", "mcp"],
        "analyze my codebase for bugs and upgrades",
        turn=10,
        state=state,
    )

    assert decision["action"] == "final"
    # ALLOWLIST: every acting tool is gone (not just filesystem) — otherwise the
    # model keeps "acting" via git/system/mcp and never finalizes.
    assert "filesystem" not in captured["tools"]
    assert "semantic_search" not in captured["tools"]
    assert "git" not in captured["tools"]
    assert "system" not in captured["tools"]
    assert "mcp" not in captured["tools"]
    assert "final" in captured["tools"]
    assert state._forced_final is True
    # the model is told reading is over and final is mandatory
    assert any(
        "codebase-reading phase is OVER" in str(m.get("content", ""))
        for m in captured["messages"]
    )


@pytest.mark.asyncio
async def test_forced_final_not_triggered_below_budget(monkeypatch):
    monkeypatch.setenv("SWARM_MAX_FS_READS", "6")

    svc = _svc()
    state = _svc_state()
    state._filesystem_reads = 2  # below budget
    captured: dict = {}

    async def _fake_call_llm(model, messages, agent_id, allowed_tools):
        captured["tools"] = list(allowed_tools)
        return {"action": "filesystem", "operation": "read", "path": "x.py"}

    monkeypatch.setattr(svc, "_call_llm", _fake_call_llm)

    await svc._get_decision(
        "code_analyzer",
        "robs4b",
        [{"role": "user", "content": "analyze my codebase for bugs and upgrades"}],
        ["filesystem", "semantic_search", "final"],
        "analyze my codebase for bugs and upgrades",
        turn=10,
        state=state,
    )

    assert "filesystem" in captured["tools"]
    assert state._forced_final is False


@pytest.mark.asyncio
async def test_forced_final_only_once(monkeypatch):
    # The restriction must be set once, not re-triggered every turn (the state
    # flag already covered, but pin it so a refactor can't re-append the message).
    monkeypatch.setenv("SWARM_MAX_FS_READS", "6")

    svc = _svc()
    state = _svc_state()
    state._filesystem_reads = 6
    calls = {"n": 0}

    async def _fake_call_llm(model, messages, agent_id, allowed_tools):
        calls["n"] += 1
        return {"action": "final", "response": "ok"}

    monkeypatch.setattr(svc, "_call_llm", _fake_call_llm)
    msgs = [{"role": "user", "content": "goal"}]
    tools = ["filesystem", "final"]

    await svc._get_decision(
        "code_analyzer", "robs4b", msgs, list(tools), "goal", turn=5, state=state
    )
    await svc._get_decision(
        "code_analyzer", "robs4b", msgs, list(tools), "goal", turn=6, state=state
    )

    assert state._forced_final is True
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_forced_final_reapplies_tools_when_loop_set_flag(monkeypatch):
    # 2026-09-10: the loop guard sets state._forced_final=True and continues; the
    # NEXT _get_decision must strip filesystem even though the read count may be
    # below the budget (the observed 5-file read cycle). Revert-proof: without the
    # "state._forced_final and filesystem in allowed" clause, tools pass through.
    monkeypatch.setenv("SWARM_MAX_FS_READS", "99")  # budget never hit

    svc = _svc()
    state = _svc_state()
    state._filesystem_reads = 1
    state._forced_final = True  # set by the loop guard
    captured: dict = {}

    async def _fake_call_llm(model, messages, agent_id, allowed_tools):
        captured["tools"] = list(allowed_tools)
        return {"action": "final", "response": "findings"}

    monkeypatch.setattr(svc, "_call_llm", _fake_call_llm)

    await svc._get_decision(
        "code_analyzer",
        "robs4b",
        [{"role": "user", "content": "analyze my codebase for bugs and upgrades"}],
        ["filesystem", "semantic_search", "final"],
        "analyze my codebase for bugs and upgrades",
        turn=11,
        state=state,
    )

    assert "filesystem" not in captured["tools"]
    assert "semantic_search" not in captured["tools"]
    assert "final" in captured["tools"]


@pytest.mark.asyncio
async def test_non_analysis_agent_never_forced_final(monkeypatch):
    # A non-analysis agent (e.g. coder) must keep its full tool surface even at
    # high exploration counts — the forced-final is an analysis-agent safety only.
    monkeypatch.setenv("SWARM_MAX_FS_READS", "1")

    svc = _svc()
    state = _svc_state()
    state._filesystem_reads = 50
    captured: dict = {}

    async def _fake_call_llm(model, messages, agent_id, allowed_tools):
        captured["tools"] = list(allowed_tools)
        return {"action": "filesystem", "operation": "read", "path": "x.py"}

    monkeypatch.setattr(svc, "_call_llm", _fake_call_llm)

    await svc._get_decision(
        "coder",
        "robs4b",
        [{"role": "user", "content": "fix the bug"}],
        ["filesystem", "final"],
        "fix the bug",
        turn=3,
        state=state,
    )

    assert "filesystem" in captured["tools"]
    assert state._forced_final is False


def test_analysis_budget_scales_with_goal_depth(monkeypatch):
    # 2026-09-12: deep codebase-analysis goals get a larger funnel than routine
    # analysis goals (flat 6-read cap truncated a whole-repo audit to ~3 files).
    from runtime_v2.api.agent_service_v2 import _analysis_budget

    monkeypatch.delenv("SWARM_DEEP_MIN_FS_READS", raising=False)
    monkeypatch.delenv("SWARM_DEEP_FS_READS", raising=False)
    monkeypatch.delenv("SWARM_DEEP_MAX_TURNS", raising=False)

    min_r, max_r, max_t = _analysis_budget("analyze my codebase for bugs and upgrades")
    assert (min_r, max_r, max_t) == (8, 14, 24)

    r_min, r_max, r_t = _analysis_budget("summarize the log file")
    assert (r_min, r_max) == (3, 6)
    assert r_t == 12


@pytest.mark.asyncio
async def test_deep_goal_synthesizes_after_min_coverage(monkeypatch):
    # Deep goals: once the deterministic funnel has read >= min (default 8) and
    # the warmup is exhausted, the harness STOPS exploration and forces the final
    # (harness owns exploration; the model only synthesizes). Below min it keeps
    # exploring even though it is past the routine 6-read cap. Revert-proof:
    # pre-fix the deep goal was not forced-final until the 14-read cap.
    monkeypatch.setenv("SWARM_MAX_FS_READS", "6")
    monkeypatch.delenv("SWARM_DEEP_FS_READS", raising=False)
    monkeypatch.delenv("SWARM_DEEP_MIN_FS_READS", raising=False)

    svc = _svc()

    async def run(reads):
        st = _svc_state()
        st._filesystem_reads = reads
        cap: dict = {}

        async def _fake(model, messages, agent_id, allowed_tools):
            cap["tools"] = list(allowed_tools)
            return {"action": "final", "response": "ok"}

        monkeypatch.setattr(svc, "_call_llm", _fake)
        await svc._get_decision(
            "code_analyzer",
            "robs4b",
            [{"role": "user", "content": "analyze my codebase for bugs and upgrades"}],
            ["filesystem", "semantic_search", "final"],
            "analyze my codebase for bugs and upgrades",
            turn=9,  # past the deterministic warmup
            state=st,
        )
        return cap, st

    # below the deep min (7 < 8): still exploring, not forced-final
    cap7, st7 = await run(7)
    assert "filesystem" in cap7["tools"]
    assert st7._forced_final is False
    # at the deep min (8): forced-final — synthesize from what was read
    cap8, st8 = await run(8)
    assert "filesystem" not in cap8["tools"]
    assert st8._forced_final is True


@pytest.mark.asyncio
async def test_routine_goal_keeps_routine_read_budget(monkeypatch):
    # A non-deep analysis goal keeps the conservative 6-read cap.
    monkeypatch.setenv("SWARM_MAX_FS_READS", "6")
    monkeypatch.delenv("SWARM_DEEP_FS_READS", raising=False)

    svc = _svc()
    state = _svc_state()
    state._filesystem_reads = 6
    captured: dict = {}

    async def _fake_call_llm(model, messages, agent_id, allowed_tools):
        captured["tools"] = list(allowed_tools)
        return {"action": "final", "response": "ok"}

    monkeypatch.setattr(svc, "_call_llm", _fake_call_llm)

    await svc._get_decision(
        "code_analyzer",
        "robs4b",
        [{"role": "user", "content": "summarize the log file"}],
        ["filesystem", "final"],
        "summarize the log file",
        turn=6,
        state=state,
    )

    assert "filesystem" not in captured["tools"]
    assert state._forced_final is True
