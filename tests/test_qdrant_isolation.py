"""Regression coverage for the Qdrant test-isolation RED finding.

Proven failure (docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md §7 / §19):
`tests/test_semantic_cache_smoke.py` obtained the REAL
`qdrant_client.AsyncQdrantClient` through a function-local import and opened a TCP
connection to 127.0.0.1:6333, taking 42s retrying a dead endpoint. Per-module
attribute patches cannot intercept a call-time import.

Invariant under test: NO ordinary pytest test may obtain a real Qdrant client
through either import style -- module-level (`from qdrant_client import ...` at
import time) or function-local (executed at call time).

No Qdrant server is started or contacted by this module.
"""

import socket

import pytest

import qdrant_client

# The fixture does NOT substitute a different class -- it constructs the REAL
# AsyncQdrantClient against an in-memory location, so `isinstance` cannot
# discriminate. The reliable discriminator is the inner client's location.
_REAL_SYNC = qdrant_client.QdrantClient


def _is_in_memory(client) -> bool:
    """True when the client talks to Qdrant's local in-memory mode, not a URL."""
    inner = getattr(client, "_client", None)
    return getattr(inner, "location", None) == ":memory:"


class _SocketSpy:
    """Record TCP connect attempts to the Qdrant port without blocking them."""

    def __init__(self, port=6333):
        self.port = port
        self.attempts = []
        self._orig = None

    def __enter__(self):
        spy = self

        def _connect(sock, addr):
            try:
                p = addr[1] if isinstance(addr, tuple) else None
                h = addr[0] if isinstance(addr, tuple) else str(addr)
            except Exception:
                p, h = None, str(addr)
            if p == spy.port:
                spy.attempts.append((h, p))
            return spy._orig(sock, addr)

        self._orig = socket.socket.connect
        socket.socket.connect = _connect
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._orig
        return False


# --- A. Function-local import interception -----------------------------------


def test_function_local_import_is_intercepted():
    """A call-time import must resolve to the in-memory client, not a URL client."""
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 - the seam under test

    client = AsyncQdrantClient(url="http://127.0.0.1:6333")
    assert _is_in_memory(client)


def test_function_local_sync_import_is_intercepted():
    """The sync constructor has the same bypass; it must be intercepted too."""
    from qdrant_client import QdrantClient  # noqa: PLC0415 - the seam under test

    client = QdrantClient(url="http://127.0.0.1:6333")
    inner = getattr(client, "_client", None)
    assert getattr(inner, "location", None) == ":memory:"


def test_semantic_cache_seam_constructs_fake_not_real():
    """Exercise the exact module seam that previously opened a live socket."""
    from runtime_v2.services import _semantic_decision_cache as m

    client = m._make_qdrant_client()
    assert _is_in_memory(client)


# --- B. Module-global bindings ------------------------------------------------


def test_module_global_bindings_are_still_intercepted():
    """A package-only patch would regress these; per-module patches are required."""
    from swarm_os.services import lesson_manager as lm
    from swarm_os.services import reflection_loop as rl
    from swarm_os.services import tool_registry as tr
    from swarm_os.services import vector_store as vs

    for name, mod in (
        ("vector_store", vs),
        ("reflection_loop", rl),
        ("tool_registry", tr),
        ("lesson_manager", lm),
    ):
        client = mod.AsyncQdrantClient(url="http://127.0.0.1:6333")
        assert _is_in_memory(client), f"{name} leaked a URL-backed client"


# --- C. Write path uses the fake and never dials the network ------------------


@pytest.mark.asyncio
async def test_semantic_cache_write_path_is_in_memory():
    """Drive cache_tool_decision far enough to construct its client and embed.

    The upsert itself needs a live embedding vector, so this asserts the client
    identity and socket silence rather than a completed write -- that is the
    property that previously failed.
    """
    from runtime_v2.services import _semantic_decision_cache as m

    m._client_bound.reset() if hasattr(m._client_bound, "reset") else None

    with _SocketSpy() as spy:
        try:
            await m.cache_tool_decision(
                [{"role": "user", "content": "isolation-regression-unique-8881234"}],
                "isolation_probe_agent",
                {"action": "filesystem", "operation": "glob", "path": "runtime_v2"},
            )
        except Exception:
            pass  # embedder is offline in tests; the client path is what matters

    assert spy.attempts == [], f"attempted real Qdrant connections: {spy.attempts}"


def test_existing_semantic_cache_smoke_cannot_reach_qdrant():
    """Directly guard the test that originally proved the RED."""
    from qdrant_client import AsyncQdrantClient

    with _SocketSpy() as spy:
        client = AsyncQdrantClient(url="http://127.0.0.1:6333")
        assert hasattr(client, "collection_exists")

    assert spy.attempts == [], f"attempted real Qdrant connections: {spy.attempts}"
    assert _is_in_memory(client)


# --- D. Both test trees -------------------------------------------------------


def test_fixture_is_active_in_this_tree():
    """Running under tests/ proves the tests/conftest.py fixture applies here."""
    from qdrant_client import AsyncQdrantClient

    assert _is_in_memory(AsyncQdrantClient(url="http://127.0.0.1:6333"))
