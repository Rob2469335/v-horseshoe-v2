"""Regression: fallback_manager's pooled probe client must not be reused across
event loops.

httpx.AsyncClient is bound to the loop that created it, and so is the asyncio
lock that guards it. Before loop-identity tracking, a caller on a second loop got
a client (and/or lock) bound to the first and failed with "Event loop is closed"
— the same class as vector_store.py (d94e177) and routes.py's
_PROBE_CLIENT_LOOP.
"""

from __future__ import annotations

import asyncio

import runtime_v2.services.fallback_manager as fm


def _reset():
    fm._http_client = None
    fm._http_client_loop = None
    fm._client_lock = None
    fm._client_lock_loop = None


def test_client_rebuilt_on_new_event_loop():
    _reset()
    ids: list[int] = []

    async def grab():
        ids.append(id(await fm.get_http_client()))

    asyncio.run(grab())
    asyncio.run(grab())  # a fresh loop
    assert len(ids) == 2
    assert ids[0] != ids[1], "client must be rebuilt for a new event loop"


def test_client_rebuilt_when_owner_loop_closed():
    _reset()

    async def grab():
        return id(await fm.get_http_client())

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
