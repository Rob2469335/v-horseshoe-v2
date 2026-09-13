"""CLI surface for file-based subagents and their config-evolution proposals."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import runtime_v2.services.subagent_registry as reg
import runtime_v2.services.subagent_evolution as se
from organism_console._command_context import CommandContext
from organism_console.command_registry import registry
from organism_console.state_store import SessionState

_TUNER = """\
---
name: tuner
description: A tunable helper.
tools: filesystem, final
model: robs4b
mutable: true
budget: 4096
---
Tuner body.
"""


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    root = tmp_path / ".rob" / "agents"
    monkeypatch.setattr(reg, "_AGENTS_ROOT", str(root))
    monkeypatch.setattr(reg, "_CACHE", (0.0, []))
    monkeypatch.setattr(se, "STAGED_DIR", tmp_path / "staged")
    monkeypatch.setattr(se, "_agents_root", lambda: root)
    monkeypatch.setattr(se, "score", lambda g: 0.5)
    monkeypatch.delenv("SWARM_SUBAGENT_EVOLUTION", raising=False)
    state = SessionState(tmp_path / "session.json")
    console = Console(file=io.StringIO(), force_terminal=False)
    context = CommandContext(
        state=state,
        console=console,
        call_api=None,
        run_prompt=None,
        get_system_stats=None,
        installed_models=["qwen3.5-4b"],
    )
    context._agents_root = root  # for convenience in tests
    return context


def _out(ctx) -> str:
    return ctx.console.file.getvalue()


def _write(root, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tuner.md").write_text(content, encoding="utf-8")


def test_subagents_command_is_registered():
    assert "subagents" in registry.commands


def test_list_shows_file_agents_and_mutable(ctx):
    _write(ctx._agents_root, _TUNER)
    registry.commands["subagents"]["func"](ctx, [])
    out = _out(ctx)
    assert "tuner" in out
    assert "yes" in out  # mutable


def test_list_empty_is_graceful(ctx):
    registry.commands["subagents"]["func"](ctx, [])
    assert "No file-based subagents" in _out(ctx)


def test_propose_reports_disabled_flag(ctx):
    _write(ctx._agents_root, _TUNER)
    registry.commands["subagents"]["func"](ctx, ["propose", "tuner"])
    assert "evolution is off" in _out(ctx)


def test_usage_when_name_missing(ctx):
    registry.commands["subagents"]["func"](ctx, ["promote"])
    assert "Usage:" in _out(ctx)


def test_rollback_without_backup_is_reported(ctx):
    registry.commands["subagents"]["func"](ctx, ["rollback", "ghost"])
    assert "Rollback failed" in _out(ctx)
