from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from swarm_os.migrations import migrate_snapshot

SNAPSHOT_DIR = Path("swarm_os/data/snapshots")
SNAPSHOT_VERSION = 2

def save_snapshot(kernel, generation: int) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "generation": generation,
        "organisms": [
            {
                "id": o.id,
                "fitness": o.fitness,
                "genome": asdict(o.genome),
            }
            for o in kernel
        ],
    }
    path = SNAPSHOT_DIR / f"snapshot_{generation:04d}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

def load_snapshot(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return migrate_snapshot(data)

