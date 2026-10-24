"""Bounded S8 driver: COMPATIBLE, built, eligible tasks only; stops on target/caps.

The driver is deliberately read-only except for invoking the real runner for one
batch at a time. It never retries around a refusal and never creates a new
execution ID to bypass a stuck reservation.
"""

from __future__ import annotations

import json
import pathlib
import shutil
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

from qwen_train.f2_s8_factory import AppendOnlyLedger


class DriverHalt(RuntimeError):
    """A named, non-retryable driver stop reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class DriverLimits:
    target_credited: int
    max_executions: int
    max_total_tasks: int
    max_retries: int
    disk_floor_bytes: int
    per_task_disk_bytes: int
    batch_limit: int = 50

    def disk_threshold(self, batch_size: int) -> int:
        return max(
            self.disk_floor_bytes,
            3 * self.per_task_disk_bytes * max(1, batch_size),
        )


@dataclass
class DriverResult:
    stop_reason: str
    detail: str = ""
    batches: int = 0
    tasks_started: int = 0
    credited: int = 0
    log: list[str] = field(default_factory=list)


def plugin_classes(report: Mapping[str, object]) -> dict[str, str]:
    return {
        str(row["task_id"]): str(row["classification"])
        for row in report.get("results", [])  # type: ignore[union-attr]
    }


def select_batch(
    pool_task_ids: Sequence[str],
    classes: Mapping[str, str],
    build_manifest: Mapping[str, Mapping[str, object]],
    *,
    terminal_tasks: Iterable[str] = (),
    limit: int = 50,
) -> list[str]:
    """Select <=limit tasks that are COMPATIBLE, built, and not terminal."""
    if limit < 1 or limit > 50:
        raise ValueError("batch limit must be 1..50")
    terminal = set(terminal_tasks)
    selected: list[str] = []
    for task_id in pool_task_ids:
        if len(selected) >= limit:
            break
        if task_id in terminal:
            continue
        if classes.get(task_id) != "OBSERVED_COMPATIBLE":
            continue
        entry = build_manifest.get(task_id)
        if not isinstance(entry, Mapping) or entry.get("status") != "built":
            continue
        workspace = entry.get("workspace")
        if not workspace or not pathlib.Path(str(workspace)).is_dir():
            continue
        selected.append(task_id)
    return selected


def terminal_tasks(
    ledger_paths: Sequence[pathlib.Path | str],
) -> set[str]:
    out: set[str] = set()
    for path in ledger_paths:
        ledger = AppendOnlyLedger(path)
        for record in ledger.records():
            if record.get("outcome_class") == "scientific":
                out.add(str(record.get("task_id")))
    return out


def run_driver(
    *,
    limits: DriverLimits,
    pool_task_ids: Sequence[str],
    classes: Mapping[str, str],
    build_manifest: Mapping[str, Mapping[str, object]],
    check_grant: Callable[[], None],
    credited_count: Callable[[], int],
    run_batch: Callable[[Sequence[str]], Mapping[str, object]],
    free_bytes: Callable[[], int],
    cleanup_after_batch: Callable[[Sequence[str], Mapping[str, object]], None] | None = None,
    terminal: Iterable[str] = (),
    log: Callable[[str], None] = print,
) -> DriverResult:
    """Run batches until a named halt. Never retries around a refusal."""
    result = DriverResult(stop_reason="")
    terminal_set = set(terminal)
    batches = 0
    tasks_started = 0
    credited = 0

    def stop(reason: str, detail: str = "") -> DriverResult:
        result.stop_reason = reason
        result.detail = detail
        line = f"STOP {reason}" + (f": {detail}" if detail else "")
        result.log.append(line)
        log(line)
        return result

    while True:
        try:
            check_grant()
        except DriverHalt as exc:
            result.credited = credited
            return stop(exc.reason, exc.detail)
        except Exception as exc:  # noqa: BLE001 - any check failure is a halt
            result.credited = credited
            return stop("RUNNER_REFUSAL", f"grant check failed: {exc}")

        credited = int(credited_count())
        result.credited = credited
        if credited >= limits.target_credited:
            return stop(
                "TARGET_REACHED",
                f"credited={credited} target={limits.target_credited}",
            )
        if batches >= limits.max_executions:
            return stop("CAPS_EXHAUSTED", f"executions={batches}")
        batch = select_batch(
            pool_task_ids,
            classes,
            build_manifest,
            terminal_tasks=terminal_set,
            limit=min(50, limits.batch_limit),
        )
        if not batch:
            return stop("NO_ELIGIBLE_BUILT_TASK", "no compatible built task remains")
        threshold = limits.disk_threshold(len(batch))
        available = int(free_bytes())
        if available < threshold:
            return stop(
                "DISK_LOW",
                f"free={available} threshold={threshold} batch={len(batch)}",
            )
        if tasks_started + len(batch) > limits.max_total_tasks:
            return stop(
                "CAPS_EXHAUSTED",
                f"tasks={tasks_started}+{len(batch)}>{limits.max_total_tasks}",
            )

        batches += 1
        tasks_started += len(batch)
        line = f"batch {batches}: {len(batch)} tasks {batch[:3]}"
        result.log.append(line)
        log(line)
        try:
            outcome = dict(run_batch(batch))
        except DriverHalt as exc:
            result.batches = batches
            result.tasks_started = tasks_started
            return stop(exc.reason, exc.detail)
        except Exception as exc:  # noqa: BLE001 - runner refusal is a halt
            result.batches = batches
            result.tasks_started = tasks_started
            return stop("RUNNER_REFUSAL", f"{type(exc).__name__}: {exc}")

        halted = str(outcome.get("halt") or outcome.get("stop_reason") or "")
        if halted:
            result.batches = batches
            result.tasks_started = tasks_started
            return stop(halted, str(outcome.get("detail") or ""))
        attempted = int(outcome.get("attempted", len(batch)))
        infra = int(outcome.get("infrastructure_invalid", 0))
        if attempted > 0 and infra / attempted > 0.10:
            result.batches = batches
            result.tasks_started = tasks_started
            return stop(
                "INFRA_FAILURE_RATE",
                f"infrastructure_invalid={infra}/{attempted}",
            )
        if cleanup_after_batch is not None:
            try:
                cleanup_after_batch(batch, outcome)
            except Exception as exc:  # noqa: BLE001 - cleanup warning only
                warning = f"CLEANUP_WARNING: {type(exc).__name__}: {exc}"
                result.log.append(warning)
                log(warning)
        result.batches = batches
        result.tasks_started = tasks_started
        if result.batches >= limits.max_executions:
            return stop("CAPS_EXHAUSTED", f"executions={result.batches}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI is intentionally minimal; production use requires the signed grant."""
    import argparse

    p = argparse.ArgumentParser(description="Bounded S8 COMPATIBLE-only driver")
    p.add_argument("--pool", required=True)
    p.add_argument("--build-manifest", required=True)
    p.add_argument("--plugin-report", required=True)
    p.add_argument("--admission-ledger", action="append", required=True)
    p.add_argument("--grant", required=True)
    p.add_argument("--registration-sha256", required=True)
    p.add_argument("--registration-manifest-sha256", required=True)
    p.add_argument("--target-credited", type=int, default=300)
    p.add_argument("--max-executions", type=int, required=True)
    p.add_argument("--max-total-tasks", type=int, required=True)
    p.add_argument("--max-retries", type=int, required=True)
    p.add_argument("--batch-limit", type=int, default=50)
    p.add_argument("--disk-floor-bytes", type=int, required=True)
    p.add_argument("--per-task-disk-bytes", type=int, required=True)
    p.add_argument("--disk-path", action="append", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--inputs", required=True)
    p.add_argument("--gates", required=True)
    p.add_argument("--staging", required=True)
    p.add_argument("--registration-file", required=True)
    from qwen_train import f2_authorization as A
    from qwen_train import f2_s8_pilot_live as runner
    from qwen_train.f2_s8_candidate_pool import load_pool, pool_digest
    from qwen_train.f2_s8_precheck import _registered_task_ids
    from qwen_train.f2_s8_batch import reconcile_admissions_across_chains

    args = p.parse_args(argv)
    pool, _ = load_pool(args.pool)
    build = json.loads(pathlib.Path(args.build_manifest).read_text("utf-8"))
    report = json.loads(pathlib.Path(args.plugin_report).read_text("utf-8"))
    classes = plugin_classes(report)
    registration_ids = _registered_task_ids(pathlib.Path(args.registration_file))
    out_root = pathlib.Path(args.out)

    def check_grant() -> None:
        A.verify_authorization(
            args.grant,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_pool_digest": pool_digest(pool),
                "registration_sha256": args.registration_sha256,
                "registration_manifest_sha256": args.registration_manifest_sha256,
            },
        )

    def credited_count() -> int:
        return int(
            reconcile_admissions_across_chains(
                args.admission_ledger, registration_ids
            )["credited_count"]
        )

    batch_no = {"n": 0}

    def run_batch(batch: Sequence[str]) -> Mapping[str, object]:
        batch_no["n"] += 1
        bdir = out_root / f"batch_{batch_no['n']:03d}"
        bdir.mkdir(parents=True, exist_ok=True)
        manifest = {
            tid: {
                "status": "built",
                "workspace": build[tid]["workspace"],
                "project_subdir": build[tid].get("project_subdir", ""),
            }
            for tid in batch
        }
        mpath = bdir / "manifest.json"
        mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
        argv_main = [
            "--manifest", str(mpath),
            "--inputs", args.inputs,
            "--registration-id", "s8-admission",
            "--registration-sha256", args.registration_sha256,
            "--registration-file", args.registration_file,
            "--candidate-pool-file", args.pool,
            "--ledger", args.admission_ledger[0],
            "--staging", args.staging,
            "--out", str(bdir),
            "--gates", args.gates,
        ]
        runner.main(argv_main)
        payload = json.loads((bdir / "pilot_results.json").read_text("utf-8"))
        totals = payload.get("totals") or {}
        return {
            "attempted": int(totals.get("attempted", 0)),
            "infrastructure_invalid": int(totals.get("infrastructure_invalid", 0)),
            "task_ids": list(batch),
        }

    result = run_driver(
        limits=DriverLimits(
            target_credited=args.target_credited,
            max_executions=args.max_executions,
            max_total_tasks=args.max_total_tasks,
            max_retries=args.max_retries,
            disk_floor_bytes=args.disk_floor_bytes,
            per_task_disk_bytes=args.per_task_disk_bytes,
            batch_limit=args.batch_limit,
        ),
        pool_task_ids=pool["task_ids"],
        classes=classes,
        build_manifest=build,
        check_grant=check_grant,
        credited_count=credited_count,
        run_batch=run_batch,
        free_bytes=lambda: min(
            shutil.disk_usage(path).free for path in args.disk_path
        ),
        terminal=terminal_tasks(args.admission_ledger),
    )
    print(json.dumps(result.__dict__, indent=1, sort_keys=True))
    return 0 if result.stop_reason in {"TARGET_REACHED"} else 1
