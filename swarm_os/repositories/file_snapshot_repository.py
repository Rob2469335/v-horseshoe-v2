from __future__ import annotations

import json
from pathlib import Path

from swarm_os.migrations import migrate_snapshot
from swarm_os.repositories.snapshot_repository import SnapshotRepository

class FileSnapshotRepository(SnapshotRepository):
    def __init__(self, root: str | Path = "swarm_os/data/snapshots"):
        self.root = Path(root)

    def save(self, payload: dict, generation: int) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"snapshot_{generation:04d}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def load(self, path: str | Path) -> dict:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return migrate_snapshot(data)

    def list(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(self.root.glob("snapshot_*.json"))

    def latest(self) -> Path | None:
        snaps = self.list()
        return max(snaps, key=lambda p: p.stat().st_mtime) if snaps else None

