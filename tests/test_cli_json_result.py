"""CLI one-shot result truthfulness (2026-09-13).

Live defect: a failed run ("All retry attempts exhausted.") made the one-shot
CLI return `ok: true` with the PREVIOUS session's answer as `content`, because
`run_agentic` read `result_history[-1]` and the retry wrapper returns the input
history unchanged on failure. Any prompt-generated data would be mislabelled.
"""

from __future__ import annotations

import io

from rich.console import Console

import organism_console.cli as cli
from organism_console.state_store import SessionState

_STALE = "STALE PREVIOUS ANSWER"


def _ctx(tmp_path) -> SessionState:
    ctx = SessionState(tmp_path / "session.json")
    ctx.console = Console(file=io.StringIO(), force_terminal=False)
    ctx.active_agent = "coordinator"  # non-editing -> no worktree snapshot
    ctx.active_model = "robs4b"
    ctx.history = [{"role": "assistant", "content": _STALE}]
    ctx.undo_stack = []
    ctx.last_prompt = ""
    return ctx


def test_failed_run_returns_empty_not_stale(tmp_path, monkeypatch):
    """Retry-exhausted returns the input history unchanged -> no new answer."""
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(cli, "stream_prompt_with_retry", lambda c, a, p, h: list(h))
    res = cli.run_agentic(ctx, "a brand new question")
    assert res["content"] == ""
    assert res["ok"] is False


def test_successful_run_returns_only_this_runs_answer(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    def fake(c, a, p, h):
        return list(h) + [{"role": "assistant", "content": "FRESH ANSWER"}]

    monkeypatch.setattr(cli, "stream_prompt_with_retry", fake)
    res = cli.run_agentic(ctx, "a brand new question")
    assert res["content"] == "FRESH ANSWER"
    assert res["ok"] is True


def test_system_failure_final_is_not_ok(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    def fake(c, a, p, h):
        return list(h) + [
            {
                "role": "assistant",
                "content": "Task FAILED: coordinator could not produce a final.",
            }
        ]

    monkeypatch.setattr(cli, "stream_prompt_with_retry", fake)
    res = cli.run_agentic(ctx, "x")
    assert res["ok"] is False
