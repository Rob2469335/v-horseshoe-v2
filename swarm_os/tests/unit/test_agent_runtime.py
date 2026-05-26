import pytest
from swarm_os.agent_runtime import AgentRuntime
from swarm_os.capabilities.models import ChatSearchRequest

pytestmark = pytest.mark.anyio

def test_agent_runtime_lists_tools():
    runtime = AgentRuntime()
    tools = runtime.list_tools()
    assert "chat_search" in tools
    assert "upwork_analyzer" in tools

async def test_agent_runtime_call_tool():
    runtime = AgentRuntime()
    req = ChatSearchRequest(query="test agent")
    response = await runtime.call_tool("chat_search", req)
    assert response.status == "success"

async def test_agent_runtime_disable_tool():
    runtime = AgentRuntime()
    runtime.disable_tool("chat_search")
    with pytest.raises(RuntimeError):
        await runtime.call_tool("chat_search", ChatSearchRequest(query="test"))

async def test_agent_runtime_clears_tool_cache():
    runtime = AgentRuntime()
    await runtime.call_tool("chat_search", ChatSearchRequest(query="test1"), cache_key="k1")
    await runtime.call_tool("chat_search", ChatSearchRequest(query="test2"), cache_key="k2")
    assert runtime.get_tool_cache_size() == 2
    runtime.clear_tool_cache()
    assert runtime.get_tool_cache_size() == 0
