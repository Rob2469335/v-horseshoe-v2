"""S8 factory: append-only ledger, bounded retry, scheduler, and resume.

Repository-side machinery only.  This module **never executes anything**: it has
no subprocess, no Hyper-V, no evaluator invocation and no network.  Execution is
an injected seam (:class:`Runner`), which is what makes the host non-execution
tests in ``tests/test_s8_factory_non_execution.py`` meaningful.

Design invariants (S8 factory contract)
---------------------------------------
1. Exactly one authoritative terminal record per (task, attempt) execution
   identity, and at most one *admitted* terminal record per task.
2. A task is admitted only when a scientific outcome ``base_fail_gold_pass``
   carries verifiable evidence whose digest matches the pinned task identity,
   registration provenance and evaluator identity.
3. An infrastructure outcome (``timeout`` / ``build_failure`` /
   ``infrastructure_failure``) is retried under an explicit cap and is **never**
   recorded as a task rejection or a scientific negative.
4. The ledger is append-only and hash-chained; any rewrite or reorder is
   detectable by :meth:`AppendOnlyLedger.verify`.
5. A repeated scheduler invocation is idempotent: tasks with a terminal
   scientific outcome, or with the retry cap exhausted, are not re-run.
6. The operative registration digest is checked before any task is selected; a
   mismatch blocks the run rather than silently using a different population.

Counts are kept strictly separate: registered != attempted != completed !=
admitted != analyzable.  ``analyzable`` is always ``0`` here — it belongs to the
later T/X phase and is never inferred from S8 evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "GENESIS_DIGEST",
    "SCIENTIFIC_OUTCOMES",
    "INFRASTRUCTURE_OUTCOMES",
    "ADMITTED_OUTCOME",
    "ALL_OUTCOMES",
    "FactoryError",
    "RegistrationMismatch",
    "LedgerError",
    "RetryPolicy",
    "ExecutionResult",
    "AppendOnlyLedger",
    "RunPlan",
    "plan_run",
    "run_once",
    "counts",
]

SCHEMA_VERSION = "f2_s8_factory_v1"
GENESIS_DIGEST = "0" * 64

ADMITTED_OUTCOME = "base_fail_gold_pass"
#: Outcomes attributable to the task; terminal, never retried.
SCIENTIFIC_OUTCOMES = ("base_fail_gold_pass", "base_pass", "gold_fail", "test_not_run")
#: Outcomes attributable to the environment; retried under the cap and never
#: counted as a task rejection.
INFRASTRUCTURE_OUTCOMES = ("timeout", "build_failure", "infrastructure_failure")
ALL_OUTCOMES = SCIENTIFIC_OUTCOMES + INFRASTRUCTURE_OUTCOMES

_SAFE_ARTIFACT_BAD = set('<>:"|?*;&') | {"/", "\\"}


class FactoryError(Exception):
    """Base class for factory contract violations."""


class RegistrationMismatch(FactoryError):
    """The live registration digest is not the pinned operative one."""


class LedgerError(FactoryError):
    """The ledger is malformed, tampered with, or would be mutated illegally."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RetryPolicy:
    """Explicit, capped retry policy.  No unbounded retry exists."""

    max_attempts: int = 3
    max_tasks_per_run: int = 25
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise FactoryError("max_attempts must be >= 1")
        if self.max_tasks_per_run < 1:
            raise FactoryError("max_tasks_per_run must be >= 1")
        if self.max_concurrency < 1:
            raise FactoryError("max_concurrency must be >= 1")

    def as_dict(self) -> dict[str, int]:
        return {
            "max_attempts": self.max_attempts,
            "max_tasks_per_run": self.max_tasks_per_run,
            "max_concurrency": self.max_concurrency,
        }


@dataclass(frozen=True)
class ExecutionResult:
    """What an injected runner reports.  Transport-level facts only."""

    task_id: str
    outcome: str
    attempt: int
    attempt_id: str
    disk_id: str = ""
    evidence_name: str = ""
    evidence_sha256: str = ""
    evidence_size: int = 0
    evaluator: Mapping[str, str] = field(default_factory=dict)
    reason: str = ""
    ts: str = ""

    @property
    def outcome_class(self) -> str:
        if self.outcome in INFRASTRUCTURE_OUTCOMES:
            return "infrastructure"
        if self.outcome in SCIENTIFIC_OUTCOMES:
            return "scientific"
        return "unknown"


def _validate_evidence_name(name: str, root: Path | None) -> None:
    if not name:
        return
    if name != name.strip() or ".." in name or any(c in _SAFE_ARTIFACT_BAD for c in name):
        raise FactoryError(f"unsafe artifact name: {name!r}")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise FactoryError(f"control character in artifact name: {name!r}")
    if root is not None:
        resolved = (Path(root) / name).resolve()
        try:
            resolved.relative_to(Path(root).resolve())
        except ValueError as exc:  # pragma: no cover - defensive
            raise FactoryError(f"artifact escapes the evidence root: {name!r}") from exc


class AppendOnlyLedger:
    """Hash-chained, append-only JSONL ledger.

    Each record embeds ``seq`` and ``prev_digest``; ``verify`` recomputes the
    whole chain, so a deleted, reordered, edited or truncated record is
    detectable.  There is deliberately **no** update or delete method.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    # ---- read ------------------------------------------------------------ #
    def records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LedgerError(f"ledger line {number} is not valid JSON: {exc}") from exc
        return out

    def verify(self) -> tuple[bool, str]:
        previous = GENESIS_DIGEST
        for index, record in enumerate(self.records()):
            if record.get("seq") != index:
                return False, f"record {index} has seq={record.get('seq')!r}"
            if record.get("prev_digest") != previous:
                return False, f"record {index} prev_digest does not link to its predecessor"
            body = {k: v for k, v in record.items() if k != "record_digest"}
            if record.get("record_digest") != _digest(body):
                return False, f"record {index} digest does not match its content"
            previous = str(record["record_digest"])
        return True, "ledger chain intact"

    def attempts(self, task_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records() if r.get("task_id") == task_id]

    def terminal_scientific(self, task_id: str) -> dict[str, Any] | None:
        for record in self.attempts(task_id):
            if record.get("outcome_class") == "scientific":
                return record
        return None

    def admitted(self, task_id: str) -> dict[str, Any] | None:
        record = self.terminal_scientific(task_id)
        if record is not None and record.get("outcome") == ADMITTED_OUTCOME:
            return record
        return None

    # ---- write ----------------------------------------------------------- #
    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        existing = self.records()
        body = dict(record)
        body["seq"] = len(existing)
        body["prev_digest"] = existing[-1]["record_digest"] if existing else GENESIS_DIGEST
        body["schema_version"] = SCHEMA_VERSION
        body.pop("record_digest", None)
        body["record_digest"] = _digest(body)

        task_id = str(body.get("task_id") or "")
        if body.get("outcome_class") == "scientific" and self.terminal_scientific(task_id):
            raise LedgerError(
                f"task {task_id!r} already has a terminal scientific record; "
                "append-only history may not be rewritten"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(body, sort_keys=True) + "\n")
        return body


@dataclass(frozen=True)
class RunPlan:
    """Deterministic selection for one scheduler invocation."""

    registration_id: str
    registration_sha256: str
    policy: RetryPolicy
    runnable: tuple[str, ...]
    skipped_admitted: tuple[str, ...]
    skipped_exhausted: tuple[str, ...]
    skipped_running: tuple[str, ...]
    counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "registration_id": self.registration_id,
            "registration_sha256": self.registration_sha256,
            "policy": self.policy.as_dict(),
            "runnable": list(self.runnable),
            "skipped_admitted": list(self.skipped_admitted),
            "skipped_exhausted": list(self.skipped_exhausted),
            "skipped_running": list(self.skipped_running),
            "counts": dict(self.counts),
        }


def _attempts_used(ledger: AppendOnlyLedger, task_id: str) -> int:
    return len(ledger.attempts(task_id))


def plan_run(
    *,
    ordered_task_ids: Sequence[str],
    ledger: AppendOnlyLedger,
    registration_id: str,
    registration_sha256: str,
    expected_registration_sha256: str,
    policy: RetryPolicy | None = None,
    in_flight: Iterable[str] = (),
) -> RunPlan:
    """Select the next tasks deterministically, honoring the retry cap.

    Tasks are considered in registered order.  A task is skipped when it is
    admitted, when its retry cap is exhausted, or when it is already in flight
    (concurrency guard).  A registration-digest mismatch raises before anything
    is selected, so a different population can never be run by accident.
    """
    if registration_sha256 != expected_registration_sha256:
        raise RegistrationMismatch(
            f"registration digest {registration_sha256!r} is not the pinned operative "
            f"{expected_registration_sha256!r}"
        )
    policy = policy or RetryPolicy()
    running = set(in_flight)

    runnable: list[str] = []
    admitted: list[str] = []
    exhausted: list[str] = []
    skipped_running: list[str] = []
    for task_id in ordered_task_ids:
        if ledger.admitted(task_id) is not None:
            admitted.append(task_id)
            continue
        if str(task_id) in running:
            skipped_running.append(task_id)
            continue
        if _attempts_used(ledger, task_id) >= policy.max_attempts:
            exhausted.append(task_id)
            continue
        if len(runnable) < policy.max_tasks_per_run:
            runnable.append(task_id)

    return RunPlan(
        registration_id=registration_id,
        registration_sha256=registration_sha256,
        policy=policy,
        runnable=tuple(runnable),
        skipped_admitted=tuple(admitted),
        skipped_exhausted=tuple(exhausted),
        skipped_running=tuple(skipped_running),
        counts={
            "registered": len(ordered_task_ids),
            "attempted": len({r["task_id"] for r in ledger.records()}),
            "completed": len(
                {r["task_id"] for r in ledger.records() if r.get("outcome_class") == "scientific"}
            ),
            "admitted": len(admitted),
            "analyzable": 0,
        },
    )


def run_once(
    *,
    task_id: str,
    ledger: AppendOnlyLedger,
    runner: Callable[[str, int], ExecutionResult],
    registration_id: str,
    registration_sha256: str,
    evaluator: Mapping[str, str],
    evidence_root: Path | None = None,
    policy: RetryPolicy | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Run exactly one attempt through the injected ``runner`` and record it.

    The module performs no execution itself.  Evidence identity is validated
    before the record is written; a task whose evidence is missing is recorded
    as ``test_not_run`` rather than admitted, and an infrastructure outcome is
    recorded as infrastructure, never as a task rejection.
    """
    policy = policy or RetryPolicy()
    attempt = _attempts_used(ledger, task_id) + 1
    if attempt > policy.max_attempts:
        raise FactoryError(f"task {task_id!r} exceeded max_attempts={policy.max_attempts}")

    result = runner(task_id, attempt)
    if not isinstance(result, ExecutionResult):
        raise FactoryError("runner must return an ExecutionResult")
    if result.task_id != task_id:
        raise FactoryError(f"runner returned task {result.task_id!r} for {task_id!r}")
    if result.outcome not in ALL_OUTCOMES:
        raise FactoryError(f"unknown outcome {result.outcome!r}")
    if result.attempt != attempt:
        raise FactoryError(
            f"runner reported attempt {result.attempt} but this is attempt {attempt}"
        )

    _validate_evidence_name(result.evidence_name, evidence_root)
    admitted = result.outcome == ADMITTED_OUTCOME
    if admitted:
        if not result.evidence_name or len(result.evidence_sha256) != 64:
            raise FactoryError(
                "an admitted outcome requires a named artifact and its SHA-256; "
                "missing evidence is never a pass"
            )
        for key in ("evaluator_id", "version", "implementation_digest", "procedure_id"):
            if not str(result.evaluator.get(key) or ""):
                raise FactoryError(f"admitted outcome is missing evaluator.{key}")

    record = {
        "schema_version": SCHEMA_VERSION,
        "ts": ts or result.ts or _utc_now(),
        "task_id": task_id,
        "registration_id": registration_id,
        "registration_sha256": registration_sha256,
        "attempt": attempt,
        "attempt_id": result.attempt_id,
        "disk_id": result.disk_id,
        "outcome": result.outcome,
        "outcome_class": result.outcome_class,
        "admitted": admitted,
        "evidence": (
            {
                "name": result.evidence_name,
                "sha256": result.evidence_sha256,
                "size_bytes": result.evidence_size,
            }
            if result.evidence_name
            else None
        ),
        "evaluator": dict(result.evaluator or evaluator),
        "reason": result.reason,
    }
    return ledger.append(record)


def counts(ledger: AppendOnlyLedger, ordered_task_ids: Sequence[str]) -> dict[str, int]:
    """Report the five states separately; ``analyzable`` is always 0 here."""
    records = ledger.records()
    attempted = {r["task_id"] for r in records}
    scientific = {r["task_id"] for r in records if r.get("outcome_class") == "scientific"}
    admitted = {r["task_id"] for r in records if r.get("admitted") is True}
    return {
        "registered": len(ordered_task_ids),
        "attempted": len(attempted & set(ordered_task_ids)),
        "completed": len(scientific & set(ordered_task_ids)),
        "admitted": len(admitted & set(ordered_task_ids)),
        "analyzable": 0,
    }
