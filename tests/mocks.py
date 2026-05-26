from __future__ import annotations

from pathlib import Path

class MockSnapshotRepository:
    def __init__(self):
        self.saved = []
        self.loaded = None

    def save(self, payload: dict, generation: int) -> Path:
        self.saved.append((generation, payload))
        return Path(f"snapshot_{generation:04d}.json")

    def load(self, path: str | Path) -> dict:
        return self.loaded

    def list(self) -> list[Path]:
        return []

    def latest(self) -> Path | None:
        return None
