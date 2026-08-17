"""Tests for the opencode-parity CLI commands (/build /analyze /chat /undo /redo).

The undo/redo snapshot helpers operate on a git working tree, so these tests
build a throwaway repo in a temp dir and exercise snapshot_worktree() /
restore_snapshot() against it. Command wiring (registry registration, mode
switching, prompt badge) is exercised with the real registry + a stub context.
"""

import subprocess
from pathlib import Path

import pytest

from organism_console._commands_opencode import (
    EDITING_AGENTS,
    mode_badge,
    restore_snapshot,
    snapshot_worktree,
)


@pytest.fixture(autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock.

    These tests build a throwaway git repo and exercise snapshot/restore against
    real `git` invocations, so subprocess must NOT be mocked here. Module-scope
    fixtures take precedence over the conftest autouse one.
    """
    yield


@pytest.fixture()
def repo(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("k = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "-q"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return tmp_path


def _git(path: Path, *args) -> str:
    res = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout


def test_undo_restores_modified_tracked_file(repo: Path):
    # Pre-existing uncommitted edits are the state undo must restore.
    (repo / "a.py").write_text("pre-agent x = 1\n", encoding="utf-8")
    (repo / "keep.py").write_text("pre-agent k = 1\n", encoding="utf-8")
    snap = snapshot_worktree(repo)
    # Agent edits both files further:
    (repo / "a.py").write_text("x = 999\n", encoding="utf-8")
    (repo / "keep.py").write_text("k = 42\n", encoding="utf-8")
    restored = restore_snapshot(snap, repo)
    assert "a.py" in restored and "keep.py" in restored
    assert (repo / "a.py").read_text(encoding="utf-8") == "pre-agent x = 1\n"
    assert (repo / "keep.py").read_text(encoding="utf-8") == "pre-agent k = 1\n"


def test_undo_removes_agent_created_untracked_file(repo: Path):
    snap = snapshot_worktree(repo)
    (repo / "new.py").write_text("created by agent\n", encoding="utf-8")
    (repo / "logs").mkdir()
    (repo / "logs" / "x.log").write_text("log\n", encoding="utf-8")
    restored = restore_snapshot(snap, repo)
    assert "new.py" in restored and "logs/" in restored
    assert not (repo / "new.py").exists()
    assert not (repo / "logs").exists()


def test_undo_preserves_preexisting_untracked_file(repo: Path):
    (repo / "notes.md").write_text("user's own file\n", encoding="utf-8")
    snap = snapshot_worktree(repo)
    (repo / "agent.py").write_text("agent file\n", encoding="utf-8")
    restore_snapshot(snap, repo)
    assert (repo / "notes.md").exists()
    assert not (repo / "agent.py").exists()


def test_undo_restores_added_tracked_file(repo: Path):
    (repo / "staged.py").write_text("pre-agent content\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "staged.py"], cwd=repo, check=True, capture_output=True
    )
    snap = snapshot_worktree(repo)
    (repo / "staged.py").write_text("post-agent content\n", encoding="utf-8")
    restore_snapshot(snap, repo)
    assert (repo / "staged.py").read_text(encoding="utf-8") == "pre-agent content\n"


def test_commands_registered():
    from organism_console.command_registry import registry

    for name in ("build", "analyze", "chat", "undo", "redo", "modes"):
        assert name in registry.commands, name
    assert "coder" in EDITING_AGENTS
    assert "code_analyzer" not in EDITING_AGENTS


def test_mode_badge_renders_without_error():
    badge = mode_badge("coder")
    assert "BUILD" in badge
    assert mode_badge("coordinator") != badge


def test_mode_commands_switch_active_agent(tmp_path):
    from organism_console.command_registry import registry
    from organism_console._command_context import CommandContext
    from organism_console.state_store import SessionState
    import io
    from rich.console import Console

    state = SessionState(tmp_path / "session.json")
    state.history = []
    state.save = lambda **kwargs: None  # avoid the async-save thread racing cleanup
    console = Console(file=io.StringIO(), force_terminal=False)
    ctx = CommandContext(
        state=state,
        console=console,
        call_api=None,
        run_prompt=None,
        get_system_stats=None,
        installed_models=["qwen3.5-4b"],
    )
    registry.commands["build"]["func"](ctx, [])
    assert state.active_agent == "coder"
    registry.commands["analyze"]["func"](ctx, [])
    assert state.active_agent == "code_analyzer"
    registry.commands["chat"]["func"](ctx, [])
    assert state.active_agent == "coordinator"


def test_run_agentic_sets_last_prompt_on_cli_context(monkeypatch, tmp_path):
    """run_agentic must write last_prompt/undo_stack onto the session state
    object directly (CLIContext IS the SessionState). The pre-fix closure used
    `ctx.state.last_prompt`, but CLIContext has no `.state` attribute — every
    CLI prompt crashed with AttributeError after the SOTA upgrade."""
    from organism_console.cli import run_agentic
    from organism_console.state_store import SessionState
    import io
    from rich.console import Console

    state = SessionState(tmp_path / "session.json")
    state.save = lambda **kwargs: None
    state.console = Console(file=io.StringIO(), force_terminal=False)
    state.active_agent = "coder"
    state.history = []

    def _fake_stream(*_args, **_kwargs):
        return [{"role": "assistant", "content": "done"}]

    monkeypatch.setattr("organism_console.cli.stream_prompt_with_retry", _fake_stream)
    monkeypatch.setattr(
        "organism_console._commands_opencode.snapshot_worktree",
        lambda *_, **__: {"test": "snap"},
    )
    monkeypatch.setattr(
        "organism_console._commands_opencode.build_run_diff", lambda *_, **__: []
    )

    result = run_agentic(state, "fix the bug", json_flag=True)
    assert result["content"] == "done"
    assert state.last_prompt == "fix the bug"
    assert state.undo_stack == [{"test": "snap"}]


def test_run_agentic_non_editing_agent_skips_snapshot(monkeypatch, tmp_path):
    from organism_console.cli import run_agentic
    from organism_console.state_store import SessionState
    import io
    from rich.console import Console

    state = SessionState(tmp_path / "session.json")
    state.save = lambda **kwargs: None
    state.console = Console(file=io.StringIO(), force_terminal=False)
    state.active_agent = "coordinator"
    state.history = []

    def _fake_stream(*_args, **_kwargs):
        return [{"role": "assistant", "content": "hi"}]

    monkeypatch.setattr("organism_console.cli.stream_prompt_with_retry", _fake_stream)
    monkeypatch.setattr(
        "organism_console._commands_opencode.snapshot_worktree",
        lambda *_, **__: {"should": "not be called"},
    )

    run_agentic(state, "hello", json_flag=True)
    assert state.last_prompt == "hello"
    assert state.undo_stack == []
