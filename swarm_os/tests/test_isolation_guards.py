"""Regression coverage for the `swarm_os/tests` isolation guards.

Closes GRAY-1 and GRAY-2 of `docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md`:

* **GRAY-1** -- the outcome-fitness guard lived only in `tests/conftest.py`,
  which is scoped to `tests/`. A future test in THIS tree that drove
  `step_agent_stream` end-to-end would have appended rows to the production
  `data/evolution/fitness.jsonl` -- the store the live evolution daemon scores
  on -- because `.env` sets `SWARM_EVOLUTION` and is loaded with
  `override=True` at app import. `swarmos_isolate_outcome_fitness` closes it.

* **GRAY-2** -- a module that binds a Qdrant client at IMPORT time holds its own
  reference that the package-level patch cannot retroactively change, so it
  needs a per-module patch. Two such modules existed outside the patch lists
  (`organism_console.skills.skill_repository`, `swarm_os.upwork.learning_engine_qdrant`).
  Both are now patched in BOTH conftests, and the sweep below fails closed if a
  new import-time binding is ever added without one.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("swarm_os", "runtime_v2", "organism_console", "qwen_train")
CONFTESTS = (
    REPO_ROOT / "tests" / "conftest.py",
    REPO_ROOT / "swarm_os" / "tests" / "conftest.py",
)
_QDRANT_NAMES = ("AsyncQdrantClient", "QdrantClient")


def test_the_outcome_fitness_gate_is_closed_in_this_tree():
    """GRAY-1: the production fitness gate must be OFF under `swarm_os/tests`."""
    from swarm_os.services.outcome_fitness import _fitness_env_enabled

    assert os.environ.get("SWARM_EVOLUTION") == "0"
    assert _fitness_env_enabled() is False


def _autouse_fixtures_setting(conftest: Path, name: str, value: str) -> set[str]:
    """Names of AUTOUSE fixtures in `conftest` that call setenv(name, value)."""
    hits: set[str] = set()
    for node in ast.parse(conftest.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        autouse = any(
            isinstance(d, ast.Call)
            and getattr(d.func, "attr", "") == "fixture"
            and any(
                k.arg == "autouse" and getattr(k.value, "value", False) is True
                for k in d.keywords
            )
            for d in node.decorator_list
        )
        if not autouse:
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and getattr(sub.func, "attr", "") == "setenv"
                and len(sub.args) == 2
                and getattr(sub.args[0], "value", None) == name
                and getattr(sub.args[1], "value", None) == value
            ):
                hits.add(node.name)
    return hits


def test_the_swarmos_conftest_declares_the_fitness_guard():
    """Structural half: the guard must be an AUTOUSE fixture in THIS tree.

    The behavioural assertion above is NOT load-bearing on a host whose `.env`
    already sets `SWARM_EVOLUTION` to something other than "1" (true on the host
    this was written on). This half fails if the guard is removed or loses
    `autouse=True`, so the gap cannot silently reopen.
    """
    declared = _autouse_fixtures_setting(
        REPO_ROOT / "swarm_os" / "tests" / "conftest.py", "SWARM_EVOLUTION", "0"
    )
    assert declared, (
        "no autouse fixture in swarm_os/tests/conftest.py sets SWARM_EVOLUTION=0"
    )


def test_the_residual_import_time_qdrant_bindings_are_in_memory():
    """GRAY-2: the two import-time `QdrantClient` globals are patched here."""
    from organism_console.skills import skill_repository
    from swarm_os.upwork import learning_engine_qdrant

    for module in (skill_repository, learning_engine_qdrant):
        client = module.QdrantClient("http://127.0.0.1:6333")
        inner = client._client
        # An UNPATCHED call builds a QdrantRemote against the URL above; the
        # patch must make it a local in-memory store instead.
        assert type(inner).__name__ == "QdrantLocal", module.__name__
        assert inner.location == ":memory:", module.__name__


def _patched_qdrant_modules() -> set[str]:
    """Module paths that carry a per-module Qdrant patch in either conftest."""
    targets: set[str] = set()
    pattern = re.compile(r'patch\(\s*"([\w.]+)\.(?:Async)?QdrantClient"')
    for conftest in CONFTESTS:
        targets.update(pattern.findall(conftest.read_text(encoding="utf-8")))
    return targets


def _import_time_qdrant_modules() -> set[str]:
    """Tracked modules that bind a Qdrant client at MODULE level (column 0)."""
    found: set[str] = set()
    for top in SOURCE_ROOTS:
        for path in (REPO_ROOT / top).rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:  # module level only; call-time imports are covered
                if isinstance(node, ast.ImportFrom) and node.module == "qdrant_client":
                    if any(alias.name in _QDRANT_NAMES for alias in node.names):
                        dotted = str(path.relative_to(REPO_ROOT))
                        found.add(dotted[:-3].replace("\\", ".").replace("/", "."))
    return found


def test_every_import_time_qdrant_binding_is_patched():
    """No module may hold an unpatched import-time Qdrant client.

    Call-time imports are already covered by the package-level patch; only
    import-time globals need a per-module entry. This fails closed if a new one
    appears without one -- exactly the failure mode GRAY-2 warned about.
    """
    uncovered = sorted(_import_time_qdrant_modules() - _patched_qdrant_modules())
    assert uncovered == [], (
        "import-time Qdrant bindings with no per-module patch in either "
        f"conftest: {uncovered}"
    )
