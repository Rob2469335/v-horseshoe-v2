"""
Module: adaptive_audit_log
Order: 10
Package: foundation.audit
Status: scaffold
Purpose: starter contract scaffold for ordered implementation.
"""

from __future__ import annotations

from collections.abc import Iterable

from swarm_os.foundation.events.event_record import EventRecord


class AdaptiveAuditLog:
    def __init__(self) -> None:
        self._entries: list[EventRecord] = []

    def write(self, event: EventRecord) -> None:
        self._entries.append(event)

    def write_many(self, events: Iterable[EventRecord]) -> None:
        for event in events:
            self.write(event)

    def snapshot(self) -> list[dict]:
        return [entry.to_dict() for entry in self._entries]