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
from qdrant_client import AsyncQdrantClient, QdrantClient

# Captured before any patch is active -- see tests/conftest.py for the full
# rationale. `_qdrant_in_memory` must build the real in-memory client from this
# reference rather than the (soon-patched) module attribute.
_REAL_ASYNC_QDRANT = AsyncQdrantClient
_REAL_SYNC_QDRANT = QdrantClient


def _qdrant_in_memory(*args, **kwargs):
    return _REAL_ASYNC_QDRANT(":memory:")


def _qdrant_in_memory_sync(*args, **kwargs):
    return _REAL_SYNC_QDRANT(":memory:")


@pytest.fixture(autouse=True)
def swarmos_qdrant_mock():
    """Force in-memory Qdrant so no swarm_os test needs a live server.

    Package-level patch covers function-local `from qdrant_client import ...`
    sites; per-module patches cover module globals bound at import time. This
    tree previously had neither the package patch nor the `lesson_manager`
    module patch. See tests/conftest.py::global_qdrant_mock.
    """
    with patch("qdrant_client.AsyncQdrantClient", side_effect=_qdrant_in_memory):
        with patch("qdrant_client.QdrantClient", side_effect=_qdrant_in_memory_sync):
            with patch(
                "swarm_os.services.vector_store.AsyncQdrantClient",
                side_effect=_qdrant_in_memory,
            ):
                with patch(
                    "swarm_os.services.reflection_loop.AsyncQdrantClient",
                    side_effect=_qdrant_in_memory,
                    create=True,
                ):
                    with patch(
                        "swarm_os.services.tool_registry.AsyncQdrantClient",
                        side_effect=_qdrant_in_memory,
                        create=True,
                    ):
                        with patch(
                            "swarm_os.services.lesson_manager.AsyncQdrantClient",
                            side_effect=_qdrant_in_memory,
                            create=True,
                        ):
                            with patch(
                                "organism_console.skills.skill_repository.QdrantClient",
                                side_effect=_qdrant_in_memory_sync,
                                create=True,
                            ):
                                with patch(
                                    "swarm_os.upwork.learning_engine_qdrant.QdrantClient",
                                    side_effect=_qdrant_in_memory_sync,
                                    create=True,
                                ):
                                    yield


@pytest.fixture(autouse=True)
def swarmos_isolate_outcome_fitness(monkeypatch):
    """Never let a swarm_os/tests test feed the REAL outcome-fitness store.

    Mirrors `tests/conftest.py::isolate_outcome_fitness`, which is scoped to
    `tests/` only. `_feed_outcome` fires whenever `SWARM_EVOLUTION == "1"`, and
    `.env` sets that globally (loaded with `override=True` at app import), so a
    future test in THIS tree that drives `step_agent_stream` end-to-end would
    append rows to the production `data/evolution/fitness.jsonl` -- the store the
    live evolution daemon scores on.

    GRAY-1 of `docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md`. No test in this
    tree currently references the fitness path, so this closes a LATENT gap
    rather than an active leak; a test that needs the path sets
    `SWARM_EVOLUTION=1` itself (monkeypatch re-orders, so its setenv wins).
    """
    monkeypatch.setenv("SWARM_EVOLUTION", "0")


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
