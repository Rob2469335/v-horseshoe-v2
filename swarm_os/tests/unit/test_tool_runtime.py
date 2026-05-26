import pytest
from swarm_os.tool_runtime import CapabilityToolExecutor
from swarm_os.capabilities.models import ChatSearchRequest, UpworkAnalysisRequest, VSCodeAutomationRequest

pytestmark = pytest.mark.anyio

async def test_tool_executor_lists_capabilities():
    executor = CapabilityToolExecutor()
    caps = executor.get_capabilities()
    assert "chat_search" in caps
    assert "upwork_analyzer" in caps
    assert "vscode_automation" in caps

async def test_tool_executor_chat_search():
    executor = CapabilityToolExecutor()
    req = ChatSearchRequest(query="system setup")
    response = await executor.execute_tool("chat_search", req)
    assert response.status == "success"

async def test_tool_executor_caches_results():
    executor = CapabilityToolExecutor()
    req = ChatSearchRequest(query="test cache")
    result1 = await executor.execute_tool("chat_search", req, cache_key="test_key")
    result2 = await executor.execute_tool("chat_search", req, cache_key="test_key")
    assert result1 is result2
    assert executor.cache_size() == 1

async def test_tool_executor_clears_cache():
    executor = CapabilityToolExecutor()
    req = ChatSearchRequest(query="test")
    await executor.execute_tool("chat_search", req, cache_key="key1")
    assert executor.cache_size() == 1
    executor.clear_cache()
    assert executor.cache_size() == 0
