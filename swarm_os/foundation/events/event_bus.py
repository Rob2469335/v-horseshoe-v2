"""
Module: event_bus
Order: 9
Package: foundation.events
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from swarm_os.foundation.events.event_record import EventRecord

EventHandler = Callable[[EventRecord], Any]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: EventRecord) -> None:
        for handler in self._handlers.get(event.event_type, []):
            handler(event)
