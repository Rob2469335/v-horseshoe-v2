"""
Module: event_store
Order: 8
Package: foundation.events
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from collections.abc import Iterable

from swarm_os.foundation.events.event_record import EventRecord


class EventStore:
    def __init__(self) -> None:
        self._events: list[EventRecord] = []

    def append(self, event: EventRecord) -> EventRecord:
        self._events.append(event)
        return event

    def list_all(self) -> list[EventRecord]:
        return list(self._events)

    def extend(self, events: Iterable[EventRecord]) -> None:
        for event in events:
            self.append(event)
