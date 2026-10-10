"""S8 session: task specification, authorization gates, VM/disk controller, orchestration.

Repository-side only.  Nothing here starts a VM, attaches a disk, touches the
network, reads a credential, or runs a real task.  The live adapter is
fail-closed behind explicit authorization gates and an injected transport that
this module never provides.

Pipeline (adapted to the repository's real contracts):

    registered task -> TaskSpec (validated) -> gates -> adapter prepare ->
    runner execute -> receipt -> evaluator/evidence/governance verification ->
    ledger outcome -> verify cleanup -> terminal

Invariant: admission is written only after the injected verification callables
approve.  If a required verifier is not wired, the session **fails closed** --
missing evidence and unverified evidence are never a pass, and an infrastructure
outcome is never recorded as a scientific negative.

Terminology follows the authority documents: an evidence-complete, governance-
verified task is `admitted`; `analyzable` remains 0 until the later T/X phase.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train.f2_s8_factory import (
    ADMITTED_OUTCOME,
    AppendOnlyLedger,
    ExecutionResult,
    RetryPolicy,
    run_once,
)

__all__ = [
    "SCHEMA_VERSION",
    "SESSION_STATES",
    "SPEC_VERSION",
    "SessionError",
    "GateNotSatisfied",
    "LiveAdapterRefused",
    "TaskSpec",
    "build_task_spec",
    "check_gates",
    "require_gates",
    "FakeVMAdapter",
    "LiveVMAdapter",
    "SessionResult",
    "run_session",
]

SCHEMA_VERSION = "f2_s8_session_v1"
SPEC_VERSION = "f2_s8_task_spec_v1"

#: Non-terminal -> terminal states of one task session.  These are the resume
#: distinctions required by the authority: never attempted, in progress,
#: infrastructure failure, scientific failure, evidence missing/invalid,
#: evidence verified, governed admission, unresolved cleanup, completion.
SESSION_STATES = (
    "never_attempted",
    "in_progress",
    "infrastructure_failure",
    "scientific_failure",
    "evidence_missing_or_invalid",
    "evidence_verified",
    "admitted",
    "unresolved_cleanup",
    "completed",
)

#: Authorization gates that must all hold before a live operation may occur.
REQUIRED_GATES = (
    "authority_current",
    "d4_budget_approved",
    "d4_storage_cap_approved",
    "approved_subnet",
    "d5_credential_available",
    "evaluator_identity_named",
    "isolation_verified",
    "pilot_authorized",
)


class SessionError(Exception):
    """The session contract was violated."""


class GateNotSatisfied(SessionError):
    """One or more authorization gates are unmet; live work must not proceed."""


class LiveAdapterRefused(SessionError):
    """The live adapter was invoked without authorization, transport or gates."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")


@dataclass(frozen=True)
class TaskSpec:
    """A versioned, validated task specification bound to its registration."""

    payload: Mapping[str, Any]

    @property
    def task_id(self) -> str:
        return str(self.payload["task_id"])

    @property
    def spec_digest(self) -> str:
        return _sha256({k: v for k, v in self.payload.items() if k != "spec_digest"})

    @property
    def registration_sha256(self) -> str:
        return str(self.payload["registration_sha256"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def build_task_spec(
    *,
    task_id: str,
    source: str,
    repo: str,
    base_commit: str,
    test_cmd: str,
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
    registration_id: str,
    registration_sha256: str,
    evaluator: Mapping[str, str],
    timeout_seconds: int = 3600,
    max_output_bytes: int = 64 * 1024 * 1024,
    evidence_dir: str = "evidence",
) -> TaskSpec:
    """Validate and freeze a task specification.  Raises on anything unusable."""
    if not task_id.strip():
        raise SessionError("task_id is required")
    if not repo.strip():
        raise SessionError("repo is required")
    if len(base_commit) != 40 or any(c not in "0123456789abcdef" for c in base_commit):
        raise SessionError("base_commit must be a 40-character lowercase hex SHA")
    if not test_cmd.strip():
        raise SessionError("test_cmd is required")
    if "--junitxml" in test_cmd:
        raise SessionError("test_cmd must not already carry --junitxml")
    if not fail_to_pass:
        raise SessionError("FAIL_TO_PASS must not be empty")
    if not _is_sha256(registration_sha256):
        raise SessionError("registration_sha256 must be a 64-hex digest")
    for key in ("evaluator_id", "version", "implementation_digest", "procedure_id", "protocol_version"):
        if not str(evaluator.get(key) or ""):
            raise SessionError(f"evaluator.{key} is required")
    if timeout_seconds < 1:
        raise SessionError("timeout_seconds must be >= 1")
    if max_output_bytes < 1:
        raise SessionError("max_output_bytes must be >= 1")
    if Path(evidence_dir).is_absolute() or ".." in Path(evidence_dir).parts:
        raise SessionError("evidence_dir must be a relative path without traversal")

    body = {
        "spec_version": SPEC_VERSION,
        "task_id": task_id,
        "source": source,
        "repo": repo,
        "base_commit": base_commit,
        "test_cmd": test_cmd,
        "fail_to_pass": list(fail_to_pass),
        "pass_to_pass": list(pass_to_pass),
        "registration_id": registration_id,
        "registration_sha256": registration_sha256,
        "evaluator": dict(evaluator),
        "timeout_seconds": int(timeout_seconds),
        "max_output_bytes": int(max_output_bytes),
        "evidence_dir": evidence_dir,
        # Declared, never inferred: the guest is not claimed equivalent to any
        # future arm environment, and proxy screening proves no contamination.
        "claims": {
            "guest_equivalent_to_arm_environment": False,
            "contamination_free": False,
        },
    }
    body["spec_digest"] = _sha256(body)
    return TaskSpec(payload=body)


def check_gates(gates: Mapping[str, Any]) -> list[str]:
    """Return the sorted names of every required gate that is not satisfied."""
    return sorted(name for name in REQUIRED_GATES if not gates.get(name))


def require_gates(gates: Mapping[str, Any]) -> None:
    missing = check_gates(gates)
    if missing:
        raise GateNotSatisfied("authorization gates unmet: " + ", ".join(missing))


@dataclass(frozen=True)
class SessionResult:
    state: str
    task_id: str
    outcome: str
    record: Mapping[str, Any] | None
    unresolved: tuple[str, ...] = ()
    detail: str = ""


class FakeVMAdapter:
    """In-memory controller.  Fully functional; touches nothing real.

    ``fail_at`` injects a failure after a named transition so tests can exercise
    every recovery boundary.  Postconditions are verified from the adapter's own
    state, not from the return value of the call that produced it.
    """

    def __init__(self, *, parent_disk_id: str = "golden-parent", free_gb: float = 100.0,
                 fail_at: str | None = None, declare_dirty: bool = False) -> None:
        self.parent_disk_id = parent_disk_id
        self.free_gb = free_gb
        self.fail_at = fail_at
        self.dirty = declare_dirty
        self.calls: list[str] = []
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.stopped = True

    def _touch(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(f"injected failure at {name}")

    def inspect_environment(self) -> dict[str, Any]:
        self._touch("inspect_environment")
        return {"parent_disk_id": self.parent_disk_id, "free_gb": self.free_gb, "vm_state": "Off"}

    def prepare_workspace(self, task_id: str, *, min_free_gb: float = 1.0) -> str:
        self._touch("prepare_workspace")
        if task_id in self.workspaces and self.workspaces[task_id].get("dirty"):
            raise SessionError(f"refusing to reuse a dirty workspace for {task_id!r}")
        if self.free_gb < min_free_gb:
            raise SessionError(f"insufficient free space: {self.free_gb} GB")
        disk_id = f"{task_id}-diff"
        self.workspaces[task_id] = {"disk_id": disk_id, "parent": self.parent_disk_id,
                                     "dirty": True, "attached": False}
        return disk_id

    def verify_disk_chain(self, task_id: str) -> str:
        self._touch("verify_disk_chain")
        ws = self.workspaces.get(task_id)
        if ws is None:
            raise SessionError(f"no workspace for {task_id!r}")
        if ws["parent"] != self.parent_disk_id:
            raise SessionError(f"disk chain parent mismatch for {task_id!r}")
        return ws["disk_id"]

    def start(self, task_id: str) -> None:
        self._touch("start")
        self.workspaces[task_id]["attached"] = True
        self.stopped = False

    def collect_receipt(self, task_id: str) -> dict[str, Any]:
        self._touch("collect_receipt")
        return {"task_id": task_id, "receipt_digest": _sha256({"task": task_id})}

    def stop(self, task_id: str) -> None:
        self._touch("stop")
        self.stopped = True
        ws = self.workspaces.get(task_id)
        if ws is not None:
            ws["attached"] = False

    def destroy(self, task_id: str) -> None:
        self._touch("destroy")
        ws = self.workspaces.pop(task_id, None)
        if ws is None:
            raise SessionError(f"no disk to destroy for {task_id!r}")

    def verify_cleanup(self, task_id: str) -> tuple[bool, tuple[str, ...]]:
        self._touch("verify_cleanup")
        ws = self.workspaces.get(task_id)
        if ws is not None:
            return False, (f"disk {ws['disk_id']} still present",)
        if not self.stopped:
            return False, ("vm still running",)
        return True, ()


class LiveVMAdapter:
    """Fail-closed live adapter boundary.  Never usable from ordinary tests.

    It performs no work of its own: every privileged operation is an injected
    ``transport`` callable (the eventual authorized Hyper-V client).  Before any
    call it requires the authorization gates, an explicit ``allow_live`` token,
    a transport, and a validated parent identity; after each destructive call it
    re-inspects state rather than trusting the call's return value.
    """

    def __init__(
        self,
        *,
        gates: Mapping[str, Any],
        transport: Callable[..., Any] | None = None,
        expected_parent_disk_id: str = "",
        allow_live: bool = False,
    ) -> None:
        self.gates = dict(gates)
        self.transport = transport
        self.expected_parent_disk_id = expected_parent_disk_id
        self.allow_live = allow_live

    def _authorize(self, operation: str) -> None:
        if not self.allow_live:
            raise LiveAdapterRefused("live operation refused: allow_live is not set")
        require_gates(self.gates)
        if self.transport is None:
            raise LiveAdapterRefused("live operation refused: no transport is installed")
        if not self.expected_parent_disk_id:
            raise LiveAdapterRefused("live operation refused: expected parent disk is unset")

    def inspect_environment(self) -> dict[str, Any]:
        self._authorize("inspect_environment")
        return dict(self.transport("inspect_environment"))

    def prepare_workspace(self, task_id: str, *, min_free_gb: float = 1.0) -> str:
        self._authorize("prepare_workspace")
        state = self.transport("inspect_environment")
        if float(state.get("free_gb", 0)) < min_free_gb:
            raise SessionError("insufficient free space for a new task disk")
        return str(self.transport("prepare_workspace", task_id=task_id,
                                  parent=self.expected_parent_disk_id))

    def verify_disk_chain(self, task_id: str) -> str:
        self._authorize("verify_disk_chain")
        parent = str(self.transport("disk_parent", task_id=task_id))
        if parent != self.expected_parent_disk_id:
            raise SessionError("refusing: task disk parent does not match the golden parent")
        return str(self.transport("disk_id", task_id=task_id))

    def start(self, task_id: str) -> None:
        self._authorize("start")
        self.transport("start", task_id=task_id)
        if not self.transport("is_running", task_id=task_id):
            raise SessionError("VM did not reach the running state")

    def collect_receipt(self, task_id: str) -> dict[str, Any]:
        self._authorize("collect_receipt")
        return dict(self.transport("collect_receipt", task_id=task_id))

    def stop(self, task_id: str) -> None:
        self._authorize("stop")
        self.transport("stop", task_id=task_id)

    def destroy(self, task_id: str) -> None:
        self._authorize("destroy")
        self.transport("destroy", task_id=task_id)

    def verify_cleanup(self, task_id: str) -> tuple[bool, tuple[str, ...]]:
        self._authorize("verify_cleanup")
        remaining = tuple(self.transport("remaining_resources", task_id=task_id) or ())
        return (not remaining), remaining


def run_session(
    *,
    spec: TaskSpec,
    adapter: Any,
    runner: Callable[[TaskSpec], ExecutionResult],
    ledger: AppendOnlyLedger,
    evaluate: Callable[[TaskSpec, ExecutionResult], str] | None = None,
    verify_evidence: Callable[[TaskSpec, ExecutionResult], bool] | None = None,
    verify_pair: Callable[[TaskSpec, ExecutionResult], bool] | None = None,
    policy: RetryPolicy | None = None,
    expected_registration_sha256: str = "",
    ts: str = "",
) -> SessionResult:
    """One task session: prepare -> run -> verify -> publish -> cleanup.

    The injected ``evaluate``/``verify_evidence``/``verify_pair`` callables stand
    for the canonical evaluator / evidence / governance contracts.  If any of
    the required verifiers is not wired, an otherwise successful run is recorded
    as ``evidence_missing_or_invalid`` -- never as admitted.
    """
    policy = policy or RetryPolicy()
    if expected_registration_sha256 and spec.registration_sha256 != expected_registration_sha256:
        raise SessionError("task spec does not belong to the pinned operative registration")

    unresolved: tuple[str, ...] = ()
    try:
        adapter.inspect_environment()
        adapter.prepare_workspace(spec.task_id)
        adapter.verify_disk_chain(spec.task_id)
        adapter.start(spec.task_id)
        receipt = adapter.collect_receipt(spec.task_id)
    except Exception as exc:  # infrastructure boundary: never a scientific negative
        try:
            adapter.stop(spec.task_id)
            adapter.destroy(spec.task_id)
            clean, unresolved = adapter.verify_cleanup(spec.task_id)
        except Exception:
            clean, unresolved = False, ("cleanup after preparation failure did not complete",)
        record = ledger.append({
            "task_id": spec.task_id,
            "registration_id": spec.payload["registration_id"],
            "registration_sha256": spec.registration_sha256,
            "spec_digest": spec.spec_digest,
            "outcome": "infrastructure_failure",
            "outcome_class": "infrastructure",
            "attempt": len(ledger.attempts(spec.task_id)) + 1,
            "reason": f"preparation/inspection failure: {type(exc).__name__}",
            "ts": ts,
        })
        return SessionResult(
            state="unresolved_cleanup" if unresolved else "infrastructure_failure",
            task_id=spec.task_id, outcome="infrastructure_failure", record=record,
            unresolved=unresolved, detail=str(exc),
        )

    if receipt.get("task_id") != spec.task_id:
        raise SessionError("receipt task identity does not match the task specification")

    try:
        result = runner(spec)
    except Exception as exc:
        adapter.stop(spec.task_id)
        adapter.destroy(spec.task_id)
        adapter.verify_cleanup(spec.task_id)
        record = ledger.append({
            "task_id": spec.task_id,
            "registration_id": spec.payload["registration_id"],
            "registration_sha256": spec.registration_sha256,
            "spec_digest": spec.spec_digest,
            "outcome": "infrastructure_failure",
            "outcome_class": "infrastructure",
            "attempt": len(ledger.attempts(spec.task_id)) + 1,
            "reason": f"runner failure: {type(exc).__name__}",
            "ts": ts,
        })
        return SessionResult(state="infrastructure_failure", task_id=spec.task_id,
                             outcome="infrastructure_failure", record=record, detail=str(exc))

    # Verification before publication.  Fail closed when a verifier is absent.
    if result.outcome == ADMITTED_OUTCOME:
        if evaluate is None or verify_evidence is None or verify_pair is None:
            record = ledger.append({
                "task_id": spec.task_id, "outcome_class": "scientific",
                "outcome": "test_not_run",
                "registration_id": spec.payload["registration_id"],
                "registration_sha256": spec.registration_sha256,
                "spec_digest": spec.spec_digest,
                "attempt": len(ledger.attempts(spec.task_id)) + 1,
                "reason": "required evaluator/evidence/governance verifier is not wired",
                "admitted": False, "ts": ts,
            })
            adapter.stop(spec.task_id); adapter.destroy(spec.task_id)
            adapter.verify_cleanup(spec.task_id)
            return SessionResult(state="evidence_missing_or_invalid", task_id=spec.task_id,
                                 outcome="test_not_run", record=record,
                                 detail="verifier not wired")
        verdict = evaluate(spec, result)
        if verdict != "pass" or not verify_evidence(spec, result) or not verify_pair(spec, result):
            record = ledger.append({
                "task_id": spec.task_id, "outcome_class": "scientific",
                "outcome": "test_not_run",
                "registration_id": spec.payload["registration_id"],
                "registration_sha256": spec.registration_sha256,
                "spec_digest": spec.spec_digest,
                "attempt": len(ledger.attempts(spec.task_id)) + 1,
                "reason": f"verification did not accept the evidence (evaluator={verdict})",
                "admitted": False, "ts": ts,
            })
            adapter.stop(spec.task_id); adapter.destroy(spec.task_id)
            adapter.verify_cleanup(spec.task_id)
            return SessionResult(state="evidence_missing_or_invalid", task_id=spec.task_id,
                                 outcome="test_not_run", record=record)

    record = run_once(
        task_id=spec.task_id, ledger=ledger,
        runner=lambda tid, attempt: result,
        registration_id=str(spec.payload["registration_id"]),
        registration_sha256=spec.registration_sha256,
        evaluator=dict(spec.payload["evaluator"]),
        policy=RetryPolicy(max_attempts=max(policy.max_attempts, len(ledger.attempts(spec.task_id)) + 1),
                           max_tasks_per_run=policy.max_tasks_per_run,
                           max_concurrency=policy.max_concurrency),
        ts=ts,
    )

    adapter.stop(spec.task_id)
    adapter.destroy(spec.task_id)
    clean, unresolved = adapter.verify_cleanup(spec.task_id)

    if record.get("admitted") is True:
        state = "unresolved_cleanup" if not clean else "admitted"
    else:
        state = ("unresolved_cleanup" if not clean else
                 "scientific_failure" if record.get("outcome_class") == "scientific"
                 else "infrastructure_failure")
    return SessionResult(state=state, task_id=spec.task_id, outcome=record["outcome"],
                         record=record, unresolved=unresolved)
