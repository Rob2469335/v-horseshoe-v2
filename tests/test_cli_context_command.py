"""Tests for the /context read-only context-budget command."""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

from organism_console.command_registry import registry  # noqa: F401  (loads all command modules in order)
from organism_console._command_context import CommandContext
from organism_console._commands_system import cmd_context


def _make_ctx(history):
    state = SimpleNamespace(
        history=history, total_input_tokens=0, total_output_tokens=0
    )
    console = Console(file=io.StringIO(), width=200, force_terminal=False)
    return CommandContext(
        state=state,
        console=console,
        call_api=lambda *a, **k: None,
        run_prompt=lambda *a, **k: None,
        get_system_stats=lambda: {},
        installed_models=[],
    )


def _out(ctx) -> str:
    return ctx.console.file.getvalue()


def test_context_command_is_registered():
    assert "context" in registry.commands
    assert "context-window" in registry.commands["context"]["description"].lower()


def test_context_reports_limit_and_history():
    ctx = _make_ctx([{"role": "user", "content": "hi there"}])
    cmd_context(ctx, [])
    out = _out(ctx).replace(",", "")
    assert "16384" in out
    assert "Session history" in out


def test_context_warns_over_80_percent():
    # 55000 chars // 4 == 13750 tokens -> 83% of 16384
    ctx = _make_ctx([{"role": "assistant", "content": "x" * 55000}])
    cmd_context(ctx, [])
    out = _out(ctx)
    assert "83%" in out
    assert "capped on save" in out
