from __future__ import annotations

from pathlib import Path
from typing import Any

from swarm_os.snapshot_index import latest_snapshot, list_snapshots

def build_status(current_generation: int | None = None, scenario: str | None = None) -> dict[str, Any]:
    latest = latest_snapshot()
    return {
        "scenario": scenario,
        "generation": current_generation,
        "snapshot_count": len(list_snapshots()),
        "latest_snapshot": str(latest) if latest else None,
    }

