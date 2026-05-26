from __future__ import annotations

from pathlib import Path

SNAPSHOT_DIR = Path("swarm_os/data/snapshots")

def list_snapshots() -> list[Path]:
    if not SNAPSHOT_DIR.exists():
        return []
    return sorted(SNAPSHOT_DIR.glob("snapshot_*.json"))

def latest_snapshot() -> Path | None:
    snaps = list_snapshots()
    return max(snaps, key=lambda p: p.stat().st_mtime) if snaps else None
