"""One-shot `--json` runs must not replay/persist conversation history.

Regression for the 2026-09-13 self-reinforcing loop: each one-shot run wrote its
final back into `.session.json`, and the next run replayed it, so the model
repeated its own prior loop-diagnosis. Interactive/REPL runs must still persist.
"""

from __future__ import annotations

import io

from rich.console import Console

import organism_console.cli as cli
from organism_console.state_store import SessionState

_STALE = "PREVIOUS ASSISTANT LOOP DIAGNOSIS"


def _ctx(tmp_path) -> SessionState:
    ctx = SessionState(tmp_path / "session.json")
    ctx.console = Console(file=io.StringIO(), force_terminal=False)
    ctx.active_agent = "coordinator"
    ctx.active_model = "robs4b"
    ctx.history = [{"role": "assistant", "content": _STALE}]
    ctx.undo_stack = []
    ctx.last_prompt = ""
    return ctx


def _fake_capture(seen):
    def fake(c, a, p, h):
        seen["h"] = list(h)
        return list(h) + [{"role": "assistant", "content": "FRESH ANSWER"}]

    return fake


def test_oneshot_does_not_inherit_or_persist_history(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(cli, "stream_prompt_with_retry", _fake_capture(seen))
    cli.run_agentic(ctx, "new question", json_flag=True)
    assert not any(_STALE in str(m.get("content", "")) for m in seen["h"]), (
        "one-shot run must not replay the prior assistant output"
    )
    assert ctx.history == [], "one-shot run must not persist history"


def test_interactive_still_persists_history(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    seen: dict = {}
    monkeypatch.setattr(cli, "stream_prompt_with_retry", _fake_capture(seen))
    cli.run_agentic(ctx, "new question", json_flag=False)
    assert any(_STALE in str(m.get("content", "")) for m in seen["h"]), (
        "interactive run must keep its persistent history"
    )
    assert any(m.get("content") == "FRESH ANSWER" for m in ctx.history)
