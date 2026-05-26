# swarm_os/kernel/snapshot.py
"""
Snapshot — save and load population state.
SNAPSHOT_DIR derived from settings.snapshot_dir (absolute, not CWD-relative).
Auto-cleans old snapshots, keeps last keep_last_n.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from swarm_os.config.settings import settings
from swarm_os.kernel.migrations import migrate_snapshot

log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 4


def _snapshot_dir() -> Path:
    """Always return an absolute path — safe regardless of CWD."""
    return Path(settings.snapshot_dir)


def save_snapshot(organisms, generation: int, keep_last_n: int = 10) -> Path:
    snap_dir = _snapshot_dir()
    snap_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "generation":       generation,
        "organisms": [
            {
                "id":      o.id,
                "fitness": o.fitness,
                "genome":  asdict(o.genome),
            }
            for o in organisms
        ],
    }

    path = snap_dir / f"snapshot_{generation:04d}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("saved snapshot generation=%d path=%s", generation, path)
    _cleanup(snap_dir, keep_last_n)
    return path


def _cleanup(snap_dir: Path, keep_last_n: int) -> None:
    snapshots = sorted(snap_dir.glob("snapshot_*.json"))
    for old in snapshots[:-keep_last_n] if len(snapshots) > keep_last_n else []:
        old.unlink()
        log.debug("deleted old snapshot %s", old.name)


def load_snapshot(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return migrate_snapshot(data)


def latest_snapshot() -> Optional[Path]:
    snap_dir  = _snapshot_dir()
    if not snap_dir.exists():
        return None
    snapshots = sorted(snap_dir.glob("snapshot_*.json"))
    return snapshots[-1] if snapshots else None
