# swarm_os/services/status.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SwarmStatus:
    ready:       bool
    events_path: Path
    event_count: int


def get_status(events_dir: Path) -> SwarmStatus:
    """Return current system status based on events directory."""
    events_path = events_dir / "events.jsonl"
    event_count = 0

    if events_path.exists():
        try:
            with events_path.open("r", encoding="utf-8") as f:
                event_count = sum(1 for line in f if line.strip())
        except OSError:
            pass

    return SwarmStatus(
        ready       = True,
        events_path = events_path,
        event_count = event_count,
    )
