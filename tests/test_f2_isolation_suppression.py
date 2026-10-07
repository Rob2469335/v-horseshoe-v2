"""Governed-F2 suppression tests.

Proves:
* the F2 P2 launcher env marks governed isolation and disables the codebase index;
* the Qdrant-backed capability strip is governed-only;
* a governed app lifespan suppresses the background subsystems (no scheduler, no
  watch-loop) while normal startup is unchanged.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from qwen_train.f2_execution_adapter import f2_p2_environment
from runtime_v2.services.f2_runtime_guard import F2_ISOLATION_ENV
from runtime_v2.api import _agent_helpers as ah


def _env(*, isolation: bool) -> dict[str, str]:
    return f2_p2_environment(
        workspace_root=r"C:\ws",
        manifest_path=r"C:\m.json",
        repo_root=r"C:\repo",
        rollout_id="r1",
        trajectory_run_id="t1",
        traj_dir=r"C:\ws\data\trajectories",
        isolation=isolation,
        base={"SWARM_F2_ISOLATION": "0"},
    )


def test_governed_env_marks_isolation_and_suppresses_index():
    env = _env(isolation=True)
    assert env[F2_ISOLATION_ENV] == "1"
    assert env["SWARM_CODEBASE_INDEX"] == "0"
    assert env["SWARM_MEMORY_INJECT"] == "0"
    assert env["SWARM_AUTONOMY"] == "0"
    assert env["SWARM_EVOLUTION"] == "0"
    assert env["SWARM_GENETIC_MUTATION"] == "0"
    assert env["SWARM_SEMANTIC_CACHE"] == "0"
    assert env["SWARM_F1_NO_WEB_TOOLS"] == "1"


def test_infra_env_is_not_governed_but_still_no_index():
    env = _env(isolation=False)
    assert env[F2_ISOLATION_ENV] == "0"
    assert env["SWARM_CODEBASE_INDEX"] == "0"


def test_capability_strip_governed(monkeypatch):
    monkeypatch.setenv(F2_ISOLATION_ENV, "1")
    out = ah._strip_f2_qdrant_tools(
        "coder",
        ["semantic_search", "remember", "deprecate_memory", "filesystem", "git", "final"],
    )
    assert out == ["filesystem", "git", "final"]


def test_capability_preserved_outside_f2(monkeypatch):
    monkeypatch.delenv(F2_ISOLATION_ENV, raising=False)
    tools = ["semantic_search", "remember", "filesystem"]
    assert ah._strip_f2_qdrant_tools("coder", tools) == tools


def test_governed_lifespan_suppresses_background(monkeypatch):
    monkeypatch.setenv(F2_ISOLATION_ENV, "1")
    # Avoid a real socket probe during the test: the guard's own logic is proven
    # by test_f2_runtime_guard.py.
    import runtime_v2.services.f2_runtime_guard as guard

    monkeypatch.setattr(
        guard, "assert_forbidden_services_unreachable", lambda *a, **k: []
    )
    from swarm_os.app.main import app

    with TestClient(app):
        assert getattr(app.state, "f2_background_suppressed", False) is True
        assert not hasattr(app.state, "scheduler")
        assert getattr(app.state, "watch_loop", None) is None


def test_normal_lifespan_not_suppressed(monkeypatch):
    monkeypatch.delenv(F2_ISOLATION_ENV, raising=False)
    from swarm_os.app.main import app

    with TestClient(app):
        assert getattr(app.state, "f2_background_suppressed", False) is False
        assert hasattr(app.state, "scheduler")
