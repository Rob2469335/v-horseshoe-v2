collect_ignore = [
    # 1-line truncated fragment of twine's tests/test_package.py, committed in
    # f8f40ecd. It is not a runnable test; its only line is a dict fragment that
    # raises SyntaxError at import, which aborts collection for the ENTIRE suite.
    # Listed here rather than in the root conftest because collect_ignore
    # resolves basenames relative to the conftest's own directory.
    "test_package.py",
]

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
import os
import subprocess

# The genuine Popen class, captured before any fixture can mock it. Used by
# `global_subprocess_mock` to guarantee a leaked mock is never restored as the
# new baseline for later patches (see that fixture's teardown).
_REAL_POPEN = subprocess.Popen


@pytest.fixture(scope="session", autouse=True)
def _mock_telegram_token():
    os.environ["TELEGRAM_BOT_TOKEN"] = ""


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
from qdrant_client import AsyncQdrantClient, QdrantClient

# Capture the REAL constructors at conftest-import time, BEFORE any patch is
# active. `_qdrant_in_memory` must build the in-memory client from this reference:
# if it called the module attribute `AsyncQdrantClient` it would resolve to the
# patched stub and recurse (or construct a real network client).
_REAL_ASYNC_QDRANT = AsyncQdrantClient
_REAL_SYNC_QDRANT = QdrantClient


def _qdrant_in_memory(*args, **kwargs):
    """Return a real in-memory AsyncQdrantClient, discarding any URL argument."""
    return _REAL_ASYNC_QDRANT(":memory:")


def _qdrant_in_memory_sync(*args, **kwargs):
    """Return a real in-memory (sync) QdrantClient, discarding any URL argument."""
    return _REAL_SYNC_QDRANT(":memory:")


@pytest.fixture(autouse=True)
def global_qdrant_mock():
    """Force every Qdrant client to be in-memory for the whole test.

    Two layers are required, and the reason matters if you edit this:

    1. Package-level (`qdrant_client.AsyncQdrantClient`). `from qdrant_client
       import AsyncQdrantClient` resolves against `sys.modules['qdrant_client']`,
       so patching the package attribute intercepts imports performed at CALL
       time. 13 production sites use a function-local import and were previously
       untouchable here: `tests/test_semantic_cache_smoke.py` opened a real TCP
       connection to 127.0.0.1:6333 and took 42s retrying a dead endpoint.

    2. Per-module bindings. A module that did `from qdrant_client import
       AsyncQdrantClient` at IMPORT time holds its own reference that a package
       patch cannot retroactively change. Patching only the package therefore
       leaves those globals pointing at the real network client -- verified.

    Layer 1 alone is insufficient; layer 2 alone (the previous state) misses the
    function-local sites. Both are needed to satisfy the invariant that no test
    can obtain a real Qdrant client by either import style.
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
    with patch("swarm_os.services.chess_trainer._get_engine", return_value=None):
        yield


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    # Prevent tests from spawning actual background servers (like uvicorn or ollama)
    # which leads to PytestUnhandledThreadExceptionWarning and zombie processes.
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.communicate.return_value = (b"", b"")
        # `subprocess.run` enters Popen as a context manager
        # (`with Popen(...) as process`), so `process` is
        # `mock_popen.return_value.__enter__.return_value` - a DIFFERENT object from
        # `mock_popen.return_value`. Without configuring that child,
        # `stdout, stderr = process.communicate(...)` unpacks a bare MagicMock and
        # raises `ValueError: not enough values to unpack (expected 2, got 0)`.
        mock_popen.return_value.__enter__.return_value.communicate.return_value = (
            b"",
            b"",
        )
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.pid = 99999
        yield mock_popen

    # `patch()` restores whatever it captured on entry. If an earlier test left a
    # mock installed as subprocess.Popen, that mock becomes the "original" for
    # every later patch, so the leak is immortalised across the whole session and
    # survives into modules that never opted into mocking. Always re-assert the
    # genuine class so a leaked mock can never become the new baseline.
    if subprocess.Popen is not _REAL_POPEN:
        subprocess.Popen = _REAL_POPEN


@pytest.fixture(autouse=True)
def isolate_outcome_fitness(monkeypatch):
    """Never let a test feed the REAL outcome-fitness store.

    `_feed_outcome` fires whenever SWARM_EVOLUTION=1, and .env sets that
    globally — so any test that drives step_agent_stream end-to-end (e.g.
    test_opencode_parity's "do a compound task") appended sentinel-task rows
    to the repo's real data/evolution/fitness.jsonl, polluting the very store
    the live evolution daemon scores on. Disable by default; tests that
    exercise the fitness path set SWARM_EVOLUTION=1 AND patch FITNESS_PATH
    themselves (monkeypatch re-orders, so their setenv still wins).
    """
    monkeypatch.setenv("SWARM_EVOLUTION", "0")


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
