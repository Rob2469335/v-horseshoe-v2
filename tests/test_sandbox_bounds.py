"""sandbox_bounds(): the effective agent tool bounds + the fail-closed write guard.

Regression context (2026-09-15). A 14-task SWE-rebench batch completed with ZERO
source edits in every instance. Root cause was NOT capability: the backend was
started with ``SWARM_WORKSPACE_ROOT`` (so reads were allowed) but the ambient
``SWARM_WRITE_ROOT=data/curriculum_fix`` - a RELATIVE path that
``filesystem.py::_within_write_root`` resolves UNDER the workspace root - so
every patch answered "path is outside SWARM_WRITE_ROOT" and the agent looped
until its turn budget was gone. The run looked healthy because the only
preflight check was a READ.

``sandbox_bounds()`` is the signal that makes that state visible before a batch
runs, and ``write_covers_workspace=False`` is the abort condition.
"""

from __future__ import annotations

from swarm_os.lib.paths import sandbox_bounds


def test_unset_write_root_means_workspace_is_writable(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
    monkeypatch.delenv("SWARM_WRITE_ROOT", raising=False)
    b = sandbox_bounds()
    assert b["write_root"] is None
    assert b["write_covers_workspace"] is True


def test_write_root_equal_to_workspace_covers_it(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("SWARM_WRITE_ROOT", str(ws))
    b = sandbox_bounds()
    assert b["write_root"] == str(ws.resolve())
    assert b["write_covers_workspace"] is True


def test_relative_subdir_write_root_does_not_cover_workspace(monkeypatch, tmp_path):
    """THE 2026-09-15 BUG: a RELATIVE write root resolves UNDER the workspace."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("SWARM_WRITE_ROOT", "data/curriculum_fix")
    b = sandbox_bounds()
    assert b["write_root"] == str((ws / "data" / "curriculum_fix").resolve())
    assert b["write_covers_workspace"] is False


def test_absolute_write_root_outside_workspace_does_not_cover(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("SWARM_WRITE_ROOT", str(other))
    assert sandbox_bounds()["write_covers_workspace"] is False


def test_workspace_root_is_reported(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
    monkeypatch.delenv("SWARM_WRITE_ROOT", raising=False)
    assert sandbox_bounds()["workspace_root"] == str(ws.resolve())


def test_invalid_workspace_root_fails_closed_never_raises(monkeypatch):
    """A status endpoint must not 500 on a bad env value - report and fail closed."""
    monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "relative/not/absolute")
    monkeypatch.delenv("SWARM_WRITE_ROOT", raising=False)
    b = sandbox_bounds()
    assert b["write_covers_workspace"] is False
    assert b["error"]


def test_status_schema_carries_sandbox() -> None:
    """The guard must actually reach /status, or the preflight can't read it."""
    from swarm_os.api.schemas import StatusResponse

    assert "sandbox" in StatusResponse.model_fields
