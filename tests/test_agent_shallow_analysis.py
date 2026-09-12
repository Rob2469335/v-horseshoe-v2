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
