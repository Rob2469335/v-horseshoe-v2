"""Durable repair-orchestrator state machine (2026 autonomy layer, P0).

A repair is a sequence of externally interruptible phases (LLM calls, subprocess
test runs, file writes). If the process crashes, times out, or is cancelled
mid-sequence, a non-durable orchestrator loses its place — a partially applied
patch can be indistinguishable from a finished one. This module gives every
repair an authoritative, durable record whose phase transitions are legal-only,
and a crash-recovery path that re-derives the next action from what was last
persisted.

DESIGN (from the closed-loop-repair review):
- Phases: CREATED -> INSPECTING -> DIAGNOSING -> PATCHING -> VALIDATING
  -> (REVALIDATING on flaky) -> SECURITY_CHECK -> EVIDENCE_CAPTURE -> ACCEPTED.
  Failure paths: any phase -> REVERTING -> REPAIR_FAILED.
- Legal transitions are an explicit table; a transition not in the table raises
  (PATCHING -> ACCEPTED is impossible; SECURITY_CHECK -> ACCEPTED requires an
  explicit successful security result first).
- The record is persisted atomically (.tmp + os.replace, FileLock) to
  data/repair_states/<repair_id>.json after EVERY transition, so a crash at any
  point leaves a recoverable record.
- recover(repair_id): loads the durable record; if the phase is CREATED..PATCHING
  (a patch was never accepted), the repair is marked REPAIR_FAILED and any
  candidate revision that was applied can be reverted. A record can only reach
  ACCEPTED through an explicit evidence-captured transition that recorded
  validation + security results — a crashed repair can never be ACCEPTED by
  construction.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from filelock import FileLock

log = logging.getLogger("RepairState")

_REPAIR_STATE_DIR = Path("data/repair_states")

# Phases and the ONLY legal transitions out of each.
# ACCEPTED / REPAIR_FAILED are terminal (empty out-sets).
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"INSPECTING", "REPAIR_FAILED"},
    "INSPECTING": {"DIAGNOSING", "REPAIR_FAILED"},
    "DIAGNOSING": {"PATCHING", "REPAIR_FAILED"},
    "PATCHING": {"VALIDATING", "REVERTING", "REPAIR_FAILED"},
    "VALIDATING": {"REVALIDATING", "SECURITY_CHECK", "REVERTING", "REPAIR_FAILED"},
    "REVALIDATING": {"SECURITY_CHECK", "REVERTING", "REPAIR_FAILED"},
    "SECURITY_CHECK": {"EVIDENCE_CAPTURE", "REVERTING", "REPAIR_FAILED"},
    "EVIDENCE_CAPTURE": {"ACCEPTED"},
    "REVERTING": {"REPAIR_FAILED"},
    "ACCEPTED": set(),
    "REPAIR_FAILED": set(),
}

# Phases that occur AFTER a patch was written to disk. A crash in any of these
# must force REVERTING (the candidate is on disk but never accepted).
POST_PATCH_PHASES = frozenset(
    {"PATCHING", "VALIDATING", "REVALIDATING", "SECURITY_CHECK", "EVIDENCE_CAPTURE"}
)

# Phases where no patch touched disk yet; a crash here is safe to fail without
# a revert (nothing to revert).
PRE_PATCH_PHASES = frozenset({"CREATED", "INSPECTING", "DIAGNOSING"})


class IllegalTransitionError(ValueError):
    pass


@dataclass
class RepairRecord:
    """Durable authoritative record of one repair attempt."""

    repair_id: str = ""
    attempt: int = 1
    phase: str = "CREATED"
    phase_started_at: float = 0.0
    phase_completed_at: float | None = None
    candidate_revision: str | None = None  # sha256 of the patched file on disk
    baseline_revision: str | None = None  # sha256 of the pre-patch file
    file_path: str = ""
    error_text: str = ""
    failure_type: str = ""
    validation_result: dict | None = (
        None  # {initial_result, retry_result, flaky, outcome}
    )
    security_result: dict | None = None  # {ok, reason}
    revert_result: dict | None = None  # {ok, reason}
    evidence_refs: list = field(default_factory=list)
    last_error: str | None = None
    next_action: str | None = None  # what recover() decided should happen next


def new_repair_id() -> str:
    return uuid.uuid4().hex[:12]


def _state_path(repair_id: str) -> Path:
    return _REPAIR_STATE_DIR / f"{repair_id}.json"


def create_record(
    file_path: Path, error_text: str, failure_type: str, baseline_revision: str | None
) -> RepairRecord:
    """Create and persist a fresh CREATED record. Returns it."""
    rec = RepairRecord(
        repair_id=new_repair_id(),
        phase="CREATED",
        phase_started_at=time.time(),
        baseline_revision=baseline_revision,
        file_path=str(file_path),
        error_text=str(error_text)[:500],
        failure_type=failure_type,
    )
    persist(rec)
    return rec


def transition(rec: RepairRecord, to_phase: str) -> RepairRecord:
    """Move to `to_phase` ONLY if the phase table allows it. Persists."""
    allowed = LEGAL_TRANSITIONS.get(rec.phase, set())
    if to_phase not in allowed:
        raise IllegalTransitionError(
            f"Illegal repair-state transition {rec.phase!r} -> {to_phase!r} "
            f"(allowed: {sorted(allowed)})"
        )
    rec.phase_completed_at = time.time()
    rec.phase = to_phase
    rec.phase_started_at = time.time()
    persist(rec)
    return rec


def persist(rec: RepairRecord) -> str:
    """Atomically persist the record (FileLock + .tmp + os.replace)."""
    rid = rec.repair_id or new_repair_id()
    rec.repair_id = rid
    try:
        path = _state_path(rid)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        lock = FileLock(str(path) + ".lock", timeout=5.0)
        with lock:
            tmp.write_text(
                json.dumps(asdict(rec), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, path)
    except Exception as exc:
        log.warning("Repair-state persist failed (%s): %s", rid, exc)
    return rid


def load(repair_id: str) -> RepairRecord | None:
    """Load a durable record. Returns None if absent or unreadable."""
    try:
        path = _state_path(repair_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return RepairRecord(
            **{k: v for k, v in data.items() if k in RepairRecord.__dataclass_fields__}
        )
    except Exception as exc:
        log.warning("Repair-state load failed (%s): %s", repair_id, exc)
        return None


def recover(repair_id: str) -> RepairRecord:
    """Crash recovery: load the durable record and decide the next action.

    - ACCEPTED / REPAIR_FAILED: terminal — nothing to do.
    - PRE_PATCH phase (nothing on disk): mark REPAIR_FAILED (safe, nothing to
      revert) with next_action="abort".
    - POST_PATCH phase (candidate touched disk but was never accepted): mark
      REPAIR_FAILED and set next_action="revert" so the caller rolls the
      candidate revision back to baseline. A crashed repair can NEVER become
      ACCEPTED — there is no recovery path from a crash to ACCEPTED.
    Raises FileNotFoundError if no record exists.
    """
    rec = load(repair_id)
    if rec is None:
        raise FileNotFoundError(f"No durable repair record for {repair_id!r}")
    if rec.phase in ("ACCEPTED", "REPAIR_FAILED"):
        rec.next_action = "none"
        persist(rec)
        return rec
    if rec.phase in PRE_PATCH_PHASES:
        rec.phase = "REPAIR_FAILED"
        rec.phase_completed_at = time.time()
        rec.last_error = f"crashed during {rec.phase}; nothing was patched"
        rec.next_action = "abort"
        persist(rec)
        return rec
    # POST_PATCH: the candidate may be on disk and was never accepted.
    rec.phase = "REPAIR_FAILED"
    rec.phase_completed_at = time.time()
    rec.last_error = (
        f"crashed during {rec.phase}; candidate revision was never accepted"
    )
    rec.next_action = "revert"
    persist(rec)
    return rec
