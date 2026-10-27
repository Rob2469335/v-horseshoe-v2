"""DRY-RUN build-probe driver. Never runs without an authorized probe grant.

The only production-visible entry point refuses; tests inject a fake authorize and a
fake builder. No network, build, or production store is touched here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import AbstractSet, Any, Callable, Mapping, Sequence


class ProbeHalt(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


class BuilderUnavailable(ProbeHalt):
    """No guest builder is configured; this is a hard prerequisite failure."""


class BuilderContractError(ProbeHalt):
    """The builder returned a result that violates the explicit contract."""


@dataclass(frozen=True)
class BuildCell:
    """One explicit per-task build result.

    ``classification`` must be one of the approved probe classifications.  A
    missing live builder is never represented as ``UNKNOWN``; it raises
    :class:`BuilderUnavailable` instead.
    """

    task_id: str
    classification: str
    workspace: str | None = None
    failure: str = ""
    reason_code: str = ""
    duration_seconds: float | None = None
    log_tail: str = ""

    def as_row(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "classification": self.classification,
            "workspace": self.workspace,
            "failure": self.failure,
            "reason_code": self.reason_code,
            "duration_seconds": self.duration_seconds,
            "log_tail": self.log_tail,
        }


class DryRunBuilder:
    """Explicit simulation builder used only by the dry-run CLI.

    It is named and typed so a caller cannot mistake its ``UNKNOWN`` rows for a
    real build result.  The live path has no builder and remains blocked.
    """

    def __call__(self, batch: Sequence[str]) -> Mapping[str, BuildCell]:
        return {
            task_id: BuildCell(task_id, "UNKNOWN")
            for task_id in batch
        }


def _normalise_cell(task_id: str, row: object) -> BuildCell:
    if isinstance(row, BuildCell):
        if row.task_id != task_id:
            raise BuilderContractError(
                f"builder returned cell for {row.task_id!r} under key {task_id!r}"
            )
        return row
    if isinstance(row, Mapping):
        return BuildCell(
            task_id=task_id,
            classification=str(row.get("classification") or ""),
            workspace=(str(row["workspace"]) if row.get("workspace") is not None else None),
            failure=str(row.get("failure") or ""),
            reason_code=str(row.get("reason_code") or ""),
            duration_seconds=(
                float(row["duration_seconds"])
                if isinstance(row.get("duration_seconds"), (int, float))
                else None
            ),
            log_tail=str(row.get("log_tail") or ""),
        )
    raise BuilderContractError(
        f"builder returned {type(row).__name__} for task {task_id!r}; "
        "expected BuildCell or mapping"
    )


#: Exactly the classification values the probe contract authorises
#: (Trust §12.4: OBSERVED_COMPATIBLE / OBSERVED_INCOMPATIBLE / UNKNOWN) plus the
#: two explicit build-failure spellings the cleanup path already recognised.
APPROVED_PROBE_CLASSIFICATIONS = frozenset({
    "OBSERVED_COMPATIBLE",
    "OBSERVED_INCOMPATIBLE",
    "UNKNOWN",
    "BUILD_FAILURE",
    "OBSERVED_BUILD_FAILURE",
})

#: Classifications whose approved action is the failed-build cleanup path.
BUILD_FAILURE_CLASSIFICATIONS = frozenset({
    "BUILD_FAILURE",
    "OBSERVED_BUILD_FAILURE",
})


class CleanupClassificationUnrecognized(RuntimeError):
    """A cleanup classification the contract does not authorise.

    Raised *before* any cleanup function is called, so an unexpected value can
    never trigger destructive cleanup.
    """

    def __init__(self, task_id: str, classification: object) -> None:
        self.task_id = task_id
        self.classification = classification
        super().__init__(
            f"CLEANUP_CLASSIFICATION_UNRECOGNIZED: task {task_id!r} "
            f"classification {classification!r}"
        )


@dataclass(frozen=True)
class ProbeLimits:
    max_tasks: int
    disk_floor_bytes: int = 25_000_000_000
    per_task_disk_bytes: int = 3_500_000_000
    per_task_time_seconds: int = 3600
    batch_limit: int = 10
    max_infra_rate: float = 0.10
    #: Stop after a batch once this many compatible workspaces exist. 0 = no
    #: target. This is an operational stopping rule, not a scientific estimate.
    target_compatible: int = 0
    #: Maximum number of builder invocations (batches). 0 = no cap.
    max_executions: int = 0

    def __post_init__(self) -> None:
        if self.batch_limit > 50:
            raise ValueError(f"batch_limit exceeds hard ceiling of 50: {self.batch_limit}")
        if self.target_compatible < 0:
            raise ValueError("target_compatible must be non-negative")
        if self.max_executions < 0:
            raise ValueError("max_executions must be non-negative")

    def disk_threshold(self, batch_size: int) -> int:
        # Fail-closed resource model: reserves disk floor plus defensible per-task reservation
        # (3.5 GB per task, exceeding the 3.07 GB maximum measured workspace).
        return max(
            self.disk_floor_bytes,
            self.disk_floor_bytes + self.per_task_disk_bytes * max(1, batch_size),
        )


@dataclass
class ProbeResult:
    stop_reason: str
    detail: str = ""
    batches: int = 0
    attempted: int = 0
    built: int = 0
    compatible: int = 0
    incompatible: int = 0
    unknown: int = 0
    infrastructure_invalid: int = 0
    credited: int = 0
    log: list[str] = field(default_factory=list)


def compute_ordered_probe_candidates(
    *,
    registration_order: Sequence[str],
    compat_classes: Mapping[str, str],
    terminal_task_ids: AbstractSet[str] | Sequence[str] = (),
    built_task_ids: AbstractSet[str] | Sequence[str] = (),
    ordering_policy: str = "REGISTRATION_ORDER_UNBUILT",
    max_tasks: int = 50,
) -> tuple[str, list[str], str]:
    """Deterministically compute (probe_pool_digest, selected_task_ids, probe_selection_digest).

    - probe_pool_digest: SHA-256 of the canonically sorted list of all UNKNOWN task IDs.
    - probe_selection_digest: SHA-256 of the canonically encoded selected_task_ids list.
    """
    if ordering_policy not in {
        "REGISTRATION_ORDER_UNBUILT",
        "REGISTRATION_ORDER_RAW",
        "ASCII_SORTED_UNBUILT",
    }:
        raise ValueError(f"unknown ordering_policy: {ordering_policy!r}")

    unknown_sorted = sorted(t for t, c in compat_classes.items() if c == "UNKNOWN")
    probe_pool_digest = hashlib.sha256(
        json.dumps(unknown_sorted, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    term_set = set(terminal_task_ids)
    built_set = set(built_task_ids)

    if ordering_policy == "REGISTRATION_ORDER_UNBUILT":
        selected = [
            t
            for t in registration_order
            if compat_classes.get(t) == "UNKNOWN"
            and t not in term_set
            and t not in built_set
        ][:max_tasks]
    elif ordering_policy == "REGISTRATION_ORDER_RAW":
        selected = [
            t for t in registration_order if compat_classes.get(t) == "UNKNOWN"
        ][:max_tasks]
    elif ordering_policy == "ASCII_SORTED_UNBUILT":
        selected = [
            t
            for t in unknown_sorted
            if t not in term_set and t not in built_set
        ][:max_tasks]

    probe_selection_digest = hashlib.sha256(
        json.dumps(selected, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return probe_pool_digest, selected, probe_selection_digest


def select_probe_batch(unknown_task_ids: Sequence[str], *, limit: int = 50) -> list[str]:
    if limit < 1 or limit > 50:
        raise ValueError("probe batch limit must be 1..50")
    return list(unknown_task_ids[:limit])


def run_probe(
    *,
    unknown_task_ids: Sequence[str],
    limits: ProbeLimits,
    authorize: Callable[[], None],
    builder: Callable[[Sequence[str]], Mapping[str, object]] | None,
    free_bytes: Callable[[], int],
    allowed_task_ids: AbstractSet[str] | Sequence[str] | None = None,
    cleanup_after_task: Callable[[str, Mapping[str, Any]], None] | None = None,
    verify_ledgers: Callable[[], None],
    log: Callable[[str], None] = print,
) -> ProbeResult:
    """Fake-builder dry run. Never writes credits or production state.

    ``verify_ledgers`` is REQUIRED: a caller cannot run the probe without a
    ledger-integrity check, so an accidental omission cannot disable it.
    """
    result = ProbeResult(stop_reason="")
    pending = list(unknown_task_ids)
    allowed_set = set(allowed_task_ids) if allowed_task_ids is not None else None
    batches = 0

    def stop(reason: str, detail: str = "") -> ProbeResult:
        result.stop_reason = reason
        result.detail = detail
        line = f"STOP {reason}" + (f": {detail}" if detail else "")
        result.log.append(line)
        log(line)
        return result

    try:
        authorize()
    except ProbeHalt as exc:
        return stop(exc.reason, exc.detail)
    except Exception as exc:  # noqa: BLE001 - fail closed
        return stop("PROBE_AUTHORIZATION_REFUSED", str(exc))

    if builder is None:
        return stop(
            "PROBE_BUILDER_UNAVAILABLE",
            "no guest builder is configured; live builds remain blocked",
        )

    while pending:
        if result.attempted >= limits.max_tasks:
            return stop("PROBE_CAPS_EXHAUSTED", f"tasks={result.attempted}")
        remaining_cap = limits.max_tasks - result.attempted
        if remaining_cap < 1:
            return stop("PROBE_CAPS_EXHAUSTED", f"tasks={result.attempted}")

        # Re-verify authorization before each batch (catches mid-run revocation / expiry)
        try:
            authorize()
        except ProbeHalt as exc:
            return stop(exc.reason, exc.detail)
        except Exception as exc:  # noqa: BLE001 - fail closed
            return stop("PROBE_REVOKED", str(exc))

        batch = select_probe_batch(
            pending, limit=min(50, limits.batch_limit, remaining_cap)
        )
        if not batch:
            return stop("PROBE_NO_ELIGIBLE_TASK")

        # Check allowed task IDs if bound by grant
        if allowed_set is not None:
            for task_id in batch:
                if task_id not in allowed_set:
                    return stop(
                        "PROBE_TASK_OUTSIDE_SELECTION",
                        f"task {task_id!r} not in authorized probe selection",
                    )

        # Batch disk threshold check
        threshold = limits.disk_threshold(len(batch))
        available = int(free_bytes())
        if available < threshold:
            return stop(
                "PROBE_DISK_LOW",
                f"free={available} threshold={threshold} batch={len(batch)}",
            )

        # Per-task floor check before batch execution
        task_floor = limits.disk_floor_bytes + limits.per_task_disk_bytes
        if available < task_floor:
            return stop(
                "PROBE_DISK_LOW",
                f"free={available} below per-task floor={task_floor}",
            )

        batches += 1
        line = f"probe batch {batches}: {len(batch)} tasks"
        result.log.append(line)
        log(line)
        try:
            raw_outcome = builder(batch)
            if not isinstance(raw_outcome, Mapping):
                raise BuilderContractError(
                    f"builder returned {type(raw_outcome).__name__}, expected a mapping"
                )
            outcome = dict(raw_outcome)
        except BuilderUnavailable as exc:
            result.batches = batches
            return stop("PROBE_BUILDER_UNAVAILABLE", str(exc))
        except BuilderContractError as exc:
            result.batches = batches
            return stop("PROBE_BUILDER_CONTRACT", str(exc))
        except ProbeHalt as exc:
            result.batches = batches
            return stop(exc.reason, exc.detail)
        except Exception as exc:  # noqa: BLE001 - builder failure is infra
            result.batches = batches
            result.attempted += len(batch)
            result.infrastructure_invalid += len(batch)
            pending = pending[len(batch):]
            return stop("PROBE_INFRA_FAILURE_RATE", f"builder failed: {exc}")

        outside = sorted(set(outcome) - set(batch))
        if outside:
            result.batches = batches
            return stop(
                "PROBE_BUILDER_CONTRACT",
                f"builder returned task(s) outside the batch: {outside[:3]}",
            )
        missing = [task_id for task_id in batch if task_id not in outcome]
        if missing:
            result.batches = batches
            return stop(
                "PROBE_BUILDER_CONTRACT",
                f"builder omitted task(s): {missing[:3]}",
            )

        infra = 0
        for task_id in batch:
            try:
                cell = _normalise_cell(task_id, outcome[task_id])
            except BuilderContractError as exc:
                result.batches = batches
                return stop("PROBE_BUILDER_CONTRACT", str(exc))
            raw_classification = cell.classification
            if not isinstance(raw_classification, str) or not raw_classification.strip():
                result.batches = batches
                return stop(
                    "CLEANUP_CLASSIFICATION_UNRECOGNIZED",
                    f"task {task_id!r} classification {raw_classification!r}",
                )
            classification = raw_classification.strip()
            if classification not in APPROVED_PROBE_CLASSIFICATIONS:
                result.batches = batches
                return stop(
                    "CLEANUP_CLASSIFICATION_UNRECOGNIZED",
                    f"task {task_id!r} classification {classification!r}",
                )
            if classification == "OBSERVED_COMPATIBLE" and cell.failure:
                result.batches = batches
                return stop(
                    "CLEANUP_CLASSIFICATION_UNRECOGNIZED",
                    f"task {task_id!r} classification {classification}+failure",
                )
            result.attempted += 1
            if classification == "OBSERVED_COMPATIBLE":
                result.compatible += 1
                workspace = cell.workspace
                if workspace and pathlib.Path(str(workspace)).is_dir():
                    result.built += 1
            elif classification == "OBSERVED_INCOMPATIBLE":
                result.incompatible += 1
            else:
                result.unknown += 1
            if cell.failure:
                infra += 1
            if cleanup_after_task is not None:
                try:
                    cleanup_after_task(task_id, cell.as_row())
                except CleanupClassificationUnrecognized as exc:
                    # An unrecognised classification is a security refusal, not
                    # a warning: no cleanup ran and the probe must not report
                    # success.
                    result.batches = batches
                    return stop("CLEANUP_CLASSIFICATION_UNRECOGNIZED", str(exc))
                except Exception as exc:
                    # A refused or failed cleanup is a terminal condition, not a
                    # warning: continuing would return PROBE_COMPLETE (exit 0)
                    # while leaving an unverified/tainted workspace behind.
                    result.batches = batches
                    return stop(
                        "CLEANUP_FAILED",
                        f"cleanup_after_task({task_id}): {exc}",
                    )

        result.infrastructure_invalid += infra
        pending = pending[len(batch):]
        result.batches = batches

        # Re-verify ledger immutability after batch
        try:
            verify_ledgers()
        except Exception as exc:
            return stop("PROBE_LEDGER_MUTATION_FORBIDDEN", str(exc))

        if batch and infra / len(batch) > limits.max_infra_rate:
            return stop(
                "PROBE_INFRA_FAILURE_RATE",
                f"infrastructure_invalid={infra}/{len(batch)}",
            )
        if limits.target_compatible and result.built >= limits.target_compatible:
            return stop(
                "PROBE_TARGET_REACHED",
                f"built={result.built} target={limits.target_compatible}",
            )
        if limits.max_executions and result.batches >= limits.max_executions:
            return stop(
                "PROBE_EXECUTION_CAP",
                f"executions={result.batches}",
            )
    return stop("PROBE_COMPLETE", f"tasks={result.attempted}")


def cleanup_probe_task(
    task_id: str,
    row: Mapping[str, Any],
    *,
    workspace_root: pathlib.Path | str,
    output_dir: pathlib.Path | str,
    manifest_path: pathlib.Path | str,
    allow_delete: bool = False,
) -> None:
    """Clean one probe workspace according to its classification.

    Every cleanup call is keyword-only and supplies every required argument.
    A classification outside the approved contract is refused BEFORE any
    cleanup function is called, so an unexpected value cannot trigger
    destructive cleanup.  A refusal raises; the driver reports it and never
    treats it as success.
    """
    from qwen_train.f2_s8_cleanup import cleanup_failed_build, cleanup_probe_workspace

    raw = row.get("classification")
    if not isinstance(raw, str) or not raw.strip():
        raise CleanupClassificationUnrecognized(task_id, raw)
    classification = raw.strip()
    if classification not in APPROVED_PROBE_CLASSIFICATIONS:
        raise CleanupClassificationUnrecognized(task_id, classification)
    if classification == "OBSERVED_COMPATIBLE":
        if row.get("failure"):
            # A compatible verdict plus a build-failure flag is contradictory.
            raise CleanupClassificationUnrecognized(
                task_id, f"{classification}+failure"
            )
        return
    root = pathlib.Path(workspace_root)
    ws = root / task_id
    if not ws.is_dir():
        return
    if classification in BUILD_FAILURE_CLASSIFICATIONS or row.get("failure"):
        cleanup_failed_build(
            task_id=task_id,
            workspace=ws,
            allowed_roots=[root],
            failure_class="build",
            manifest_path=manifest_path,
            allow_delete=allow_delete,
        )
        return
    if classification == "UNKNOWN":
        # The contract authorises deletion for a verified OBSERVED_INCOMPATIBLE
        # result, not for UNKNOWN.  The probe exists to resolve UNKNOWN, so its
        # workspace/evidence is retained.
        return
    cleanup_probe_workspace(
        task_id=task_id,
        workspace=ws,
        allowed_roots=[root],
        probe_report_path=pathlib.Path(output_dir) / "probe_report.json",
        build_log_path=pathlib.Path(output_dir) / f"{task_id}.build.log",
        manifest_path=manifest_path,
        allow_delete=allow_delete,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Fail-closed probe driver CLI. Requires a valid signed authorization grant."""
    from qwen_train.f2_s8_acquisition import require_unprivileged_identity

    try:
        require_unprivileged_identity("qwen_train.f2_s8_probe_driver.main")
    except Exception as exc:
        print(f"PROBE_NOT_AUTHORIZED: privileged identity refused: {exc}", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(description="F2 S8 Build-Probe Driver")
    ap.add_argument("--grant-file", default=os.environ.get("F2_AUTHORIZATION_FILE"))
    ap.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Run in dry-run/simulation mode (default)",
    )
    ap.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Run in live mode (requires prerequisites)",
    )
    ap.add_argument("--output-dir", default=r"C:\Users\rober\F2_SCRATCH\probe_output")
    ap.add_argument(
        "--compat-report",
        default=r"data\f2_population\plugin_compat\plugin_compat_report.json",
    )
    ap.add_argument(
        "--registration-file",
        default=r"C:\Users\rober\F2_EVIDENCE\s8\registration\registered_tasks.json",
    )
    ap.add_argument(
        "--registration-manifest",
        default=r"C:\Users\rober\F2_EVIDENCE\s8\registration\registered_tasks.provenance.json",
    )
    ap.add_argument(
        "--ledgers",
        nargs="*",
        default=[
            r"C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger.jsonl",
            r"C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger_corrected.jsonl",
        ],
    )
    ap.add_argument(
        "--budget-root",
        default=None,
        help="Explicit probe budget store root (tests MUST pass a temporary root)",
    )
    ap.add_argument(
        "--workspace-root",
        default=r"C:\f2_pilot",
        help="Probe workspace root (isolation: pass a temporary root in tests)",
    )
    args = ap.parse_args(argv)

    # Live execution is refused BEFORE any grant read or budget reservation.
    # Previously this check ran after authorize_s8_probe_run(reserve_execution=
    # True), so a blocked --live invocation consumed a production probe-budget
    # slot and then exited 1.  The refusal must come first.
    if not args.dry_run:
        print(
            "PROBE_PREREQUISITES_MISSING: live candidate build-probe execution requires "
            "owner-provisioned OS network isolation and offline wheelhouse. "
            "Live execution is disabled by fail-closed policy.",
            file=sys.stderr,
        )
        return 1

    if not args.grant_file or not pathlib.Path(args.grant_file).is_file():
        print(
            "PROBE_NOT_AUTHORIZED: no signed build-probe grant supplied. "
            "Pass --grant-file or set F2_AUTHORIZATION_FILE.",
            file=sys.stderr,
        )
        return 1

    from qwen_train.f2_s8_v2_gate import authorize_s8_probe_run

    try:
        authorized = authorize_s8_probe_run(
            grant_path=args.grant_file,
            registration_file=args.registration_file,
            registration_manifest_file=args.registration_manifest,
            compat_report_file=args.compat_report,
            ledger_paths=args.ledgers,
            budget_ledger_root=args.budget_root,
            # Only dry-run reaches this point (live is refused above).  A
            # verification/dry-run path must never reserve execution budget.
            reserve_execution=False,
        )
    except Exception as exc:
        print(f"PROBE_NOT_AUTHORIZED: {exc}", file=sys.stderr)
        return 1

    limits = ProbeLimits(
        max_tasks=authorized.budget_limits.get("max_total_tasks", 50),
        disk_floor_bytes=authorized.disk_floor_bytes,
        per_task_disk_bytes=authorized.per_task_disk_bytes,
        batch_limit=authorized.batch_limit,
        target_compatible=authorized.target_compatible,
    )
    def _revalidate():
        from qwen_train.f2_s8_v2_gate import check_probe_grant_still_valid

        check_probe_grant_still_valid(
            authorized, budget_ledger_root=args.budget_root
        )

    def _verify_ledgers():
        for p_str, old_sha in authorized.ledger_fingerprints_before.items():
            p = pathlib.Path(p_str)
            if not p.is_file():
                raise RuntimeError(f"ledger missing: {p}")
            curr_sha = hashlib.sha256(p.read_bytes()).hexdigest()
            if curr_sha != old_sha:
                raise RuntimeError(f"ledger mutated: {p}")

    def _cleanup_task(task_id: str, row: Mapping[str, Any]):
        cleanup_probe_task(
            task_id,
            row,
            workspace_root=args.workspace_root,
            output_dir=args.output_dir,
            manifest_path=pathlib.Path(args.output_dir) / "cleanup_manifest.jsonl",
            allow_delete=not args.dry_run,
        )

    result = run_probe(
        unknown_task_ids=list(authorized.selected_task_ids),
        limits=limits,
        authorize=_revalidate,
        verify_ledgers=_verify_ledgers,
        cleanup_after_task=_cleanup_task,
        # Explicit simulation builder: its UNKNOWN rows are dry-run output, not
        # a live build result.  A live builder must be supplied by the future
        # VM-contained implementation; until then live is refused above.
        builder=DryRunBuilder(),
        free_bytes=lambda: 10**18,
        allowed_task_ids=set(authorized.selected_task_ids),
    )
    print(f"Dry-run probe finished: stop_reason={result.stop_reason} attempted={result.attempted}")
    return 0 if result.stop_reason == "PROBE_COMPLETE" else 1


if __name__ == "__main__":  # pragma: no cover - CLI only
    raise SystemExit(main())
