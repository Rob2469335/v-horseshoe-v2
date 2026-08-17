"""Regression tests for the message-bus fan-out fix (#13):

The _process_events loop previously did `await asyncio.gather(*tasks)` for one
event's handlers BEFORE pulling the next event off the queue. A slow subscriber
therefore head-of-line-blocked every other topic: a FAST event queued behind a
SLOW event's handler did not start until the slow handler finished.

After the fix the loop dispatches handler tasks without awaiting them, so a
fast event is handled concurrently with a slow one already in flight.
"""

import asyncio
import time

import pytest

from swarm_os.core.message_bus import Event, MessageBus


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_block_other_topics():
    bus = MessageBus()
    started: dict[str, float] = {}

    async def slow_handler(event: Event) -> None:
        started["slow"] = time.monotonic()
        await asyncio.sleep(0.6)

    async def fast_handler(event: Event) -> None:
        started["fast"] = time.monotonic()

    bus.subscribe("slow_topic", slow_handler)
    bus.subscribe("fast_topic", fast_handler)

    await bus.start()
    await bus.publish(Event(topic="slow_topic", payload={}))
    await bus.publish(Event(topic="fast_topic", payload={}))
    # Give the loop time to pick up both events. The slow handler is still
    # sleeping (0.6s), so the fast one has clearly not been block-joined.
    await asyncio.sleep(0.2)
    assert "fast" in started, (
        "FAST event was blocked behind a slow subscriber on another topic"
    )
    await bus.stop()


@pytest.mark.asyncio
async def test_stop_cancels_pending_handlers():
    bus = MessageBus()

    async def slow_handler(event: Event) -> None:
        await asyncio.sleep(5.0)

    bus.subscribe("topic", slow_handler)
    await bus.start()
    await bus.publish(Event(topic="topic", payload={}))
    await asyncio.sleep(0.05)
    # stop() must not hang waiting for the 5s handler to finish.
    await bus.stop()
    assert not bus._pending_tasks
