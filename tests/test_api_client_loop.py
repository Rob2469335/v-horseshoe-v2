"""Regression: the CLI's pooled async client must not be reused across event
loops.

httpx.AsyncClient is bound to the loop that created it. The CLI runs streams
from more than one loop (asyncio.run in a worker thread), so before loop-identity
tracking a second loop received the first loop's client and failed with
"Event loop is closed" — the same class as vector_store.py (d94e177) and
routes.py's _PROBE_CLIENT_LOOP.
"""

from __future__ import annotations

import asyncio

import organism_console.api_client as ac


def _reset():
    ac._async_client = None
    ac._async_client_loop = None


def test_client_rebuilt_on_new_event_loop():
    _reset()
    ids: list[int] = []

    async def grab():
        ids.append(id(ac._get_async_client()))

    asyncio.run(grab())
    asyncio.run(grab())  # a fresh loop
    assert len(ids) == 2
    assert ids[0] != ids[1], "client must be rebuilt for a new event loop"


def test_client_rebuilt_when_owner_loop_closed():
    _reset()

    async def grab():
        return id(ac._get_async_client())

    loop1 = asyncio.new_event_loop()
    try:
        c1 = loop1.run_until_complete(grab())
    finally:
        loop1.close()
    loop2 = asyncio.new_event_loop()
    try:
        c2 = loop2.run_until_complete(grab())
    finally:
        loop2.close()
    assert c1 != c2, "client must be rebuilt when its owning loop is closed"
