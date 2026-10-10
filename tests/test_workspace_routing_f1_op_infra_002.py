"""Tests for F1-OP-INFRA-002 workspace-routing correction.

Covers the complete transport chain:
  evaluator env → CLI header → credential-gated backend reception →
  request-scoped context → tool_executor containment/path logic →
  filesystem_handler receiving the correct workspace root.
"""
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. Header forwarding
# ---------------------------------------------------------------------------

class TestHeaderForwarding:
    """SWARM_WORKSPACE_ROOT is forwarded as X-Swarm-Workspace-Root."""

    def test_workspace_root_forwarded_when_set(self, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "/some/workspace")
        from organism_console.api_client import _auth_headers
        headers = _auth_headers()
        assert headers.get("X-Swarm-Workspace-Root") == "/some/workspace"

    def test_workspace_root_absent_when_unset(self, monkeypatch):
        monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
        from organism_console.api_client import _auth_headers
        headers = _auth_headers()
        assert "X-Swarm-Workspace-Root" not in headers

    def test_workspace_root_empty_string_not_sent(self, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "  ")
        from organism_console.api_client import _auth_headers
        headers = _auth_headers()
        assert "X-Swarm-Workspace-Root" not in headers

    def test_other_headers_unchanged(self, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "/ws")
        monkeypatch.setenv("SWARM_TASK_ID", "t1")
        monkeypatch.setenv("SWARM_HARNESS_KEY", "k1")
        monkeypatch.setenv("SWARM_ROLLOUT_ID", "r1")
        monkeypatch.setenv("SWARM_EVAL_ID", "e1")
        from organism_console.api_client import _auth_headers
        headers = _auth_headers()
        assert headers["X-Swarm-Task-Id"] == "t1"
        assert headers["X-Swarm-Harness-Key"] == "k1"
        assert headers["X-Swarm-Rollout-Id"] == "r1"
        assert headers["X-Swarm-Eval-Id"] == "e1"
        assert headers["X-Swarm-Workspace-Root"] == "/ws"


# ---------------------------------------------------------------------------
# 2. Authentication gate
# ---------------------------------------------------------------------------

class TestAuthenticationGate:
    """Workspace header is rejected/ignored without valid harness credential."""

    def test_no_harness_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("SWARM_HARNESS_KEY", raising=False)
        from swarm_os.api.agents import _harness_workspace_root
        result = _harness_workspace_root({
            "x-swarm-workspace-root": "/some/path",
        })
        assert result is None

    def test_wrong_harness_key_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "correct-key")
        from swarm_os.api.agents import _harness_workspace_root
        result = _harness_workspace_root({
            "x-swarm-harness-key": "wrong-key",
            "x-swarm-workspace-root": str(tmp_path),
        })
        assert result is None

    def test_no_header_returns_none(self, monkeypatch):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        from swarm_os.api.agents import _harness_workspace_root
        result = _harness_workspace_root({})
        assert result is None

    def test_empty_header_returns_none(self, monkeypatch):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        from swarm_os.api.agents import _harness_workspace_root
        result = _harness_workspace_root({
            "x-swarm-harness-key": "key1",
            "x-swarm-workspace-root": "",
        })
        assert result is None


# ---------------------------------------------------------------------------
# 3. Valid workspace
# ---------------------------------------------------------------------------

class TestValidWorkspace:
    """Authorized valid Twine workspace is accepted."""

    def test_valid_workspace_under_swe_probe_work(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        # Create a workspace under swe_probe_work (adjacent to project root)
        proj = tmp_path / "v-horseshoe-v2"
        proj.mkdir()
        swe = tmp_path / "swe_probe_work"
        ws = swe / "pypa__twine-1066" / "repo"
        ws.mkdir(parents=True)
        from swarm_os.api.agents import _harness_workspace_root
        with patch("swarm_os.lib.paths.project_root", return_value=proj):
            result = _harness_workspace_root({
                "x-swarm-harness-key": "key1",
                "x-swarm-workspace-root": str(ws),
            })
        assert result == str(ws.resolve())

    def test_workspace_under_project_root_rejected(self, monkeypatch, tmp_path):
        """A workspace under the project root but outside swe_probe_work is rejected."""
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        ws = tmp_path / "subdir"
        ws.mkdir()
        from swarm_os.api.agents import _harness_workspace_root
        with patch("swarm_os.lib.paths.project_root", return_value=tmp_path):
            result = _harness_workspace_root({
                "x-swarm-harness-key": "key1",
                "x-swarm-workspace-root": str(ws),
            })
        assert result is None


# ---------------------------------------------------------------------------
# 4. Invalid workspace
# ---------------------------------------------------------------------------

class TestInvalidWorkspace:
    """Reject non-absolute, nonexistent, non-directory, outside-parent."""

    def test_non_absolute_rejected(self, monkeypatch):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        from swarm_os.api.agents import _harness_workspace_root
        result = _harness_workspace_root({
            "x-swarm-harness-key": "key1",
            "x-swarm-workspace-root": "relative/path",
        })
        assert result is None

    def test_nonexistent_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        from swarm_os.api.agents import _harness_workspace_root
        result = _harness_workspace_root({
            "x-swarm-harness-key": "key1",
            "x-swarm-workspace-root": str(tmp_path / "nonexistent"),
        })
        assert result is None

    def test_non_directory_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        f = tmp_path / "file.txt"
        f.touch()
        from swarm_os.api.agents import _harness_workspace_root
        result = _harness_workspace_root({
            "x-swarm-harness-key": "key1",
            "x-swarm-workspace-root": str(f),
        })
        assert result is None

    def test_outside_allowed_parent_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        # Create workspace outside swe_probe_work
        outside = tmp_path / "other" / "workspace"
        outside.mkdir(parents=True)
        from swarm_os.api.agents import _harness_workspace_root
        proj = tmp_path / "v-horseshoe-v2"
        proj.mkdir()
        with patch("swarm_os.lib.paths.project_root", return_value=proj):
            result = _harness_workspace_root({
                "x-swarm-harness-key": "key1",
                "x-swarm-workspace-root": str(outside),
            })
        assert result is None

    def test_swe_probe_work_evil_sibling_rejected(self, monkeypatch, tmp_path):
        """A workspace under swe_probe_work_evil (sibling of swe_probe_work) is rejected."""
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        proj = tmp_path / "v-horseshoe-v2"
        proj.mkdir()
        # swe_probe_work_evil is a sibling of swe_probe_work, not a child
        evil = tmp_path / "swe_probe_work_evil" / "task" / "repo"
        evil.mkdir(parents=True)
        from swarm_os.api.agents import _harness_workspace_root
        with patch("swarm_os.lib.paths.project_root", return_value=proj):
            result = _harness_workspace_root({
                "x-swarm-harness-key": "key1",
                "x-swarm-workspace-root": str(evil),
            })
        assert result is None


# ---------------------------------------------------------------------------
# 5. Symlink/junction containment
# ---------------------------------------------------------------------------

class TestSymlinkContainment:
    """A workspace resolving outside the allowed parent cannot bypass validation.

    ``_harness_workspace_root`` containment is against
    ``project_root().parent / "swe_probe_work"`` and it imports ``project_root``
    locally from ``swarm_os.lib.paths`` — so the patch target must be
    ``swarm_os.lib.paths.project_root`` (there is no module-level
    ``project_root`` on ``swarm_os.api.agents``; patching that raised
    AttributeError).
    """

    def test_symlink_outside_parent_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        proj = tmp_path / "project"
        proj.mkdir()
        # The allowed parent is sibling to the project root.
        swe_parent = tmp_path / "swe_probe_work"
        swe_parent.mkdir()
        # Create a symlink that resolves outside the allowed parent
        allowed = swe_parent / "allowed"
        allowed.mkdir()
        target = tmp_path / "forbidden"
        target.mkdir()
        link = allowed / "escape"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("OS does not support symlinks")
        from swarm_os.api.agents import _harness_workspace_root
        with patch("swarm_os.lib.paths.project_root", return_value=proj):
            result = _harness_workspace_root({
                "x-swarm-harness-key": "key1",
                "x-swarm-workspace-root": str(link),
            })
        # The resolved canonical path is outside the allowed parent → rejected
        assert result is None

    def test_symlink_inside_parent_accepted(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SWARM_HARNESS_KEY", "key1")
        proj = tmp_path / "project"
        proj.mkdir()
        swe_parent = tmp_path / "swe_probe_work"
        swe_parent.mkdir()
        ws_real = swe_parent / "workspaces" / "task1"
        ws_real.mkdir(parents=True)
        link = swe_parent / "active_task"
        try:
            link.symlink_to(ws_real)
        except OSError:
            pytest.skip("OS does not support symlinks")
        from swarm_os.api.agents import _harness_workspace_root
        with patch("swarm_os.lib.paths.project_root", return_value=proj):
            result = _harness_workspace_root({
                "x-swarm-harness-key": "key1",
                "x-swarm-workspace-root": str(link),
            })
        assert result == str(ws_real.resolve())


# ---------------------------------------------------------------------------
# 6. Request isolation
# ---------------------------------------------------------------------------

class TestRequestIsolation:
    """Two request contexts with different workspace roots do not interfere."""

    def test_concurrent_contexts_independent(self, tmp_path):
        """ContextVar values are request-scoped and do not leak between tasks."""
        from runtime_v2.services.tool_executor import WORKSPACE_ROOT_CTX

        ws_a = tmp_path / "ws_a"
        ws_a.mkdir()
        ws_b = tmp_path / "ws_b"
        ws_b.mkdir()

        # Default is None
        assert WORKSPACE_ROOT_CTX.get() is None

        # Set to ws_a, verify it
        token_a = WORKSPACE_ROOT_CTX.set(str(ws_a))
        assert WORKSPACE_ROOT_CTX.get() == str(ws_a)

        # Set to ws_b (new token), verify ws_b
        token_b = WORKSPACE_ROOT_CTX.set(str(ws_b))
        assert WORKSPACE_ROOT_CTX.get() == str(ws_b)

        # Reset ws_b, verify ws_a is still there
        WORKSPACE_ROOT_CTX.reset(token_b)
        assert WORKSPACE_ROOT_CTX.get() == str(ws_a)

        # Reset ws_a, verify default
        WORKSPACE_ROOT_CTX.reset(token_a)
        assert WORKSPACE_ROOT_CTX.get() is None


# ---------------------------------------------------------------------------
# 7. Ordinary fallback
# ---------------------------------------------------------------------------

class TestOrdinaryFallback:
    """No workspace header → existing _ROOT behavior."""

    def test_effective_root_returns_ROOT_when_ctx_unset(self):
        from runtime_v2.services.tool_executor import _effective_root, _ROOT, WORKSPACE_ROOT_CTX

        token = WORKSPACE_ROOT_CTX.set(None)
        try:
            result = _effective_root()
            assert result == _ROOT
        finally:
            WORKSPACE_ROOT_CTX.reset(token)

    def test_effective_root_returns_ctx_when_set(self, tmp_path):
        from runtime_v2.services.tool_executor import _effective_root, WORKSPACE_ROOT_CTX

        token = WORKSPACE_ROOT_CTX.set(str(tmp_path))
        try:
            result = _effective_root()
            assert result == tmp_path
        finally:
            WORKSPACE_ROOT_CTX.reset(token)


# ---------------------------------------------------------------------------
# 8. _contained()
# ---------------------------------------------------------------------------

class TestContainedGuard:
    """Experiment workspace writes/patches are evaluated against the request-scoped workspace."""

    def test_contained_uses_ROOT_when_ctx_unset(self, tmp_path):
        from runtime_v2.services.tool_executor import _contained

        # Patch _ROOT to tmp_path for testing
        with patch("runtime_v2.services.tool_executor._ROOT", tmp_path):
            # A file inside _ROOT should be contained
            f = tmp_path / "inside.py"
            f.write_text("x")
            result = _contained("inside.py")
            assert result is not None
            assert result.exists()

    def test_contained_uses_workspace_root_when_ctx_set(self, tmp_path):
        from runtime_v2.services.tool_executor import WORKSPACE_ROOT_CTX

        ws = tmp_path / "workspace"
        ws.mkdir()
        f = ws / "target.py"
        f.write_text("x")

        token = WORKSPACE_ROOT_CTX.set(str(ws))
        try:
            from runtime_v2.services.tool_executor import _contained
            result = _contained("target.py")
            assert result is not None
            assert result.exists()
            assert result == f.resolve()
        finally:
            WORKSPACE_ROOT_CTX.reset(token)

    def test_contained_rejects_escape_from_workspace(self, tmp_path):
        from runtime_v2.services.tool_executor import WORKSPACE_ROOT_CTX

        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("secret")

        token = WORKSPACE_ROOT_CTX.set(str(ws))
        try:
            from runtime_v2.services.tool_executor import _contained
            result = _contained("../outside.py")
            assert result is None
        finally:
            WORKSPACE_ROOT_CTX.reset(token)


# ---------------------------------------------------------------------------
# 9. Filesystem containment via _norm()
# ---------------------------------------------------------------------------

class TestNormResolution:
    """_norm() strips the effective root prefix."""

    def test_norm_strips_workspace_root_prefix(self, tmp_path):
        from runtime_v2.services.tool_executor import WORKSPACE_ROOT_CTX

        ws = tmp_path / "workspace"
        ws.mkdir()

        token = WORKSPACE_ROOT_CTX.set(str(ws))
        try:
            from runtime_v2.services.tool_executor import _norm
            full = str(ws / "subdir" / "file.py").replace("\\", "/")
            result = _norm(full)
            assert result == "subdir/file.py"
        finally:
            WORKSPACE_ROOT_CTX.reset(token)

    def test_norm_strips_ROOT_prefix_when_no_ctx(self):
        from runtime_v2.services.tool_executor import _ROOT, WORKSPACE_ROOT_CTX

        token = WORKSPACE_ROOT_CTX.set(None)
        try:
            from runtime_v2.services.tool_executor import _norm
            root_abs = str(_ROOT.resolve()).replace("\\", "/")
            full = root_abs + "/some/file.py"
            result = _norm(full)
            assert result == "some/file.py"
        finally:
            WORKSPACE_ROOT_CTX.reset(token)

    def test_norm_passthrough_for_unprefixed(self):
        from runtime_v2.services.tool_executor import WORKSPACE_ROOT_CTX

        token = WORKSPACE_ROOT_CTX.set(None)
        try:
            from runtime_v2.services.tool_executor import _norm
            result = _norm("AGENTS.md")
            assert result == "AGENTS.md"
        finally:
            WORKSPACE_ROOT_CTX.reset(token)


# ---------------------------------------------------------------------------
# 10. SWARM_WRITE_ROOT interaction
# ---------------------------------------------------------------------------

class TestWriteRootInteraction:
    """SWARM_WRITE_ROOT behaviour is preserved when workspace root is set."""

    def test_write_root_unset_allows_write_in_workspace(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SWARM_WRITE_ROOT", raising=False)
        from swarm_os.lib.mcp.filesystem import filesystem_handler

        ws = tmp_path / "ws"
        ws.mkdir()
        f = ws / "test.txt"
        result = filesystem_handler(
            {"operation": "write", "path": str(f), "content": "hello"},
            root=ws,
        )
        assert result.get("ok") is True
        assert f.read_text() == "hello"

    def test_write_root_restricts_write(self, tmp_path, monkeypatch):
        # SWARM_WRITE_ROOT is relative to the workspace root.
        # Write inside allowed → ok; write outside allowed → blocked.
        ws = tmp_path / "ws"
        ws.mkdir()
        allowed = ws / "allowed"
        allowed.mkdir()
        monkeypatch.setenv("SWARM_WRITE_ROOT", "allowed")
        from swarm_os.lib.mcp.filesystem import filesystem_handler

        # Write inside allowed → ok
        f1 = allowed / "ok.txt"
        r1 = filesystem_handler(
            {"operation": "write", "path": str(f1), "content": "ok"},
            root=ws,
        )
        assert r1.get("ok") is True
        # Write outside allowed (but inside workspace) → blocked
        f2 = ws / "denied.txt"
        r2 = filesystem_handler(
            {"operation": "write", "path": str(f2), "content": "no"},
            root=ws,
        )
        assert r2.get("ok") is False
        assert "outside SWARM_WRITE_ROOT" in r2.get("error", "")
