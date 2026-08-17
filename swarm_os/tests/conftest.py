"""Isolation fixtures for the swarm_os/tests suite.

The root conftest.py applies to both `tests/` and `swarm_os/tests/`, but
`tests/conftest.py` (Qdrant-in-memory, MCP manager, system probes) is scoped to
the `tests/` directory only. When the two suites run in one pytest process, the
swarm_os tests can initialize real module-level clients that later fight the
`tests/` mocks (or vice-versa). This conftest gives swarm_os/tests the SAME
isolation guarantees so the combined run is deterministic.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from qdrant_client import AsyncQdrantClient


@pytest.fixture(autouse=True)
def swarmos_qdrant_mock():
    """Force in-memory AsyncQdrantClient so no swarm_os test needs live Qdrant."""

    def mock_init(*args, **kwargs):
        return AsyncQdrantClient(":memory:")

    with patch(
        "swarm_os.services.vector_store.AsyncQdrantClient", side_effect=mock_init
    ):
        with patch(
            "swarm_os.services.reflection_loop.AsyncQdrantClient",
            side_effect=mock_init,
            create=True,
        ):
            with patch(
                "swarm_os.services.tool_registry.AsyncQdrantClient",
                side_effect=mock_init,
                create=True,
            ):
                yield


@pytest.fixture(autouse=True)
def swarmos_mcp_manager_mock():
    """Prevent real npx MCP subprocess spawns (matches tests/conftest.py)."""
    mock_mgr = MagicMock()
    mock_mgr.cached_tools = []
    mock_mgr.call_tool = AsyncMock(return_value="mock mcp result")
    mock_mgr.start = AsyncMock()
    mock_mgr.stop = AsyncMock()
    with patch(
        "runtime_v2.services.tool_executor.get_mcp_manager",
        AsyncMock(return_value=mock_mgr),
    ):
        with patch(
            "runtime_v2.services.tool_executor._mcp_manager", mock_mgr, create=True
        ):
            with patch(
                "swarm_os.app.main.get_mcp_manager",
                AsyncMock(return_value=mock_mgr),
                create=True,
            ):
                yield


@pytest.fixture(autouse=True)
def swarmos_system_probe_mock():
    """Block psutil-based system probes."""
    with patch(
        "swarm_os.healing.system_probes.run_system_probes", return_value={}, create=True
    ):
        with patch("swarm_os.app.main.run_system_probes", return_value={}, create=True):
            yield


@pytest.fixture(autouse=True)
def swarmos_subprocess_mock():
    """Prevent background-server subprocess spawns."""
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.communicate.return_value = (b"", b"")
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.pid = 99999
        yield mock_popen
