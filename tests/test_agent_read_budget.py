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
        ["filesystem", "semantic_search", "final", "remember"],
        "analyze my codebase for bugs and upgrades",
        turn=5,
        state=state,
    )

    assert decision["action"] == "final"
    assert "filesystem" not in captured["tools"]
    assert "semantic_search" not in captured["tools"]
    assert "final" in captured["tools"]
    assert state._forced_final is True
    # the model is told reading is over and final is mandatory
    assert any(
        "filesystem tool has been disabled" in str(m.get("content", ""))
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
        turn=5,
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