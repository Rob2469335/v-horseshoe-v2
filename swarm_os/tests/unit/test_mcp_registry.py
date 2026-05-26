import pytest
from swarm_os.agent_runtime import AgentRuntime


@pytest.mark.anyio
async def test_filesystem_allows_repo_relative_read():
    rt = AgentRuntime()
    result = await rt.call_tool("filesystem", {"path": "swarm_os/tool_runtime.py"})
    assert result["ok"] is True
    assert result["path"].endswith("swarm_os\\tool_runtime.py")
    assert isinstance(result["content"], str)
    assert len(result["content"]) > 0


@pytest.mark.anyio
async def test_filesystem_blocks_path_escape():
    rt = AgentRuntime()
    result = await rt.call_tool("filesystem", {"path": r"..\..\Windows\win.ini"})
    assert result["ok"] is False
    assert "outside sandbox" in result["error"].lower()


@pytest.mark.anyio
async def test_qdrant_recall_returns_valid_shape():
    rt = AgentRuntime()
    result = await rt.call_tool("qdrant_recall", {"query": "test", "collection": "chat_archive"})
    assert result["ok"] is True
    assert isinstance(result["results"], list)
