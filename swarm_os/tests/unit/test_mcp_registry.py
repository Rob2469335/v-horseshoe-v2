import os
import pytest
from pathlib import Path
from swarm_os.agent_runtime import AgentRuntime


@pytest.mark.anyio
async def test_filesystem_allows_repo_relative_read():
    rt = AgentRuntime()
    result = await rt.call_tool("filesystem", {"path": "swarm_os/tool_runtime.py"})
    assert result["ok"] is True
    # Compare with os.sep so this works on both Windows (\) and POSIX (/).
    assert Path(result["path"]).name == "tool_runtime.py"
    assert result["path"].replace(os.sep, "/").endswith("swarm_os/tool_runtime.py")
    assert isinstance(result["content"], str)
    assert len(result["content"]) > 0


@pytest.mark.anyio
async def test_filesystem_blocks_path_escape():
    rt = AgentRuntime()
    # A path that escapes the sandbox root regardless of platform.
    escape = os.path.join(os.pardir, os.pardir, "should-not-exist.txt")
    result = await rt.call_tool("filesystem", {"path": escape})
    assert result["ok"] is False
    assert "outside sandbox" in result["error"].lower()


@pytest.mark.anyio
async def test_qdrant_recall_returns_valid_shape():
    rt = AgentRuntime()
    result = await rt.call_tool("qdrant_recall", {"query": "test", "collection": "chat_archive"})
    assert result["ok"] is True
    assert isinstance(result["results"], list)
