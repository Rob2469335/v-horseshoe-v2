"""Bounded, reusable S8 authorization (v2) — executions, tasks, retries.

UNITS (fixed here, referenced by every caller)
    execution  one protected runner invocation
    reservation durable claim made BEFORE that invocation may run
    task       one candidate task in that execution's manifest
    attempt    one actual run of a task
    retry      an additional attempt after the first, claimed individually

LIMITS (all REQUIRED on a v2 record; no defaults)
    max_executions   grant-wide reservations
    max_total_tasks  grant-wide task capacity, claimed at reservation time
    max_retries      grant-wide retry claims

STORAGE
    <root>/<record_digest>/
        exec/<sha256(execution_id)>.lock      execution-ID claim
        <NNNNNN>.json                         reservation slot
        tasks/<NNNNNN>.json                   one claimed task unit
        retries/<sha256(exec, task)>.json     one claimed retry

Every claim is a single ``O_CREAT | O_EXCL`` create, so N processes racing for
the last unit produce exactly one winner. There is no read-then-write path.

The production root is ``None`` and every v2 reservation fails closed until the
owner authorizes a separate production ledger. The root is injectable only as a
function argument; no environment variable, CLI flag or config key selects it.

TAMPER LIMITS (``NOT ESTABLISHED``): a principal with write access to the root
can delete or roll these files back. Nothing here detects that.
"""

from __future__ import annotations

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
MAX_EXECUTIONS_CEILING = 500
MAX_TASKS_CEILING = 5000

_SLOT_RE = re.compile(r"^\d{6}\.json$")
_ALLOWED_DIRS = {"exec", "tasks", "retries"}

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
    for key, ceiling in (("max_executions", MAX_EXECUTIONS_CEILING),
                         ("max_total_tasks", MAX_TASKS_CEILING),
                         ("max_retries", MAX_TASKS_CEILING)):
        if key not in raw:
            raise BudgetError(f"budget is missing required field {key!r}")
        try:
            value = int(raw[key])
        except (TypeError, ValueError) as exc:
            raise BudgetError(f"{key} is malformed: {exc}") from exc
        if key == "max_retries":
            if value < 0 or value > ceiling:
                raise BudgetError(f"{key} must be 0..{ceiling}, got {value}")
        elif value < 1 or value > ceiling:
            raise BudgetError(f"{key} must be 1..{ceiling}, got {value}")
        out[key] = value
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
    return d if isinstance(d, dict) and d.get("schema") == RESERVATION_SCHEMA else None


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


def audit(record_digest_value: str, ledger_root: pathlib.Path | str | None = None) -> dict[str, int]:
    """Classify every entry. ANY unknown entry fails closed.

    Renaming a reservation-like file does not free capacity: an unexpected name
    is an error, not an ignorable file.
    """
    d = _grant_dir(budget_root(ledger_root), record_digest_value)
    counts = {"slots": 0, "exec": 0, "tasks": 0, "retries": 0}
    if not d.is_dir():
        return counts
    for p in sorted(d.iterdir()):
        if p.is_dir():
            if p.name not in _ALLOWED_DIRS:
                raise BudgetError(f"unexpected directory in budget store: {p.name!r}")
            for q in sorted(p.iterdir()):
                if not q.is_file():
                    raise BudgetError(f"unexpected entry in {p.name}/: {q.name!r}")
                if p.name == "exec" and q.suffix == ".lock":
                    counts["exec"] += 1
                elif p.name == "tasks" and _SLOT_RE.match(q.name):
                    counts["tasks"] += 1
                elif p.name == "retries" and q.suffix == ".json":
                    counts["retries"] += 1
                else:
                    raise BudgetError(f"unexpected file in {p.name}/: {q.name!r}")
            continue
        if _SLOT_RE.match(p.name):
            if _read_json(p) is None:
                raise BudgetError(f"malformed reservation record: {p.name}")
            counts["slots"] += 1
        else:
            raise BudgetError(f"unexpected file in budget store: {p.name!r}")
    return counts


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
    ledger_root: pathlib.Path | str | None = None,
) -> dict[str, Any]:
    """Claim one execution ID, one reservation slot, and ``task_count`` task units."""
    budget = budget_of(record)
    if budget is None:
        raise BudgetError("not a budgeted (v2) authorization")
    if not execution_id:
        raise BudgetError("execution_id is required before reservation")
    try:
        task_count = int(task_count)
    except (TypeError, ValueError) as exc:
        raise BudgetError(f"task_count is malformed: {exc}") from exc
    if task_count < 1:
        raise BudgetError(f"task_count must be >= 1, got {task_count}")

    digest = record_digest(record)
    root = budget_root(ledger_root)
    gdir = _grant_dir(root, digest)
    gdir.mkdir(parents=True, exist_ok=True)

    # Fail closed on ANY unexpected entry before touching anything.
    audit(digest, root)

    for sub in ("exec", "tasks", "retries"):
        (gdir / sub).mkdir(exist_ok=True)

    # 1. execution-ID claim (atomic, cross-process)
    lock = gdir / "exec" / (hashlib.sha256(execution_id.encode()).hexdigest() + ".lock")
    try:
        _claim(lock, {"schema": RESERVATION_SCHEMA, "kind": "execution-claim",
                      "execution_id": execution_id,
                      "claimed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "pid": os.getpid()})
    except FileExistsError as exc:
        raise BudgetError(f"duplicate execution_id {execution_id!r} already reserved") from exc

    # 2. reservation slot
    seq = _allocate(gdir, budget["max_executions"],
                    {"schema": RESERVATION_SCHEMA, "record_digest": digest,
                     "stage": stage, "operation": operation,
                     "execution_id": execution_id, "binding": dict(binding),
                     "binding_digest": binding_digest,
                     "task_count": task_count,
                     "reserved_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "pid": os.getpid()},
                    "execution")

    # 3. task capacity (grant-wide, atomic per unit)
    for _ in range(task_count):
        _allocate(gdir / "tasks", budget["max_total_tasks"],
                  {"schema": RESERVATION_SCHEMA, "record_digest": digest,
                   "execution_id": execution_id}, "task")

    entry = _read_json(gdir / f"{seq:06d}.json")
    assert entry is not None
    entry["seq"] = seq
    return entry


def claim_retry(
    *,
    record: Mapping[str, Any],
    execution_id: str,
    task_id: str,
    ledger_root: pathlib.Path | str | None = None,
) -> str:
    """Claim one retry unit. Atomic; the key is (execution, task)."""
    budget = budget_of(record)
    if budget is None:
        raise BudgetError("not a budgeted (v2) authorization")
    if not execution_id or not task_id:
        raise BudgetError("execution_id and task_id are required for a retry claim")
    digest = record_digest(record)
    root = budget_root(ledger_root)
    gdir = _grant_dir(root, digest)
    (gdir / "retries").mkdir(parents=True, exist_ok=True)
    audit(digest, root)
    # One atomic slot IS the claim; the identity lives inside it. Duplicate
    # detection is a scan AFTER the claim, so a duplicate burns a slot
    # (fail-closed over-consumption) and can never under-count capacity.
    slot = _allocate(gdir / "retries", budget["max_retries"],
                     {"schema": RESERVATION_SCHEMA, "record_digest": digest,
                      "kind": "retry-claim", "execution_id": execution_id,
                      "task_id": task_id}, "retry")
    for p in sorted((gdir / "retries").iterdir()):
        if not p.is_file() or not _SLOT_RE.match(p.name):
            continue
        if p.name == f"{slot:06d}.json":
            continue
        prior = _read_json(p) or {}
        if (prior.get("execution_id") == execution_id
                and prior.get("task_id") == task_id):
            raise BudgetError(
                f"retry already claimed for {execution_id}/{task_id}"
            )
    return f"{slot:06d}.json"


def remaining(record: Mapping[str, Any], ledger_root: pathlib.Path | str | None = None) -> dict[str, int]:
    budget = budget_of(record)
    if budget is None:
        raise BudgetError("not a budgeted (v2) authorization")
    counts = audit(record_digest(record), ledger_root)
    return {
        "executions": budget["max_executions"] - counts["slots"],
        "tasks": budget["max_total_tasks"] - counts["tasks"],
        "retries": budget["max_retries"] - counts["retries"],
    }
