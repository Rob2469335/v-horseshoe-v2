"""Tests for the SOTA CLI upgrades: permission model, run-diff review, toasts.

Covers:
  - permissions module: default policies, set_policy, auto-mode should_ask/blocked
  - build_run_diff: per-file unified diff of what an agent changed since snapshot
  - /diff-last /permissions /auto /toasts command registration + wiring
"""
import subprocess
from pathlib import Path

import pytest

from organism_console._commands_opencode import (
    build_run_diff,
    render_run_diff,
    snapshot_worktree,
)


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Let these tests use real `git` (see tests/test_cli_opencode.py)."""
    yield


@pytest.fixture()
def repo(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\nx = 2\nx = 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


# --------------------------------------------------------------------------
# permissions module
# --------------------------------------------------------------------------

def test_permissions_defaults_and_roundtrip(tmp_path, monkeypatch):
    from organism_console import permissions as perms
    monkeypatch.setattr(perms, "PERMISSIONS_FILE", tmp_path / "perms.json")
    perms.reset()
    assert perms.policy_for("sandbox_repl") == "ask"
    assert perms.policy_for("read") == "allow"
    assert perms.should_ask("read") is False
    assert perms.should_ask("sandbox_repl") is True
    assert perms.blocked("read") is False


def test_permissions_auto_mode(tmp_path, monkeypatch):
    from organism_console import permissions as perms
    monkeypatch.setattr(perms, "PERMISSIONS_FILE", tmp_path / "perms.json")
    perms.reset()
    perms.set_auto_mode(True)
    # auto mode auto-approves anything not explicitly denied
    assert perms.should_ask("sandbox_repl") is False
    assert perms.should_ask("approval") is False
    perms.set_policy("approval", "deny")
    # deny always wins even in auto mode
    assert perms.should_ask("approval") is True
    assert perms.blocked("approval") is True


def test_permissions_set_policy_validation(tmp_path, monkeypatch):
    from organism_console import permissions as perms
    monkeypatch.setattr(perms, "PERMISSIONS_FILE", tmp_path / "perms.json")
    perms.reset()
    assert perms.set_policy("screen", "deny") is True
    assert perms.policy_for("screen") == "deny"
    assert perms.set_policy("screen", "bogus") is False
    assert perms.set_policy("", "allow") is False


# --------------------------------------------------------------------------
# build_run_diff
# --------------------------------------------------------------------------

def test_run_diff_shows_only_agent_changes(repo: Path):
    snap = snapshot_worktree(repo)
    # Agent edits a tracked file, creates a new untracked file.
    (repo / "a.py").write_text("x = 1\nx = 2\nx = 99\n", encoding="utf-8")
    (repo / "new.py").write_text("created\n", encoding="utf-8")
    results = build_run_diff(snap, repo)
    by_path = {r["path"]: r for r in results}
    assert set(by_path) == {"a.py", "new.py"}
    assert by_path["a.py"]["added"] == 1
    assert by_path["a.py"]["removed"] == 1
    assert by_path["new.py"]["added"] == 1
    assert by_path["new.py"]["removed"] == 0


def test_run_diff_ignores_pre_existing_edits(repo: Path):
    # A file already modified BEFORE the snapshot must not show as a change.
    (repo / "a.py").write_text("x = 1\nx = 42\nx = 3\n", encoding="utf-8")
    snap = snapshot_worktree(repo)
    results = build_run_diff(snap, repo)
    assert results == []
    # And if the agent further edits it, only the delta is reported.
    (repo / "a.py").write_text("x = 1\nx = 42\nx = 43\n", encoding="utf-8")
    results = build_run_diff(snap, repo)
    assert len(results) == 1
    assert results[0]["path"] == "a.py"
    assert results[0]["added"] == 1
    assert results[0]["removed"] == 1


def test_run_diff_untracked_edited_in_place(repo: Path):
    (repo / "scratch.py").write_text("v1\n", encoding="utf-8")
    snap = snapshot_worktree(repo)
    (repo / "scratch.py").write_text("v2\n", encoding="utf-8")
    results = build_run_diff(snap, repo)
    assert len(results) == 1
    assert results[0]["path"] == "scratch.py"
    assert results[0]["added"] == 1
    assert results[0]["removed"] == 1


def test_run_diff_agent_deletes_file(repo: Path):
    (repo / "doomed.py").write_text("bye\n", encoding="utf-8")
    snap = snapshot_worktree(repo)
    (repo / "doomed.py").unlink()
    results = build_run_diff(snap, repo)
    assert len(results) == 1
    assert results[0]["path"] == "doomed.py"
    assert results[0]["added"] == 0
    assert results[0]["removed"] == 1


def test_render_run_diff_output(repo: Path):
    snap = snapshot_worktree(repo)
    (repo / "a.py").write_text("x = 9\nx = 2\nx = 3\n", encoding="utf-8")
    rendered = render_run_diff(build_run_diff(snap, repo))
    assert "a.py" in rendered
    assert "+x = 9" in rendered
    assert "-x = 1" in rendered


# --------------------------------------------------------------------------
# command registration / wiring
# --------------------------------------------------------------------------

def test_sota_commands_registered():
    from organism_console.command_registry import registry
    for name in ("permissions", "auto", "toasts", "diff-last", "changes"):
        assert name in registry.commands, f"/{name} not registered"


def test_diff_last_wiring(tmp_path, monkeypatch):
    import io
    from rich.console import Console
    from organism_console.command_registry import registry
    from organism_console._command_context import CommandContext
    from organism_console.state_store import SessionState

    state = SessionState(tmp_path / "session.json")
    state.save = lambda **kwargs: None
    console = Console(file=io.StringIO(), force_terminal=False)
    ctx = CommandContext(state=state, console=console, call_api=None, run_prompt=None,
                         get_system_stats=None, installed_models=["qwen3.5-4b"])
    registry.commands["diff-last"]["func"](ctx, [])
    out = console.file.getvalue()
    assert "No previous run snapshot" in out


def test_permissions_commands_wire(tmp_path, monkeypatch):
    import io
    from rich.console import Console
    from organism_console.command_registry import registry
    from organism_console._command_context import CommandContext
    from organism_console.state_store import SessionState
    from organism_console import permissions as perms
    monkeypatch.setattr(perms, "PERMISSIONS_FILE", tmp_path / "perms.json")
    perms.reset()

    state = SessionState(tmp_path / "session.json")
    state.save = lambda **kwargs: None
    console = Console(file=io.StringIO(), force_terminal=False)
    ctx = CommandContext(state=state, console=console, call_api=None, run_prompt=None,
                         get_system_stats=None, installed_models=["qwen3.5-4b"])

    registry.commands["permissions"]["func"](ctx, [])
    assert "sandbox_repl" in console.file.getvalue()

    registry.commands["permissions"]["func"](ctx, ["screen", "deny"])
    assert perms.policy_for("screen") == "deny"

    registry.commands["auto"]["func"](ctx, ["on"])
    assert perms.auto_mode() is True

    registry.commands["toasts"]["func"](ctx, ["off"])
    assert state.toasts_enabled is False


def test_session_state_toasts_flag(tmp_path):
    from organism_console.state_store import SessionState
    state = SessionState(tmp_path / "session.json")
    assert state.toasts_enabled is True
    state.toasts_enabled = False
    state.save(sync=True)
    state2 = SessionState(tmp_path / "session.json")
    assert state2.toasts_enabled is False


def test_notifications_noop_safe():
    from organism_console import notifications as notif
    notif.set_enabled(False)
    notif.notify("t", "b")  # must not raise
    notif.set_enabled(True)
