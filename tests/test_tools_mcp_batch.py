import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from runtime_v2.services.tool_executor import run
from swarm_os.services.approval_registry import agent_tool_policy, ALLOW, CONFIRM
from swarm_os.lib.mcp.mcp_client import ExternalMCPClientManager

@pytest.fixture
def mock_mcp_manager():
    manager = MagicMock(spec=ExternalMCPClientManager)
    return manager

@pytest.mark.asyncio
async def test_mcp_batch_concurrent_execution(mock_mcp_manager):
    """Verify that multiple MCP tools execute concurrently in mcp_batch."""
    execution_record = []
    
    async def mock_call_tool(server_name, mcp_tool, arguments):
        execution_record.append(f"start {mcp_tool}")
        await asyncio.sleep(0.1)
        execution_record.append(f"end {mcp_tool}")
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text=f"result of {mcp_tool}")]
        return mock_result
        
    mock_mcp_manager.call_tool = AsyncMock(side_effect=mock_call_tool)
    
    with patch("runtime_v2.services.tool_executor.get_mcp_manager", return_value=mock_mcp_manager), \
         patch("runtime_v2.services.tool_executor.agent_tool_policy", return_value=ALLOW):
        calls = [
            {"server": "test", "tool": "tool1", "arguments": {}},
            {"server": "test", "tool": "tool2", "arguments": {}}
        ]
        
        start_time = asyncio.get_event_loop().time()
        result = await run("mcp_batch", {"calls": calls})
        elapsed = asyncio.get_event_loop().time() - start_time
        
        out_str = str(result)
        assert "result of tool1" in out_str
        assert "result of tool2" in out_str
        
        # Time check
        assert elapsed < 0.15, f"Execution was not concurrent, took {elapsed}s"
        
        # Order check
        starts = [r for r in execution_record if r.startswith("start")]
        ends = [r for r in execution_record if r.startswith("end")]
        assert len(starts) == 2
        assert len(ends) == 2

@pytest.mark.asyncio
async def test_mcp_batch_partial_failure(mock_mcp_manager):
    """Verify that a failure in one batched call does not crash the whole batch."""
    async def mock_call_tool(server_name, mcp_tool, arguments):
        if mcp_tool == "fail_tool":
            raise ValueError("Tool crashed")
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text="success result")]
        return mock_result
        
    mock_mcp_manager.call_tool = AsyncMock(side_effect=mock_call_tool)
    
    with patch("runtime_v2.services.tool_executor.get_mcp_manager", return_value=mock_mcp_manager), \
         patch("runtime_v2.services.tool_executor.agent_tool_policy", return_value=ALLOW):
        calls = [
            {"server": "test", "tool": "good_tool", "arguments": {}},
            {"server": "test", "tool": "fail_tool", "arguments": {}}
        ]
        
        result = await run("mcp_batch", {"calls": calls})
        
        out_str = str(result)
        assert "success result" in out_str
        assert "MCP tool execution failed" in out_str

def test_mcp_batch_approval_gate():
    """Verify that mcp_batch requires CONFIRM level approval."""
    decision = agent_tool_policy("mcp_batch")
    assert decision == CONFIRM, f"Expected CONFIRM, got {decision}"


@pytest.mark.asyncio
async def test_mcp_batch_malformed_calls(mock_mcp_manager):
    """Verify that a malformed calls list does not crash the batch."""
    with patch("runtime_v2.services.tool_executor.get_mcp_manager", return_value=mock_mcp_manager), \
         patch("runtime_v2.services.tool_executor.agent_tool_policy", return_value=ALLOW):
        calls = [
            "this is a string, not a dict",
            {"server": "test", "tool": "good_tool", "arguments": {}}
        ]
        
        result = await run("mcp_batch", {"calls": calls})
        
        out_str = str(result)
        assert "Invalid call object" in out_str

@pytest.mark.asyncio
async def test_mcp_batch_too_many_calls(mock_mcp_manager):
    """Verify that batch size is limited."""
    with patch("runtime_v2.services.tool_executor.get_mcp_manager", return_value=mock_mcp_manager), \
         patch("runtime_v2.services.tool_executor.agent_tool_policy", return_value=ALLOW):
        calls = [{"server": "test", "tool": "tool", "arguments": {}}] * 51
        
        result = await run("mcp_batch", {"calls": calls})
        
        out_str = str(result)
        assert "Maximum of 50 concurrent calls allowed" in out_str
