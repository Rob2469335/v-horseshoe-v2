"""Loop-bound pooled async client helper (rebuilds across event loops)."""

from __future__ import annotations

import asyncio

from swarm_os.lib.loop_bound import LoopBoundAsyncClient


class _FakeClient:
    def __init__(self):
        self.is_closed = False


def test_reused_on_same_loop():
    bound = LoopBoundAsyncClient(_FakeClient)

    async def grab():
        return bound.get()

    loop = asyncio.new_event_loop()
    try:
        a = loop.run_until_complete(grab())
        b = loop.run_until_complete(grab())
    finally:
        loop.close()
    assert a is b


def test_rebuilt_on_new_loop():
    bound = LoopBoundAsyncClient(_FakeClient)
    ids = []

    async def grab():
        ids.append(id(bound.get()))

    asyncio.run(grab())
    asyncio.run(grab())
    assert ids[0] != ids[1]


def test_rebuilt_when_owner_loop_closed():
    bound = LoopBoundAsyncClient(_FakeClient)

    async def grab():
        return id(bound.get())

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
    assert c1 != c2


def test_rebuilt_when_client_closed():
    bound = LoopBoundAsyncClient(_FakeClient)

    async def grab():
        return bound.get()

    a = asyncio.run(grab())
    a.is_closed = True
    b = asyncio.run(grab())
    assert a is not b


def test_reset_drops_reference():
    bound = LoopBoundAsyncClient(_FakeClient)
    a = asyncio.run(_grab(bound))
    bound.reset()
    b = asyncio.run(_grab(bound))
    assert a is not b


async def _grab(bound):
    return bound.get()
