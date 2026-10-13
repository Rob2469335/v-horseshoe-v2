"""S8 production batch driver: resumable, idempotent, observable.

This is the production counterpart to the one-shot composition in
:mod:`qwen_train.f2_s8_compose`.  It drives many tasks through an injected,
fully-authorized per-task callable, using the append-only ledger and the
deterministic :func:`qwen_train.f2_s8_factory.plan_run` selector.

Guarantees
----------
* **Resumable.** The ledger is the durable state.  Re-invoking the driver selects
  only tasks that are not admitted and whose retry cap is not exhausted, so an
  interrupted batch continues where it stopped.
* **No duplicate admissions.** ``plan_run`` skips admitted tasks and
  ``AppendOnlyLedger`` refuses a second terminal scientific record for a task.
* **Bounded retry.** A task is never selected once ``attempts >= max_attempts``.
* **Infrastructure is not a task verdict.** A task whose callable raises is
  recorded as ``infrastructure_failure`` and retried under the cap; it is never
  recorded as a scientific negative.
* **Observable progress.** Each task appends one progress line (JSONL) naming the
  task, attempt, outcome and the running admission count derived from the ledger.
* **Fail-closed population binding.** A registration digest mismatch raises
  before any task is selected.

The driver performs no execution itself: ``process_task`` is the injected,
already-governed per-task entry point (e.g. a closure over
:func:`qwen_train.f2_s8_compose.produce_governed_task`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train.f2_s8_factory import (
    AppendOnlyLedger,
    INFRASTRUCTURE_OUTCOMES,
    RetryPolicy,
    plan_run,
)

__all__ = [
    "BATCH_SCHEMA",
    "BatchError",
    "BatchReport",
    "drive_batch",
    "reconcile_counts",
]

BATCH_SCHEMA = "f2_s8_batch_v1"


class BatchError(Exception):
    """The batch contract was violated."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class BatchReport:
    registration_id: str
    registration_sha256: str
    policy: Mapping[str, int]
    selected: tuple[str, ...]
    results: tuple[Mapping[str, Any], ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    progress_path: str = ""
    skipped_admitted: tuple[str, ...] = ()
    skipped_exhausted: tuple[str, ...] = ()
    stopped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BATCH_SCHEMA,
            "registration_id": self.registration_id,
            "registration_sha256": self.registration_sha256,
            "policy": dict(self.policy),
            "selected": list(self.selected),
            "results": [dict(r) for r in self.results],
            "counts": dict(self.counts),
            "progress_path": self.progress_path,
            "skipped_admitted": list(self.skipped_admitted),
            "skipped_exhausted": list(self.skipped_exhausted),
            "stopped_reason": self.stopped_reason,
        }


def reconcile_counts(
    ledger: AppendOnlyLedger, ordered_task_ids: Sequence[str]
) -> dict[str, int]:
    """Ledger-derived counts.  Every value is computed from authoritative records.

    ``planned`` / ``evidence_valid`` / ``analyzable`` are deliberately **not**
    invented here: planning is a scheduler output, evidence validity requires
    re-verifying retained artifacts, and ``analyzable`` belongs to the later T/X
    phase.  Those are reported by their owning layers, never inferred from a
    ledger row.
    """
    universe = set(ordered_task_ids)
    records = [r for r in ledger.records() if str(r.get("task_id")) in universe]
    attempted = {str(r["task_id"]) for r in records}
    scientific = {str(r["task_id"]) for r in records if r.get("outcome_class") == "scientific"}
    admitted = {str(r["task_id"]) for r in records if r.get("admitted") is True}
    infra = {
        str(r["task_id"])
        for r in records
        if r.get("outcome") in INFRASTRUCTURE_OUTCOMES
    }
    return {
        "registered": len(ordered_task_ids),
        "attempted": len(attempted),
        "completed": len(scientific),
        "admitted": len(admitted),
        "infrastructure_only": len(infra - scientific),
    }


def _append_progress(path: Path | None, entry: Mapping[str, Any]) -> None:
    if path is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(entry), sort_keys=True) + "\n")


def _infra_record(
    *,
    ledger: AppendOnlyLedger,
    task_id: str,
    registration_id: str,
    registration_sha256: str,
    attempt: int,
    exc: BaseException,
) -> dict[str, Any]:
    return ledger.append({
        "task_id": task_id,
        "registration_id": registration_id,
        "registration_sha256": registration_sha256,
        "outcome": "infrastructure_failure",
        "outcome_class": "infrastructure",
        "attempt": attempt,
        "reason": f"driver caught {type(exc).__name__}",
        "admitted": False,
    })


def drive_batch(
    *,
    ordered_task_ids: Sequence[str],
    ledger: AppendOnlyLedger,
    registration_id: str,
    registration_sha256: str,
    expected_registration_sha256: str,
    process_task: Callable[[str, int], Mapping[str, Any]],
    policy: RetryPolicy | None = None,
    progress_path: Path | str | None = None,
    in_flight: Sequence[str] = (),
    stop_after: int | None = None,
    clock: Callable[[], str] | None = None,
) -> BatchReport:
    """Drive the next batch of tasks, resumably and idempotently.

    ``process_task(task_id, attempt)`` performs one fully-governed task and
    returns the ledger record it appended (or any mapping summarising it).  The
    driver never weakens or re-interprets that record.
    """
    policy = policy or RetryPolicy()
    now = clock or _utc_now
    plan = plan_run(
        ordered_task_ids=ordered_task_ids,
        ledger=ledger,
        registration_id=registration_id,
        registration_sha256=registration_sha256,
        expected_registration_sha256=expected_registration_sha256,
        policy=policy,
        in_flight=in_flight,
    )

    results: list[dict[str, Any]] = []
    stopped_reason = ""
    for index, task_id in enumerate(plan.runnable):
        if stop_after is not None and index >= stop_after:
            stopped_reason = f"stop_after={stop_after} reached"
            break
        attempt = len(ledger.attempts(task_id)) + 1
        error = ""
        try:
            record = dict(process_task(task_id, attempt))
        except Exception as exc:  # noqa: BLE001 - infrastructure boundary
            record = _infra_record(
                ledger=ledger, task_id=task_id, registration_id=registration_id,
                registration_sha256=registration_sha256, attempt=attempt, exc=exc,
            )
            error = f"{type(exc).__name__}: {exc}"
        admitted_so_far = len(
            {r["task_id"] for r in ledger.records() if r.get("admitted") is True}
        )
        summary = {
            "task_id": task_id,
            "attempt": attempt,
            "outcome": record.get("outcome", ""),
            "outcome_class": record.get("outcome_class", ""),
            "admitted": bool(record.get("admitted", False)),
            "error": error,
            "admitted_total": admitted_so_far,
            "ts": now(),
        }
        results.append(summary)
        _append_progress(Path(progress_path) if progress_path else None, summary)

    return BatchReport(
        registration_id=registration_id,
        registration_sha256=registration_sha256,
        policy=policy.as_dict(),
        selected=tuple(plan.runnable),
        results=tuple(results),
        counts=reconcile_counts(ledger, ordered_task_ids),
        progress_path=str(progress_path or ""),
        skipped_admitted=plan.skipped_admitted,
        skipped_exhausted=plan.skipped_exhausted,
        stopped_reason=stopped_reason,
    )
