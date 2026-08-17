"""Canary registry for signal-gated rollback (2026 autonomy layer, move 4, Phase B).

After a repair ships, a canary tracks whether the repaired file is still healthy
within the policy's canary window (default 30 min). Evaluated on the watch-loop's
tick as an OFF-TICK background task (never blocking the heartbeat or event
tailing — a slow pytest re-verify must not make the daemon itself look stale).

RESTART-SURVIVAL: the registry is a lock-guarded JSON file re-read every tick
(the same fail-closed-but-visible pattern as the heartbeat), so a daemon that
restarts mid-window does NOT lose pending canaries.

DISTINCT TERMINAL STATES (never a silent no-op):
  pending      -> cleared       : verified healthy (tests pass / no downstream hit)
  pending      -> flagged       : regression detected. signal_1 (direct test
                                  failure) is AUTHORITATIVE -> automatic rollback.
                                  signal_2-only (graph-based downstream inference,
                                  which has a confirmed dynamic-import blind spot
                                  in the capabilities/bootstrap chain) -> HUMAN
                                  REVIEW, never automatic.
  pending      -> unverifiable  : the canary could not run (test target missing,
                                  daemon down past window, test run errored) ->
                                  flag for human attention, keep the snapshot,
                                  never assume clean.

SAME-FILE COLLISION: one pending canary per file at a time. A second repair on a
file with a canary already pending is REFUSED registration (matches Phase A's
refused_conflict shape) — so a flagged rollback always knows which snapshot to
restore (the one still in its window).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from filelock import FileLock

log = logging.getLogger("CanaryRegistry")

_REGISTRY_FILE = Path("data/events/canary_pending.json")

PENDING = "pending"
CLEARED = "cleared"
FLAGGED = "flagged"
UNVERIFIABLE = "unverifiable"

SIGNAL1 = "post_repair_test_regression"
SIGNAL2 = "downstream_consumer_breakage"


def _now() -> float:
    return time.time()


def _iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def load_registry() -> dict:
    """Load the canary registry. Missing/corrupt -> {} (fail-safe: no canaries)."""
    try:
        if not _REGISTRY_FILE.exists():
            return {}
        data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("Canary registry load failed (%s); treating as empty.", exc)
        return {}


def _save_registry(registry: dict) -> None:
    try:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(_REGISTRY_FILE) + ".lock", timeout=5.0)
        with lock:
            _REGISTRY_FILE.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception as exc:
        log.warning("Canary registry save failed (%s).", exc)


def register_canary(
    file_rel: str, snapshot_id: str, window_minutes: float = 30.0, policy=None
) -> tuple[bool, str]:
    """Register a pending canary for a repaired file. Refuses if the file already
    has a pending canary (one per file at a time — a flagged rollback must know
    which snapshot to restore). Returns (ok, repair_id_or_reason)."""
    file_key = file_rel.replace("\\", "/").lstrip("./")
    if not file_key:
        return False, "empty file path"
    # The lock must guard the ENTIRE load-check-save span, not just the write —
    # otherwise two concurrent registrations for the same file can both pass the
    # pending check and produce TWO pending canaries (breaking the one-snapshot
    # invariant a flagged rollback relies on).
    try:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(_REGISTRY_FILE) + ".lock", timeout=5.0)
        with lock:
            registry = load_registry()
            for rid, c in registry.items():
                if c.get("file") == file_key and c.get("state") == PENDING:
                    return False, f"canary already pending for {file_key} ({rid})"
            window = float(window_minutes)
            if policy is not None:
                try:
                    window = float(
                        policy.data["rollback"]["signal_gate"]["canary_window_minutes"]
                    )
                except Exception:
                    pass
            repair_id = f"canary-{int(_now() * 1000)}-{abs(hash(file_key)) % 10000}"
            registry[repair_id] = {
                "repair_id": repair_id,
                "file": file_key,
                "snapshot_id": snapshot_id,
                "due_at": _now() + window * 60,
                "created_at": _iso(),
                "state": PENDING,
            }
            # Write directly under the SAME lock we already hold — calling
            # _save_registry here would try to re-acquire the lock file with a
            # NEW FileLock instance (re-entrancy is per-instance) and deadlock.
            _REGISTRY_FILE.write_text(
                json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True, repair_id
    except Exception as exc:
        log.warning("Canary registration failed (%s).", exc)
        return False, f"registration failed: {exc}"


def pending_canaries() -> list[dict]:
    return [c for c in load_registry().values() if c.get("state") == PENDING]


def due_canaries() -> list[dict]:
    now = _now()
    return [c for c in pending_canaries() if now >= float(c.get("due_at", now))]


def resolve_canary(repair_id: str, state: str, detail: str = "") -> None:
    """Move a canary to a terminal state (cleared/flagged/unverifiable), audited
    by the caller. Cleared canaries also release their snapshot for GC."""
    registry = load_registry()
    c = registry.get(repair_id)
    if not c:
        return
    c["state"] = state
    c["resolved_at"] = _iso()
    c["detail"] = detail
    _save_registry(registry)


def clear_expired_old_flags(max_age_days: float = 14.0) -> int:
    """Stated open-edge handling for NEVER-REVIEWED flags: a flagged/unverifiable
    canary older than `max_age_days` is moved to a 'expired' state so the registry
    and its snapshot can be GC'd — but the fact it was flagged stays in the audit
    trail forever. Prevents unbounded growth in data/run_snapshots/ the same way
    checkpoint cleanup bounds data/checkpoints/. Returns count expired."""
    registry = load_registry()
    now = _now()
    expired = 0
    for rid, c in registry.items():
        if c.get("state") in (FLAGGED, UNVERIFIABLE):
            resolved = c.get("resolved_at")
            if not resolved:
                continue
            from datetime import datetime

            try:
                t = datetime.fromisoformat(resolved).timestamp()
            except Exception:
                continue
            if now - t > max_age_days * 86400:
                c["state"] = "expired"
                expired += 1
    if expired:
        _save_registry(registry)
    return expired
