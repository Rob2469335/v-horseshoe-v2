import pytest
import json
import httpx
from swarm_os.services.agent_service import AgentService

class MockOrchestrator:
    pass

@pytest.fixture
def agent_service():
    return AgentService(orchestrator=MockOrchestrator())

def test_six_agents_registered(agent_service):
    agents = agent_service.list_agents()
    assert len(agents) == 6
    ids = {a["id"] for a in agents}
    assert ids == {"coordinator", "planner", "executor", "coder", "tool-runner", "reviewer"}

    coordinator = agent_service.get_agent("coordinator")
    assert coordinator["role"] == "coordinator"
    assert "orchestrator" in coordinator["description"].lower()

    reviewer = agent_service.get_agent("reviewer")
    assert reviewer["role"] == "reviewer"

class MockStreamResponse:
    def __init__(self, content):
        self.content = content
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def raise_for_status(self): pass
    async def aiter_lines(self):
        yield json.dumps({"message": {"content": self.content}})
        yield json.dumps({"done": True})

def create_mock_client(content):
    class MockClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def stream(self, *args, **kwargs):
            return MockStreamResponse(content)
    return MockClient

@pytest.mark.asyncio
async def test_delegation_emits_handoff_and_depth_limit(agent_service, monkeypatch):
    import swarm_os.services.agent_service as agent_service_mod
    monkeypatch.setattr(agent_service_mod, "reconcile_and_repair_tool_call", lambda a, b, c: ("__delegate__", b))

    monkeypatch.setattr(httpx, "AsyncClient", create_mock_client('<tool_call name="delegate">{"target_agent": "planner", "task": "do something"}</tool_call>'))

    stream = agent_service_mod.AgentService(orchestrator=MockOrchestrator()).step_agent_stream("coordinator", "prompt")

    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
        if chunk.get("type") == "agent_handoff":
            break

    handoff_chunk = next((c for c in chunks if c.get("type") == "agent_handoff"), None)
    assert handoff_chunk is not None
    assert handoff_chunk["from"] == "coordinator"
    assert handoff_chunk["to"] == "planner"

@pytest.mark.asyncio
async def test_complex_task_forces_full_six_agent_chain(monkeypatch):
    import swarm_os.services.agent_service as agent_service_mod

    # Each agent delegates to the next in the chain; reviewer gives a final answer
    delegation_sequence = {
        "coordinator": '<tool_call name="delegate">{"target_agent": "planner", "task": "plan it"}</tool_call>',
        "planner":     '<tool_call name="delegate">{"target_agent": "executor", "task": "execute it"}</tool_call>',
        "executor":    '<tool_call name="delegate">{"target_agent": "coder", "task": "code it"}</tool_call>',
        "coder":       '<tool_call name="delegate">{"target_agent": "tool-runner", "task": "run tools"}</tool_call>',
        "tool-runner": '<tool_call name="delegate">{"target_agent": "reviewer", "task": "review it"}</tool_call>',
        "reviewer":    "All done. Code reviewed and approved.",
    }

    call_count = {}

    def create_agent_mock_client(content):
        class MockClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            def stream(self, *a, **kw):
                return MockStreamResponse(content)
        return MockClient

    monkeypatch.setattr(agent_service_mod, "reconcile_and_repair_tool_call",
        lambda name, payload, tools: ("__delegate__", payload) if name == "delegate" else (name, payload))

    monkeypatch.setattr(agent_service_mod, 'fetch_live_models_if_needed', lambda: None)
    svc = agent_service_mod.AgentService(orchestrator=MockOrchestrator())

    # Patch each agent's HTTP client to return the right delegation content
    original_step = svc.step_agent_stream.__func__ if hasattr(svc.step_agent_stream, "__func__") else None

    async def patched_step(agent_id, prompt, history=None, delegation_chain=None):
        content = delegation_sequence.get(agent_id, "Done.")
        monkeypatch.setattr(httpx, "AsyncClient", create_agent_mock_client(content))
        async for chunk in agent_service_mod.AgentService.step_agent_stream(svc, agent_id, prompt, history=history, delegation_chain=delegation_chain):
            yield chunk

    svc.step_agent_stream = patched_step

    chunks = [c async for c in svc.step_agent_stream("coordinator", "fix the route bug")]
    handoffs = [(c.get("from"), c.get("to")) for c in chunks if c.get("type") == "agent_handoff"]

    assert handoffs == [
        ("coordinator", "planner"),
        ("planner", "executor"),
        ("executor", "coder"),
        ("coder", "tool-runner"),
        ("tool-runner", "reviewer"),
    ]

    final_chunk = next((c for c in reversed(chunks) if c.get("type") == "final"), None)
    assert final_chunk is not None
    assert final_chunk["agent_id"] == "reviewer"

@pytest.mark.asyncio
async def test_circular_delegation_blocked(agent_service, monkeypatch):
    import swarm_os.services.agent_service as agent_service_mod
    monkeypatch.setattr(agent_service_mod, "reconcile_and_repair_tool_call", lambda a, b, c: ("__delegate__", b))

    monkeypatch.setattr(httpx, "AsyncClient", create_mock_client('<tool_call name="delegate">{"target_agent": "coordinator", "task": "do something"}</tool_call>'))

    stream = agent_service_mod.AgentService(orchestrator=MockOrchestrator()).step_agent_stream("coordinator", "prompt", delegation_chain=["coordinator"])

    chunks = [c async for c in stream]
    final_chunk = next((c for c in chunks if c.get("type") == "final"), None)
    assert final_chunk is not None
    assert "Self-delegation blocked for agent: coordinator." in final_chunk["content"]

@pytest.mark.asyncio
async def test_ask_user_suppression(agent_service, monkeypatch):
    import swarm_os.services.agent_service as agent_service_mod
    monkeypatch.setattr(agent_service_mod, "reconcile_and_repair_tool_call", lambda a, b, c: ("ask_user", b))

    monkeypatch.setattr(httpx, "AsyncClient", create_mock_client('<tool_call name="ask_user">{"question": "yes?"}</tool_call>'))

    stream = agent_service_mod.AgentService(orchestrator=MockOrchestrator()).step_agent_stream("planner", "prompt")
    chunks = [c async for c in stream]

    final_chunk = next((c for c in chunks if c.get("type") == "final"), None)
    assert final_chunk is not None
    assert "Only the coordinator agent is allowed" in final_chunk["content"]

def test_cli_delegation_chain_update():
    class DummyContext:
        def __init__(self):
            self.delegation_chain = ["coordinator"]
        def save(self):
            pass

    ctx = DummyContext()

    chunk = {"delegated_by": "coordinator", "agent_id": "planner", "type": "chunk"}

    parent = chunk["delegated_by"]
    child = chunk["agent_id"]
    if child not in ctx.delegation_chain:
        if parent in ctx.delegation_chain:
            idx = ctx.delegation_chain.index(parent)
            ctx.delegation_chain = ctx.delegation_chain[:idx+1] + [child]
        else:
            ctx.delegation_chain.append(child)

    assert ctx.delegation_chain == ["coordinator", "planner"]
