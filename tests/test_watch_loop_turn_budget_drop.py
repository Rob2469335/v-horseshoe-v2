"""Regression: watch_loop must not silently drop a turn-budget reflexion record
when no event loop is running.

`_handle_turn_budget` has two scheduling paths: the main loop (threadsafe
`call_soon_threadsafe`) and, when there is no main loop, the current running
loop via `asyncio.get_running_loop()`. With no running loop that raised
`RuntimeError`, which was swallowed with a bare `pass` — the reflexion record
vanished. Same bug class/fix as genetic_mutation_loop (be7d33f): log it.
"""

from __future__ import annotations

from unittest.mock import patch

import swarm_os.services.watch_loop as wl


def _bare_loop_object():
    # Bypass __init__ (needs a real engine); _handle_turn_budget only touches
    # _main_loop and _bg_tasks.
    obj = object.__new__(wl.WatchLoop)
    obj._main_loop = None
    obj._bg_tasks = set()
    return obj


def test_turn_budget_drop_is_logged_when_no_running_loop():
    obj = _bare_loop_object()
    data = {"payload": {"agent_id": "code_analyzer", "prompt": "analyze the codebase"}}
    with patch.object(wl.log, "warning") as warn:
        obj._handle_turn_budget(data)
    joined = " ".join(str(c.args) for c in warn.call_args_list).lower()
    assert "no running event loop" in joined or "dropped" in joined, (
        "the dropped turn-budget reflexion must be logged, not swallowed; "
        f"got: {warn.call_args_list}"
    )
