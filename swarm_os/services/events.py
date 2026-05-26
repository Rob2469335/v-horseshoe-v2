from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

@dataclass(frozen=True)
class EventRecord:
    path: str
    kind: str
    payload: str


def write_event(events_dir: Path, kind: str, payload: str) -> Path:
    events_dir.mkdir(parents=True, exist_ok=True)
    target = events_dir / "events.log"
    target.write_text(f"{kind}`t{payload}`n", encoding="utf-8", errors="ignore")
    return target


def list_events(events_dir: Path) -> List[EventRecord]:
    target = events_dir / "events.log"
    if not target.exists():
        return []
    lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    out: List[EventRecord] = []
    for line in lines:
        if "`t" not in line:
            continue
        kind, payload = line.split("`t", 1)
        out.append(EventRecord(path=str(target), kind=kind, payload=payload))
    return out
