import pytest

def _svc():
    from runtime_v2.api import agent_service_v2 as agent
    return agent.AgentServiceV2.__new__(agent.AgentServiceV2)

def _svc_state():
    from runtime_v2.api._agent_state import _CallState
    return _CallState()

@pytest.mark.asyncio
async def test_min_read_budget_strips_final(monkeypatch):
    monkeypatch.setenv("SWARM_MIN_FS_READS", "3")
    svc = _svc()
    state = _svc_state()
    state._filesystem_reads = 1
    state.agent_id = "code_analyzer"

    async def _mock_call(model, messages, agent_id, allowed_tools):
        # Just return the allowed_tools so we can inspect them
        return {"action": "dummy", "allowed": allowed_tools}

    monkeypatch.setattr(svc, "_call_llm", _mock_call)

    # It's an analysis agent, goal matches bug hunt, reads < 3
    tools = ["filesystem", "final", "remember"]
    res = await svc._get_decision("code_analyzer", "dummy", [], tools, "analyze my codebase for bugs", 10, state=state)
    assert res is not None and "allowed" in res, f"res is {res}"
    assert "final" not in res["allowed"]

# Now if we hit the min reads
    state._filesystem_reads = 3
    tools = ["filesystem", "final", "remember"]
    res = await svc._get_decision("code_analyzer", "dummy", [], tools, "", 10, state=state)
    assert "final" in res["allowed"]


def test_deep_analysis_warmup_covers_architecture_layers():
    # Deep goals must read across the architecture layers deterministically,
    # not deep-dive a single subsystem the small decision model happens to pick.
    from runtime_v2.api import _agent_routing as ar

    def steps(deep: bool) -> int:
        n = 0
        for t in range(40):
            if ar.fast_start_for_agent("code_analyzer", t, deep=deep) is None:
                break
            n += 1
        return n

    routine, deep = steps(False), steps(True)
    assert deep >= 8, f"deep warmup should hit the 8-read floor, got {deep}"
    assert deep > routine, "deep warmup must cover more than the routine grounding"
    # the deep funnel must reach the other layers (CLI shell, API surface).
    paths = [
        ar.fast_start_for_agent("code_analyzer", t, deep=True).get("path")
        for t in range(deep)
    ]
    assert any("organism_console/cli.py" in p for p in paths)
    assert any("swarm_os/api/routes.py" in p for p in paths)
