"""The REPL must not be locked out by a stale ``last_goal_result``.

`/goal` stamps ``ctx.last_goal_result``; the REPL prompt-dispatch guard
(``getattr(ctx, "last_goal_result", None) is None``) skips ``run_agentic`` while
it is set, so without clearing it each turn every later typed prompt is
silently dropped. ``cli._reset_goal_result`` performs that clear; ``cli.main``'s
REPL loop must call it.
"""

import inspect
from types import SimpleNamespace

from organism_console import cli


def test_reset_goal_result_removes_stale_attr():
    ctx = SimpleNamespace(
        last_goal_result={"content": "x", "files_changed": [], "passed": True}
    )
    cli._reset_goal_result(ctx)
    # The prompt-dispatch guard now evaluates True -> the next prompt runs.
    assert getattr(ctx, "last_goal_result", None) is None


def test_reset_goal_result_is_noop_when_absent():
    ctx = SimpleNamespace()
    cli._reset_goal_result(ctx)  # must not raise
    assert getattr(ctx, "last_goal_result", None) is None


def test_repl_loop_resets_before_reading_input():
    """Source-pin: the REPL while-loop must clear the stale result.

    This asserts the wiring only (that main() calls the helper inside the REPL
    loop); the behaviour of the clear itself is covered above.
    """
    src = inspect.getsource(cli.main)
    assert "while True:" in src, "main() REPL loop shape changed"
    repl_loop = src.split("while True:", 1)[1]
    assert "_reset_goal_result(ctx)" in repl_loop
