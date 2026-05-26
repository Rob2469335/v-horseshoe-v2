from __future__ import annotations

import json
from pathlib import Path
from .envelope import EventEnvelope


class EventStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'events.jsonl'

    def append(self, event: EventEnvelope) -> None:
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + '\n')

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        items = []
        with self.path.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items
