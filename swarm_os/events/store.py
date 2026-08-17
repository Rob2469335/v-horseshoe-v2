from __future__ import annotations

import json
from pathlib import Path
from .envelope import EventEnvelope


class EventStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "events.jsonl"

    def append(self, event: EventEnvelope) -> None:
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        import logging
        from filelock import FileLock

        lock_path = self.path.with_suffix(".lock")
        try:
            with FileLock(lock_path, timeout=5.0):
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
        except Exception as e:
            logging.getLogger(__name__).error("Failed to append event: %s", e)

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        items = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    # BUG FIX: Wrap json.loads in try/except.
                    # Previously, a single corrupted line would crash the entire read_all() method,
                    # making the entire event log unreadable.
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        import logging

                        logging.getLogger(__name__).warning(
                            "Skipping corrupted event line: %r", line[:80]
                        )
                        continue
        return items
