"""Bounded, reusable S8 authorization (v2) — executions, tasks, retries.

UNITS (fixed here, referenced by every caller)
    execution  one protected runner invocation
    reservation durable claim made BEFORE that invocation may run
    task       one candidate task in that execution's manifest
    attempt    one actual run of a task
    retry      an additional attempt after the first, claimed individually

LIMITS (all REQUIRED on a v2 record; no defaults)
    max_executions   grant-wide reservations (1..500)
    max_total_tasks  grant-wide task capacity, claimed at reservation time (1..5000)
    max_retries      grant-wide retry claims (0..5000)

STORAGE
    <root>/<record_digest>/
        .transaction.lock                     persistent store-wide coordination lock
        exec/<sha256(execution_id)>.lock      execution-ID claim
        <NNNNNN>.json                         committed reservation slot
        tasks/<NNNNNN>.json                   attributable claimed task unit
        retries/claims/<hash>.lock            retry pre-claim lock
        retries/<NNNNNN>.json                 claimed retry slot

Every claim is an atomic creation, and every multi-step transaction is guarded
by the persistent store-wide lock `.transaction.lock` with operating-system locking.
The committed reservation slot is created AFTER task capacity is allocated,
preventing committed slots from referencing unallocated tasks.

The production root is ``None`` and every v2 reservation fails closed until the
owner authorizes a separate production ledger. The root is injectable only as a
function argument; no environment variable, CLI flag or config key selects it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import re
import time
from typing import Any, Mapping

from qwen_train.f2_authorization import AuthorizationError, record_digest

SCHEMA_V2 = "f2-authorization/v2"
RESERVATION_SCHEMA = "f2-authz-reservation/v1"
TASK_SCHEMA = "f2-reservation-task/v1"
SLOT_SCHEMA = "f2-reservation-slot/v1"

MAX_EXECUTIONS_CEILING = 500
MAX_TASKS_CEILING = 5000
MAX_RETRIES_CEILING = 5000

_SLOT_RE = re.compile(r"^\d{6}\.json$")
_ALLOWED_DIRS = {"exec", "tasks", "retries", "audit"}
_LOCK_FILE_NAME = ".transaction.lock"

PRODUCTION_BUDGET_ROOT: pathlib.Path | None = None


class BudgetError(AuthorizationError):
    """Budget policy is absent, malformed, exhausted, or the store is ambiguous."""


def budget_root(explicit: pathlib.Path | str | None = None) -> pathlib.Path:
    if explicit is not None:
        return pathlib.Path(explicit)
    if PRODUCTION_BUDGET_ROOT is None:
        raise BudgetError(
            "no v2 budget root is authorized: PRODUCTION_BUDGET_ROOT is unset"
        )
    return pathlib.Path(PRODUCTION_BUDGET_ROOT)


def budget_of(record: Mapping[str, Any]) -> dict[str, int] | None:
    if record.get("schema") != SCHEMA_V2:
        return None
    raw = record.get("budget")
    if not isinstance(raw, Mapping):
        raise BudgetError("v2 authorization declares no budget object")
    out: dict[str, int] = {}
    for key, ceiling in (
        ("max_executions", MAX_EXECUTIONS_CEILING),
        ("max_total_tasks", MAX_TASKS_CEILING),
        ("max_retries", MAX_RETRIES_CEILING),
    ):
        if key not in raw:
            raise BudgetError(f"budget is missing required field {key!r}")
        val = raw[key]
        if isinstance(val, bool) or not isinstance(val, int):
            raise BudgetError(f"{key} is malformed: integer required, got {type(val).__name__}")
        if key == "max_retries":
            if val < 0 or val > ceiling:
                raise BudgetError(f"{key} must be 0..{ceiling}, got {val}")
        elif val < 1 or val > ceiling:
            raise BudgetError(f"{key} must be 1..{ceiling}, got {val}")
        out[key] = val
    return out


def _grant_dir(root: pathlib.Path, digest: str) -> pathlib.Path:
    if not digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise BudgetError(f"unsafe record digest: {digest!r}")
    return root / digest


def _read_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(d, dict) and d.get("schema") in (RESERVATION_SCHEMA, SLOT_SCHEMA, TASK_SCHEMA):
        return d
    return None


@contextlib.contextmanager
def store_transaction_lock(grant_dir: pathlib.Path):
    """Store-wide coordination lock using persistent .transaction.lock.

    The file is never deleted on release.
    """
    grant_dir.mkdir(parents=True, exist_ok=True)
    lock_path = grant_dir / _LOCK_FILE_NAME
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    start_time = time.monotonic()
    timeout = 30.0
    locked = False
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (OSError, PermissionError):
                if time.monotonic() - start_time > timeout:
                    raise BudgetError(
                        "timeout acquiring store transaction lock"
                    ) from None
                time.sleep(0.02)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _claim(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    """Atomic create-if-absent with durable content. Raises BudgetError on clash."""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise
    except OSError as exc:
        raise BudgetError(f"claim store unusable: {exc}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def classify_store(grant_dir: pathlib.Path, record_digest_value: str) -> dict[str, Any]:
    """Audit the store against the 12 mutually exclusive accounting categories."""
    empty_result = {
        "status": "HEALTHY",
        "reason": "",
        "counts": {"slots": 0, "exec": 0, "tasks": 0, "retries": 0},
        "slots": [],
        "tasks": [],
        "retries": [],
    }
    if not grant_dir.is_dir():
        return empty_result

    # 1. Inspect top-level directory entries
    for p in sorted(grant_dir.iterdir()):
        if p.is_dir():
            if p.name not in _ALLOWED_DIRS:
                return {
                    "status": "ANOMALY",
                    "reason": f"unexpected directory in budget store: {p.name!r}",
                    "counts": empty_result["counts"],
                }
        else:
            if p.name == _LOCK_FILE_NAME:
                continue
            if not _SLOT_RE.match(p.name):
                return {
                    "status": "ANOMALY",
                    "reason": f"unexpected file in budget store: {p.name!r}",
                    "counts": empty_result["counts"],
                }

    # 2. Inspect exec/
    exec_dir = grant_dir / "exec"
    exec_locks: dict[str, dict[str, Any]] = {}
    if exec_dir.is_dir():
        for q in sorted(exec_dir.iterdir()):
            if not q.is_file() or not q.name.endswith(".lock"):
                return {
                    "status": "MALFORMED_LOCK",
                    "reason": f"unexpected entry in exec/: {q.name!r}",
                    "counts": empty_result["counts"],
                }
            try:
                data = json.loads(q.read_text(encoding="utf-8"))
            except Exception as exc:
                return {
                    "status": "MALFORMED_LOCK",
                    "reason": f"malformed execution lock {q.name}: {exc}",
                    "counts": empty_result["counts"],
                }
            if (
                not isinstance(data, dict)
                or data.get("schema") != RESERVATION_SCHEMA
                or data.get("kind") != "execution-claim"
            ):
                return {
                    "status": "MALFORMED_LOCK",
                    "reason": f"invalid schema or kind in execution lock {q.name}",
                    "counts": empty_result["counts"],
                }
            eid = data.get("execution_id")
            if not isinstance(eid, str) or not eid:
                return {
                    "status": "MALFORMED_LOCK",
                    "reason": f"missing execution_id in execution lock {q.name}",
                    "counts": empty_result["counts"],
                }
            expected_name = hashlib.sha256(eid.encode("utf-8")).hexdigest() + ".lock"
            if q.name != expected_name:
                return {
                    "status": "MALFORMED_LOCK",
                    "reason": f"execution lock filename mismatch: {q.name} != {expected_name}",
                    "counts": empty_result["counts"],
                }
            exec_locks[eid] = data

    # 3. Inspect tasks/
    tasks_dir = grant_dir / "tasks"
    task_records: list[dict[str, Any]] = []
    if tasks_dir.is_dir():
        for q in sorted(tasks_dir.iterdir()):
            if not q.is_file() or not _SLOT_RE.match(q.name):
                return {
                    "status": "ANOMALY",
                    "reason": f"unexpected entry in tasks/: {q.name!r}",
                    "counts": empty_result["counts"],
                }
            try:
                data = json.loads(q.read_text(encoding="utf-8"))
            except Exception as exc:
                return {
                    "status": "MALFORMED_TASK",
                    "reason": f"malformed task record {q.name}: {exc}",
                    "counts": empty_result["counts"],
                }
            if not isinstance(data, dict) or data.get("schema") not in (TASK_SCHEMA, RESERVATION_SCHEMA):
                return {
                    "status": "MALFORMED_TASK",
                    "reason": f"invalid schema in task record {q.name}",
                    "counts": empty_result["counts"],
                }
            task_records.append(data)

    # 4. Inspect retries/
    retries_dir = grant_dir / "retries"
    retry_slots: list[dict[str, Any]] = []
    retry_claims: dict[str, dict[str, Any]] = {}
    if retries_dir.is_dir():
        for q in sorted(retries_dir.iterdir()):
            if q.is_dir():
                if q.name == "claims":
                    for c in sorted(q.iterdir()):
                        if not c.is_file() or not c.name.endswith(".lock"):
                            return {
                                "status": "ANOMALY",
                                "reason": f"unexpected file in retries/claims: {c.name}",
                                "counts": empty_result["counts"],
                            }
                        try:
                            cdata = json.loads(c.read_text(encoding="utf-8"))
                        except Exception:
                            return {
                                "status": "MALFORMED_RETRY",
                                "reason": f"malformed retry claim lock {c.name}",
                                "counts": empty_result["counts"],
                            }
                        if not isinstance(cdata, dict) or cdata.get("schema") != RESERVATION_SCHEMA:
                            return {
                                "status": "MALFORMED_RETRY",
                                "reason": f"invalid schema in retry claim lock {c.name}",
                                "counts": empty_result["counts"],
                            }
                        retry_claims[c.name] = cdata
                else:
                    return {
                        "status": "ANOMALY",
                        "reason": f"unexpected directory in retries/: {q.name}",
                        "counts": empty_result["counts"],
                    }
            elif q.is_file():
                if not _SLOT_RE.match(q.name):
                    return {
                        "status": "ANOMALY",
                        "reason": f"unexpected file in retries/: {q.name}",
                        "counts": empty_result["counts"],
                    }
                try:
                    rdata = json.loads(q.read_text(encoding="utf-8"))
                except Exception:
                    return {
                        "status": "MALFORMED_RETRY",
                        "reason": f"malformed retry record {q.name}",
                        "counts": empty_result["counts"],
                    }
                if not isinstance(rdata, dict) or rdata.get("schema") != RESERVATION_SCHEMA:
                    return {
                        "status": "MALFORMED_RETRY",
                        "reason": f"invalid schema in retry slot {q.name}",
                        "counts": empty_result["counts"],
                    }
                retry_slots.append(rdata)

    # 5. Inspect committed reservation slots in grant_dir
    committed_slots: list[dict[str, Any]] = []
    for p in sorted(grant_dir.iterdir()):
        if p.is_file() and _SLOT_RE.match(p.name):
            try:
                sdata = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {
                    "status": "MALFORMED_SLOT",
                    "reason": f"malformed reservation record: {p.name}",
                    "counts": empty_result["counts"],
                }
            if not isinstance(sdata, dict) or sdata.get("schema") not in (RESERVATION_SCHEMA, SLOT_SCHEMA):
                return {
                    "status": "MALFORMED_SLOT",
                    "reason": f"invalid schema in reservation record {p.name}",
                    "counts": empty_result["counts"],
                }
            committed_slots.append(sdata)

    counts = {
        "slots": len(committed_slots),
        "exec": len(exec_locks),
        "tasks": len(task_records),
        "retries": len(retry_slots),
    }

    # 6. Correlate and classify against the 12 accounting states
    slots_by_eid: dict[str, list[dict[str, Any]]] = {}
    for s in committed_slots:
        eid = s.get("execution_id")
        if not isinstance(eid, str) or not eid:
            return {
                "status": "MALFORMED_SLOT",
                "reason": "committed slot missing execution_id",
                "counts": counts,
            }
        slots_by_eid.setdefault(eid, []).append(s)

    # Case 8: Conflicting duplicate committed slots
    for eid, s_list in slots_by_eid.items():
        if len(s_list) > 1:
            return {
                "status": "UNRESOLVED",
                "reason": f"conflicting duplicate committed slots for execution_id {eid!r}",
                "counts": counts,
            }

    # Case 1 / Case 3 / Case 4: Verify each committed slot and its tasks
    for s in committed_slots:
        eid = s["execution_id"]
        tc = s.get("task_count")
        if isinstance(tc, bool) or not isinstance(tc, int) or tc < 1:
            return {
                "status": "MALFORMED_SLOT",
                "reason": f"invalid task_count in slot for {eid!r}",
                "counts": counts,
            }
        if eid not in exec_locks:
            return {
                "status": "INCONSISTENT",
                "reason": f"committed slot for {eid!r} missing matching exec lock",
                "counts": counts,
            }
        matching_tasks = [t for t in task_records if t.get("execution_id") == eid]
        if len(matching_tasks) < tc:
            return {
                "status": "INCONSISTENT",
                "reason": f"committed slot for {eid!r} has fewer tasks ({len(matching_tasks)}) than declared ({tc})",
                "counts": counts,
            }
        if len(matching_tasks) > tc:
            # Case 3: Provisional reservation task count exceeds declared reservation
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": f"task count ({len(matching_tasks)}) exceeds declared ({tc}) for {eid!r}",
                "counts": counts,
            }
        task_indices = [t.get("task_index") for t in matching_tasks]
        task_ids = [t.get("task_id") for t in matching_tasks]
        if any(isinstance(idx, bool) or not isinstance(idx, int) or idx < 0 for idx in task_indices):
            return {
                "status": "INCONSISTENT",
                "reason": f"invalid task_index in tasks for {eid!r}",
                "counts": counts,
            }
        if sorted(task_indices) != list(range(tc)):
            return {
                "status": "INCONSISTENT",
                "reason": f"duplicate or non-contiguous task_index in tasks for {eid!r}",
                "counts": counts,
            }
        if len(set(task_ids)) != len(task_ids):
            return {
                "status": "INCONSISTENT",
                "reason": f"duplicate task_id in tasks for {eid!r}",
                "counts": counts,
            }
        s_md = s.get("manifest_digest")
        if s_md:
            for t in matching_tasks:
                t_md = t.get("manifest_digest")
                if t_md and t_md != s_md:
                    return {
                        "status": "INCONSISTENT",
                        "reason": f"manifest_digest mismatch between slot and task for {eid!r}",
                        "counts": counts,
                    }

    # Case 2: Incomplete execution reservation (lock without slot)
    committed_eids = set(slots_by_eid.keys())
    for eid in exec_locks:
        if eid not in committed_eids:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": f"incomplete reservation: execution_id {eid!r} claimed lock but no committed slot exists",
                "counts": counts,
            }

    # Case 7: Orphan task records
    all_eids = set(exec_locks.keys()) | committed_eids
    for t in task_records:
        t_eid = t.get("execution_id")
        if t_eid not in all_eids:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": f"orphan task record found with execution_id {t_eid!r}",
                "counts": counts,
            }

    # Case 9 & Case 10: Retries verification
    for r in retry_slots:
        r_eid = r.get("execution_id")
        r_tid = r.get("task_id")
        r_att = r.get("retry_attempt", 1)
        if r_eid not in committed_eids:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": f"retry references uncommitted execution {r_eid!r}",
                "counts": counts,
            }
        slot = slots_by_eid[r_eid][0]
        slot_tasks = slot.get("task_ids") or []
        if not slot_tasks:
            slot_tasks = [t.get("task_id") for t in task_records if t.get("execution_id") == r_eid]
        if r_tid not in slot_tasks:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": f"retry references unknown task {r_tid!r} in execution {r_eid!r}",
                "counts": counts,
            }
        expected_claim_key = (
            hashlib.sha256(f"{r_eid}:{r_tid}:{r_att}".encode("utf-8")).hexdigest() + ".lock"
        )
        if expected_claim_key not in retry_claims:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": f"retry slot missing matching claim lock {expected_claim_key}",
                "counts": counts,
            }

    claimed_keys = {
        hashlib.sha256(f"{r.get('execution_id')}:{r.get('task_id')}:{r.get('retry_attempt', 1)}".encode("utf-8")).hexdigest() + ".lock"
        for r in retry_slots
    }
    for cname in retry_claims:
        if cname not in claimed_keys:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": f"incomplete retry claim: lock {cname} has no retry slot",
                "counts": counts,
            }

    return {
        "status": "HEALTHY",
        "reason": "",
        "counts": counts,
        "slots": committed_slots,
        "tasks": task_records,
        "retries": retry_slots,
    }


def audit(record_digest_value: str, ledger_root: pathlib.Path | str | None = None) -> dict[str, int]:
    """Classify every entry. Any unknown, malformed, or unresolved entry fails closed."""
    d = _grant_dir(budget_root(ledger_root), record_digest_value)
    report = classify_store(d, record_digest_value)
    if report["status"] != "HEALTHY":
        raise BudgetError(f"budget store is {report['status']}: {report['reason']}")
    return report["counts"]


def usage(record_digest_value: str, ledger_root: pathlib.Path | str | None = None) -> int:
    return audit(record_digest_value, ledger_root)["slots"]


def _allocate(dirpath: pathlib.Path, limit: int, payload: Mapping[str, Any], label: str) -> int:
    for seq in range(limit):
        path = dirpath / f"{seq:06d}.json"
        try:
            _claim(path, dict(payload, seq=seq, kind=label))
        except FileExistsError:
            continue
        return seq
    raise BudgetError(f"{label} capacity exhausted ({limit})")


def reserve(
    *,
    record: Mapping[str, Any],
    binding: Mapping[str, Any],
    binding_digest: str,
    stage: str,
    operation: str,
    execution_id: str,
    task_count: int = 1,
    task_ids: list[str] | None = None,
    manifest_digest: str = "",
    ledger_root: pathlib.Path | str | None = None,
) -> dict[str, Any]:
    """Claim one execution ID, one reservation slot, and ``task_count`` task units.

    Coordinates via persistent .transaction.lock under store-wide locking.
    """
    budget = budget_of(record)
    if budget is None:
        raise BudgetError("not a budgeted (v2) authorization")

    if isinstance(task_count, bool) or not isinstance(task_count, int):
        raise BudgetError(f"task_count is malformed: integer required, got {type(task_count).__name__}")
    if task_count < 1 or task_count > MAX_TASKS_CEILING:
        raise BudgetError(f"task_count must be 1..{MAX_TASKS_CEILING}, got {task_count}")

    if budget["max_executions"] > 1:
        if not execution_id:
            raise BudgetError("explicit execution_id is required when max_executions > 1")
    else:
        if not execution_id:
            execution_id = str(record.get("id") or "")
        if not execution_id:
            raise BudgetError("execution_id is required before reservation")

    if task_ids is not None:
        if not isinstance(task_ids, list) or not all(isinstance(tid, str) and tid for tid in task_ids):
            raise BudgetError("task_ids must be a list of non-empty strings")
        if len(task_ids) != task_count:
            raise BudgetError(f"task_ids length ({len(task_ids)}) != task_count ({task_count})")
        if len(set(task_ids)) != len(task_ids):
            raise BudgetError("duplicate task_id in task_ids list")
    else:
        task_ids = [f"task-{i}" for i in range(task_count)]

    digest = record_digest(record)
    root = budget_root(ledger_root)
    gdir = _grant_dir(root, digest)
    gdir.mkdir(parents=True, exist_ok=True)

    with store_transaction_lock(gdir):
        # 1. Audit store before taking actions (fail closed on malformed/unresolved state)
        report = classify_store(gdir, digest)
        if report["status"] != "HEALTHY":
            raise BudgetError(f"budget store is {report['status']} ({report['reason']}); reconciliation required")

        # 2. Check budget limits
        current_slots = report["counts"]["slots"]
        if current_slots + 1 > budget["max_executions"]:
            raise BudgetError(f"execution capacity exhausted ({budget['max_executions']})")
        current_tasks = report["counts"]["tasks"]
        if current_tasks + task_count > budget["max_total_tasks"]:
            raise BudgetError(f"task capacity exhausted ({budget['max_total_tasks']})")

        for sub in ("exec", "tasks", "retries"):
            (gdir / sub).mkdir(exist_ok=True)

        # 3. Execution-ID claim (atomic)
        lock = gdir / "exec" / (hashlib.sha256(execution_id.encode("utf-8")).hexdigest() + ".lock")
        try:
            _claim(
                lock,
                {
                    "schema": RESERVATION_SCHEMA,
                    "kind": "execution-claim",
                    "execution_id": execution_id,
                    "task_count": task_count,
                    "manifest_digest": manifest_digest,
                    "claimed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "pid": os.getpid(),
                },
            )
        except FileExistsError as exc:
            raise BudgetError(f"duplicate execution_id {execution_id!r} already reserved") from exc

        # 4. Task capacity allocation (grant-wide, sequential, attributable)
        for idx, tid in enumerate(task_ids):
            _allocate(
                gdir / "tasks",
                budget["max_total_tasks"],
                {
                    "schema": TASK_SCHEMA,
                    "record_digest": digest,
                    "execution_id": execution_id,
                    "manifest_digest": manifest_digest,
                    "task_id": tid,
                    "task_index": idx,
                },
                "task",
            )

        # 5. Committed reservation slot (created AFTER tasks are allocated)
        seq = _allocate(
            gdir,
            budget["max_executions"],
            {
                "schema": RESERVATION_SCHEMA,
                "record_digest": digest,
                "stage": stage,
                "operation": operation,
                "execution_id": execution_id,
                "binding": dict(binding),
                "binding_digest": binding_digest,
                "task_count": task_count,
                "manifest_digest": manifest_digest,
                "task_ids": task_ids,
                "reserved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pid": os.getpid(),
            },
            "execution",
        )

        entry = _read_json(gdir / f"{seq:06d}.json")
        assert entry is not None
        entry["seq"] = seq
        return entry


def claim_retry(
    *,
    record: Mapping[str, Any],
    execution_id: str,
    task_id: str,
    retry_attempt: int = 1,
    ledger_root: pathlib.Path | str | None = None,
) -> str:
    """Claim one retry unit. Atomic; key is (execution_id, task_id, retry_attempt)."""
    budget = budget_of(record)
    if budget is None:
        raise BudgetError("not a budgeted (v2) authorization")
    if not execution_id or not task_id:
        raise BudgetError("execution_id and task_id are required for a retry claim")
    if isinstance(retry_attempt, bool) or not isinstance(retry_attempt, int) or retry_attempt < 1:
        raise BudgetError(f"retry_attempt must be integer >= 1, got {retry_attempt!r}")

    digest = record_digest(record)
    root = budget_root(ledger_root)
    gdir = _grant_dir(root, digest)
    gdir.mkdir(parents=True, exist_ok=True)

    with store_transaction_lock(gdir):
        report = classify_store(gdir, digest)
        if report["status"] != "HEALTHY":
            raise BudgetError(f"budget store is {report['status']} ({report['reason']}); reconciliation required")

        committed = [s for s in report["slots"] if s.get("execution_id") == execution_id]
        if not committed:
            raise BudgetError(f"retry references uncommitted execution_id {execution_id!r}")
        slot = committed[0]
        slot_tasks = slot.get("task_ids") or []
        if not slot_tasks:
            slot_tasks = [t.get("task_id") for t in report["tasks"] if t.get("execution_id") == execution_id]
        if task_id not in slot_tasks:
            raise BudgetError(f"retry references unknown task {task_id!r} in execution {execution_id!r}")

        # Pre-claim identity atomically before allocating capacity
        claims_dir = gdir / "retries" / "claims"
        claims_dir.mkdir(parents=True, exist_ok=True)
        claim_key = hashlib.sha256(f"{execution_id}:{task_id}:{retry_attempt}".encode("utf-8")).hexdigest() + ".lock"
        claim_lock = claims_dir / claim_key
        try:
            _claim(
                claim_lock,
                {
                    "schema": RESERVATION_SCHEMA,
                    "kind": "retry-claim-lock",
                    "execution_id": execution_id,
                    "task_id": task_id,
                    "retry_attempt": retry_attempt,
                    "pid": os.getpid(),
                },
            )
        except FileExistsError as exc:
            # Clean duplicate retry claim: rejected without consuming retry capacity
            raise BudgetError(f"retry already claimed for {execution_id}/{task_id}") from exc

        # Check retry budget capacity
        current_retries = report["counts"]["retries"]
        if current_retries + 1 > budget["max_retries"]:
            try:
                claim_lock.unlink()
            except OSError:
                pass
            raise BudgetError(f"retry capacity exhausted ({budget['max_retries']})")

        slot = _allocate(
            gdir / "retries",
            budget["max_retries"],
            {
                "schema": RESERVATION_SCHEMA,
                "record_digest": digest,
                "kind": "retry-claim",
                "execution_id": execution_id,
                "task_id": task_id,
                "retry_attempt": retry_attempt,
            },
            "retry",
        )
        return f"{slot:06d}.json"


def remaining(record: Mapping[str, Any], ledger_root: pathlib.Path | str | None = None) -> dict[str, int]:
    budget = budget_of(record)
    if budget is None:
        raise BudgetError("not a budgeted (v2) authorization")
    d = _grant_dir(budget_root(ledger_root), record_digest(record))
    report = classify_store(d, record_digest(record))
    if report["status"] == "UNRESOLVED":
        # Sentinel for unresolved state: operational remaining capacity is zero
        return {"executions": 0, "tasks": 0, "retries": 0}
    if report["status"] != "HEALTHY":
        raise BudgetError(f"budget store is {report['status']}: {report['reason']}")
    counts = report["counts"]
    return {
        "executions": budget["max_executions"] - counts["slots"],
        "tasks": budget["max_total_tasks"] - counts["tasks"],
        "retries": budget["max_retries"] - counts["retries"],
    }
