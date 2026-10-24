"""DRY-RUN build-probe driver. Never runs without an authorized probe grant.

The only production-visible entry point refuses; tests inject a fake authorize and a
fake builder. No network, build, or production store is touched here.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence


class ProbeHalt(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class ProbeLimits:
    max_tasks: int
    disk_floor_bytes: int
    per_task_disk_bytes: int
    per_task_time_seconds: int = 3600
    batch_limit: int = 50
    max_infra_rate: float = 0.10

    def disk_threshold(self, batch_size: int) -> int:
        return max(
            self.disk_floor_bytes,
            3 * self.per_task_disk_bytes * max(1, batch_size),
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
    log: list[str] = field(default_factory=list)


def select_probe_batch(unknown_task_ids: Sequence[str], *, limit: int = 50) -> list[str]:
    if limit < 1 or limit > 50:
        raise ValueError("probe batch limit must be 1..50")
    return list(unknown_task_ids[:limit])


def run_probe(
    *,
    unknown_task_ids: Sequence[str],
    limits: ProbeLimits,
    authorize: Callable[[], None],
    builder: Callable[[Sequence[str]], Mapping[str, Mapping[str, object]]],
    free_bytes: Callable[[], int],
    log: Callable[[str], None] = print,
) -> ProbeResult:
    """Fake-builder dry run. Never writes credits or production state."""
    result = ProbeResult(stop_reason="")
    pending = list(unknown_task_ids)
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

    while pending:
        if result.attempted >= limits.max_tasks:
            return stop("PROBE_CAPS_EXHAUSTED", f"tasks={result.attempted}")
        remaining_cap = limits.max_tasks - result.attempted
        if remaining_cap < 1:
            return stop("PROBE_CAPS_EXHAUSTED", f"tasks={result.attempted}")
        batch = select_probe_batch(
            pending, limit=min(50, limits.batch_limit, remaining_cap)
        )
        if not batch:
            return stop("PROBE_NO_ELIGIBLE_TASK")
        threshold = limits.disk_threshold(len(batch))
        available = int(free_bytes())
        if available < threshold:
            return stop(
                "PROBE_DISK_LOW",
                f"free={available} threshold={threshold} batch={len(batch)}",
            )
        batches += 1
        line = f"probe batch {batches}: {len(batch)} tasks"
        result.log.append(line)
        log(line)
        try:
            outcome = dict(builder(batch))
        except ProbeHalt as exc:
            result.batches = batches
            return stop(exc.reason, exc.detail)
        except Exception as exc:  # noqa: BLE001 - builder failure is infra
            result.batches = batches
            result.attempted += len(batch)
            result.infrastructure_invalid += len(batch)
            pending = pending[len(batch):]
            return stop("PROBE_INFRA_FAILURE_RATE", f"builder failed: {exc}")

        infra = 0
        for task_id in batch:
            row = outcome.get(task_id) or {}
            classification = str(row.get("classification") or "UNKNOWN")
            result.attempted += 1
            if classification == "OBSERVED_COMPATIBLE":
                result.compatible += 1
                workspace = row.get("workspace")
                if workspace and pathlib.Path(str(workspace)).is_dir():
                    result.built += 1
            elif classification == "OBSERVED_INCOMPATIBLE":
                result.incompatible += 1
            else:
                result.unknown += 1
            if row.get("failure"):
                infra += 1
        result.infrastructure_invalid += infra
        pending = pending[len(batch):]
        result.batches = batches
        if batch and infra / len(batch) > limits.max_infra_rate:
            return stop(
                "PROBE_INFRA_FAILURE_RATE",
                f"infrastructure_invalid={infra}/{len(batch)}",
            )
    return stop("PROBE_COMPLETE", f"tasks={result.attempted}")


def main(argv: Sequence[str] | None = None) -> int:
    """No signed probe grant schema exists; real activation is refused by design."""
    print(
        "PROBE_NOT_AUTHORIZED: no signed build-probe grant exists. "
        "This module is dry-run/test-only; use fake builder tests."
    )
    return 1
