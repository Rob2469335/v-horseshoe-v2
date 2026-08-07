"""Tests for the written autonomy policy loader (autonomy_policy.json).

Covers: the single-source-of-truth loading, the fail-closed path when the policy
is missing, the directory-level self-modify block, and the DEPENDENCY-AWARE
self-modify block (a file the repair machinery imports is off-limits even if it
lives in an allowed directory).
"""
from pathlib import Path

import pytest

from swarm_os.services import autonomy_policy as ap


@pytest.fixture
def policy():
    p = ap.get_autonomy_policy(reload=True)
    assert p is not None, "autonomy_policy.json must load for these tests"
    return p


def test_policy_allows_app_source_trees(policy):
    root = policy.repo_root
    assert policy.is_repairable(root / "swarm_os/services/tool_registry.py")
    assert policy.is_repairable(root / "runtime_v2/api/agent_service_v2.py")
    assert policy.is_repairable(root / "organism_console/ui/live_stream.py")


def test_policy_blocks_src_and_blocked_patterns(policy):
    root = policy.repo_root
    # src/ was deleted 2026-08 — must not be allowed (stale-dir trap).
    assert not policy.is_repairable(root / "src/core/foo.py")
    assert not policy.is_repairable(root / "tests/test_x.py")
    assert not policy.is_repairable(root / "scripts/x.py")
    assert not policy.is_repairable(root / "config/settings.py")


def test_policy_blocks_self_modify_machinery_dirs(policy):
    root = policy.repo_root
    for rel in (
        "swarm_os/healing/recovery_engine.py",
        "swarm_os/healing/governor.py",
        "swarm_os/services/reflection_loop.py",
        "swarm_os/services/security_gate.py",
        "swarm_os/app/main.py",
    ):
        assert not policy.is_repairable(root / rel), rel


def test_policy_blocks_machinery_dependency_aware(policy):
    """Dependency-aware self-modify: recovery_engine imports memory_bridge, which
    imports vector_store — so vector_store is off-limits even though it lives in
    an otherwise-allowed directory. An enumerated path list would miss this."""
    root = policy.repo_root
    assert not policy.is_repairable(root / "swarm_os/memory/memory_bridge.py")
    assert not policy.is_repairable(root / "swarm_os/services/vector_store.py")
    assert str((root / "swarm_os/services/vector_store.py").resolve()) in policy.self_modify_files


def test_policy_is_repairable_fails_closed_on_none():
    assert ap.AutonomyPolicy.__dict__.get("is_repairable")
    # Missing/None file path -> not repairable (fail closed).
    policy = ap.get_autonomy_policy(reload=True)
    assert policy is not None
    assert policy.is_repairable(None) is False
    assert policy.is_repairable(Path("not_a_py_file.txt")) is False
