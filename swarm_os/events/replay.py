from __future__ import annotations

from .store import EventStore


def replay_events(store: EventStore) -> list[dict]:
    return store.read_all()
