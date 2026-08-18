import ssl

try:
    ssl._create_default_https_context = ssl._create_unverified_context
    ssl.create_default_context = ssl._create_unverified_context
except AttributeError:
    pass
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

import swarm_os.bootstrap  # noqa: F401  (side-effect import: initializes bootstrap)
from fastapi.testclient import TestClient
import pytest

from swarm_os.app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session", autouse=True)
def harden_testclient_shutdown():
    import logging
    import threading
    from starlette import testclient as _starlette_testclient

    log = logging.getLogger("tests.conftest")
    _orig_exit = _starlette_testclient.TestClient.__exit__

    def _bounded_exit(self, *args):
        t = threading.Thread(
            target=_orig_exit,
            args=(self, *args),
            name="testclient-exit",
            daemon=True,
        )
        t.start()
        t.join(timeout=20)
        if t.is_alive():
            log.warning(
                "TestClient shutdown exceeded 20s (anyio#1014 portal wakeup lost "
                "on asyncio); abandoning teardown to keep the suite running"
            )

    _starlette_testclient.TestClient.__exit__ = _bounded_exit
    yield
    _starlette_testclient.TestClient.__exit__ = _orig_exit


from unittest.mock import patch, AsyncMock, MagicMock
from qdrant_client import AsyncQdrantClient


@pytest.fixture(autouse=True)
def global_qdrant_mock():
    # Intercept any AsyncQdrantClient instantiation and force it to be an in-memory client.
    # This prevents the test suite from requiring a live local Qdrant server.
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
def global_mcp_manager_mock():
    """Prevent real npx MCP server subprocesses from spawning during TestClient
    lifespan startup. The MCP SDK uses anyio.create_subprocess_exec which
    bypasses subprocess.Popen mocks, causing npx downloads/handshakes that
    hang indefinitely with no timeout.

    BUG FIX: main.py does `from runtime_v2.services.tool_executor import get_mcp_manager`
    inside the lifespan body, so the patch must also cover the locally-imported
    name that Python binds at call time. Patching only the module-level symbol
    leaves the in-lifespan binding pointing at the real function.
    """
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
def global_system_probe_mock():
    """Block system probes from running during tests — they call psutil and
    may take seconds or raise on CI environments without full OS access."""
    with patch(
        "swarm_os.healing.system_probes.run_system_probes", return_value={}, create=True
    ):
        with patch("swarm_os.app.main.run_system_probes", return_value={}, create=True):
            yield


@pytest.fixture(autouse=True)
def global_chess_engine_mock():
    """Prevent a REAL Stockfish from ever spawning during the suite.

    Every TestClient startup runs the app lifespan, which calls
    `resume_incomplete()` → `asyncio.to_thread(_analyze_game, ...)` on the
    event loop's DEFAULT ThreadPoolExecutor. Under a module-scope real-Popen
    override that executor thread then blocks forever inside python-chess's
    `engine.analyse` (no timeout), and the TestClient portal's shutdown does
    `executor.shutdown(wait=True)` — the asyncio-lib #1014-wide hang class that
    wedged full-suite runs at nondeterministic percentages.

    `_get_engine()` is the one seam: both `_analyse` and `_best_move_and_cp`
    route through it and fail closed (return None) when it returns None, so no
    subprocess is ever started.
    """
    with patch(
        "swarm_os.services.chess_trainer._get_engine", return_value=None
    ):
        yield


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    # Prevent tests from spawning actual background servers (like uvicorn or ollama)
    # which leads to PytestUnhandledThreadExceptionWarning and zombie processes.
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.communicate.return_value = (b"", b"")
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.pid = 99999
        yield mock_popen


async def run_approved(tool_executor_run, tool_name: str, payload: dict) -> dict:
    """Drive a tool through the pre-action authorization gate to its real
    implementation: call run() (creates a pending action), then execute the
    STORED payload via execute_approved (digest-trust-anchored). Mirrors the
    CLI approve flow. Tests that exercise the tool HANDLER (path guards, SSRF,
    arg checks) call this instead of bypassing the gate.
    """
    first = await tool_executor_run(tool_name, payload)
    if first.get("status") != "confirmation_required":
        return first  # ALLOW/DENY path — nothing to approve
    pending_id = first["pending_id"]
    from runtime_v2.services.tool_executor import execute_approved

    return await execute_approved(pending_id)
