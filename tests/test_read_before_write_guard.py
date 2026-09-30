"""Regression tests for the read-before-write guard path-equivalence fix.

Verifies that the guard correctly recognizes equivalent absolute and relative
representations of the same permitted workspace file.

Root cause (F1 Observation 2): _norm() only stripped _ROOT (the main repo),
so an absolute F1 workspace path could not be normalized to root-relative form,
causing the explored-set lookup to fail when the model patched with a relative path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO / "runtime_v2" / "services") not in sys.path:
    sys.path.insert(0, str(_REPO / "runtime_v2" / "services"))

import tool_executor as te


@pytest.fixture(autouse=True)
def _clean_exploration():
    """Reset exploration state before and after each test."""
    te.reset_exploration_state()
    yield
    te.reset_exploration_state()


class TestPathNorm:
    def test_absolute_main_repo_path_strips_root(self):
        """Absolute path under _ROOT is normalized to root-relative."""
        abs_path = str(te._ROOT / "swarm_os" / "lib" / "paths.py")
        assert te._norm(abs_path) == "swarm_os/lib/paths.py"

    def test_relative_path_unchanged(self):
        """Relative path stays relative."""
        assert te._norm("swarm_os/lib/paths.py") == "swarm_os/lib/paths.py"

    def test_absolute_workspace_path_strips_workspace_root(self):
        """Absolute path under SWARM_WORKSPACE_ROOT is normalized to root-relative."""
        ws = os.environ.get("SWARM_WORKSPACE_ROOT", "")
        if not ws:
            pytest.skip("SWARM_WORKSPACE_ROOT not set")
        abs_path = str(Path(ws) / "swarm_os" / "lib" / "paths.py")
        result = te._norm(abs_path)
        assert result == "swarm_os/lib/paths.py"

    def test_backslash_paths_normalized(self):
        """Windows backslash paths are normalized to forward slashes."""
        assert te._norm("swarm_os\\lib\\paths.py") == "swarm_os/lib/paths.py"

    def test_lstrip_slashes(self):
        """Leading slashes are stripped."""
        assert te._norm("/swarm_os/lib/paths.py") == "swarm_os/lib/paths.py"


class TestReadBeforeWriteGuard:
    def test_absolute_read_relative_patch_allowed(self):
        """Absolute read + relative patch = ALLOWED (the F1 Observation 2 fix)."""
        ws = os.environ.get("SWARM_WORKSPACE_ROOT", "")
        if not ws:
            pytest.skip("SWARM_WORKSPACE_ROOT not set")
        abs_path = str(Path(ws) / "swarm_os" / "lib" / "paths.py")
        rel_path = "swarm_os/lib/paths.py"
        te._mark_explored([abs_path])
        assert te._explored(rel_path)

    def test_relative_read_absolute_patch_allowed(self):
        """Relative read + absolute patch = ALLOWED."""
        ws = os.environ.get("SWARM_WORKSPACE_ROOT", "")
        if not ws:
            pytest.skip("SWARM_WORKSPACE_ROOT not set")
        rel_path = "swarm_os/lib/paths.py"
        abs_path = str(Path(ws) / "swarm_os" / "lib" / "paths.py")
        te._mark_explored([rel_path])
        assert te._explored(abs_path)

    def test_identical_relative_paths_allowed(self):
        """Same relative path read then patch = ALLOWED."""
        te._mark_explored(["swarm_os/lib/paths.py"])
        assert te._explored("swarm_os/lib/paths.py")

    def test_different_file_rejected(self):
        """Reading one file does not authorize patching a different file."""
        te._mark_explored(["swarm_os/lib/paths.py"])
        assert not te._explored("swarm_os/lib/other_file.py")

    def test_unread_file_rejected(self):
        """File never read cannot be patched."""
        assert not te._explored("swarm_os/lib/paths.py")

    def test_path_traversal_rejected(self):
        """Path traversal does not match explored paths."""
        te._mark_explored(["swarm_os/lib/paths.py"])
        assert not te._explored("swarm_os/lib/../../../etc/passwd")

    def test_parent_dir_read_authorizes_child(self):
        """Reading a parent directory authorizes patching files within it."""
        te._mark_explored(["swarm_os/lib"])
        assert te._explored("swarm_os/lib/paths.py")

    def test_cross_root_workspace_paths_match(self):
        """Absolute path from one root and relative from another still match."""
        ws = os.environ.get("SWARM_WORKSPACE_ROOT", "")
        if not ws:
            pytest.skip("SWARM_WORKSPACE_ROOT not set")
        # Simulate: read with absolute path under _ROOT, patch with relative
        main_abs = str(te._ROOT / "swarm_os" / "lib" / "paths.py")
        te._mark_explored([main_abs])
        assert te._explored("swarm_os/lib/paths.py")
