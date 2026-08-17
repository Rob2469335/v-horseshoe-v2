"""Snapshot migration re-export.

The canonical snapshot-migration logic lives in `swarm_os/migrations.py`
(v1→v4: fitness, named Genome fields, CognitivePolicy, tool_genes backfill).
This module exists for backward compatibility with callers that import
`swarm_os.kernel.migrations.migrate_snapshot` (kernel/restore.py, snapshot.py,
simulation_service.py) — it delegates to the canonical implementation so all
load paths migrate to the SAME current version instead of a divergent one.
"""

from __future__ import annotations

from swarm_os.migrations import CURRENT_VERSION, migrate_snapshot

__all__ = ["migrate_snapshot", "CURRENT_VERSION"]
