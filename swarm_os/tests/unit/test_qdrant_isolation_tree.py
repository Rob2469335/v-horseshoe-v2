"""Both-test-tree proof for the Qdrant isolation fix.

`swarm_os/tests/conftest.py::swarmos_qdrant_mock` previously patched three module
attributes and omitted `lesson_manager`. This module, living in the `swarm_os/tests`
tree, proves that tree receives a fixture which intercepts BOTH import styles --
a package-level patch that function-local imports resolve through, and per-module
patches for globals bound at import time.

No Qdrant server is started or contacted.
"""


def _is_in_memory(client) -> bool:
    """The fixture builds the REAL class against :memory:, so isinstance cannot
    discriminate; the inner client location can."""
    return getattr(getattr(client, "_client", None), "location", None) == ":memory:"


def test_swarmos_tree_function_local_import_is_intercepted():
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 - seam under test

    assert _is_in_memory(AsyncQdrantClient(url="http://127.0.0.1:6333"))


def test_swarmos_tree_lesson_manager_global_is_intercepted():
    """The site the previous audit flagged as omitted in this tree."""
    from swarm_os.services import lesson_manager as lm

    assert _is_in_memory(lm.AsyncQdrantClient(url="http://127.0.0.1:6333"))


def test_swarmos_tree_other_globals_are_intercepted():
    from swarm_os.services import reflection_loop as rl
    from swarm_os.services import tool_registry as tr
    from swarm_os.services import vector_store as vs

    for name, mod in (
        ("vector_store", vs),
        ("reflection_loop", rl),
        ("tool_registry", tr),
    ):
        assert _is_in_memory(
            mod.AsyncQdrantClient(url="http://127.0.0.1:6333")
        ), f"{name} leaked a URL-backed client"
