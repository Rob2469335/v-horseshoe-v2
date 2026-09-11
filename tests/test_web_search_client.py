"""web_search pooled client must not be reused across event loops.

Regression (2026-09-11): 'tavily failed: Event loop is closed'. The module-level
httpx.AsyncClient was bound to the loop that first used it; a later call from a
different/closed loop (CLI asyncio.run in a thread, watch-loop) reused the dead
pool. _get_client now tracks the owning loop and rebuilds on a loop change.
"""

from __future__ import annotations

import asyncio

from swarm_os.lib.mcp import web_search as ws


async def _get():
    return ws._get_client()


def test_client_reused_within_same_loop():
    async def _twice():
        return ws._get_client(), ws._get_client()

    a, b = asyncio.run(_twice())
    assert a is b


def test_client_rebuilt_across_different_loops():
    a = asyncio.run(_get())  # loop A (closes after run)
    b = asyncio.run(_get())  # loop B — must NOT reuse A's client
    assert a is not b
