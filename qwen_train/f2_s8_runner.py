"""S8 guest runner: validated task specification -> verifiable execution receipt.

Repository-side component.  The runner is **non-executing by default**: it holds
an injected ``transport`` (the eventual in-guest command executor) and refuses to
run without one.  Tests inject a synthetic transport, so no registered task, VM,
host process or evaluator is ever touched here.

It carries no credentials, reads no environment secret, and asserts no
contamination-free or guest-equivalence claim.  Output is captured with an
explicit size cap and published atomically; the receipt is content-addressed and
binds the task specification, the evaluator/procedure identity and the artifact
digests, so a malformed, stale or substituted result cannot pass verification.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from qwen_train.f2_s8_session import TaskSpec

__all__ = [
    "RUNNER_SCHEMA",
    "FAILURE_CLASSES",
    "RunnerError",
    "RunnerNotConfigured",
    "ReceiptInvalid",
    "RunOutcome",
    "RunnerReceipt",
    "GuestRunner",
    "verify_receipt",
    "publish_atomic",
]

RUNNER_SCHEMA = "f2_s8_runner_v1"

#: Task-attributable vs infrastructure failure classes.  Only the empty class
#: means "the command ran and produced an exit status".
FAILURE_CLASSES = ("", "timeout", "infrastructure_failure", "invalid_input", "output_truncated")
INFRASTRUCTURE_CLASSES = ("timeout", "infrastructure_failure", "invalid_input")


class RunnerError(Exception):
    """The runner contract was violated."""


class RunnerNotConfigured(RunnerError):
    """No transport is installed; the runner is non-executing by default."""


class ReceiptInvalid(RunnerError):
    """A receipt failed verification."""


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical(payload))


def publish_atomic(path: Path | str, data: bytes) -> str:
    """Write ``data`` so a partial write can never appear complete.

    Writes a sibling temp file, then ``os.replace`` (atomic on the same volume).
    Returns the SHA-256 of the published bytes.
    """
    import os

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return _sha256_bytes(data)


@dataclass(frozen=True)
class RunOutcome:
    """Raw facts as reported by the injected transport."""

    exit_status: int
    stdout: bytes = b""
    stderr: bytes = b""
    failure_class: str = ""
    detail: str = ""
    started_at: str = ""
    finished_at: str = ""


@dataclass(frozen=True)
class RunnerReceipt:
    payload: Mapping[str, Any]

    @property
    def task_id(self) -> str:
        return str(self.payload["task_id"])

    @property
    def infrastructure(self) -> bool:
        return self.payload["failure_class"] in INFRASTRUCTURE_CLASSES

    @property
    def receipt_digest(self) -> str:
        return str(self.payload["receipt_digest"])

    def compute_digest(self) -> str:
        return _sha256_payload({k: v for k, v in self.payload.items() if k != "receipt_digest"})

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class GuestRunner:
    """Validated, bounded, non-executing-by-default task runner."""

    def __init__(
        self,
        *,
        transport: Callable[[TaskSpec, int], RunOutcome] | None = None,
        output_root: Path | str = ".",
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.transport = transport
        self.output_root = Path(output_root)
        self.clock = clock or (lambda: "")

    def run(self, spec: TaskSpec, *, attempt: int = 1) -> RunnerReceipt:
        if self.transport is None:
            raise RunnerNotConfigured("no transport installed; the runner does not execute")
        if attempt < 1:
            raise RunnerError("attempt must be >= 1")
        _validate_spec(spec)

        outcome = self.transport(spec, attempt)
        if not isinstance(outcome, RunOutcome):
            raise RunnerError("transport must return a RunOutcome")
        if outcome.failure_class not in FAILURE_CLASSES:
            raise RunnerError(f"unknown failure_class {outcome.failure_class!r}")

        cap = int(spec.payload["max_output_bytes"])
        stdout = outcome.stdout or b""
        stderr = outcome.stderr or b""
        out_trunc = len(stdout) > cap
        err_trunc = len(stderr) > cap
        stdout = stdout[:cap]
        stderr = stderr[:cap]

        junit_name = f"{spec.task_id}.junit.xml"
        junit_dir = self.output_root / str(spec.payload["evidence_dir"])
        # The transport may publish JUnit itself; the runner records what exists.
        junit_path = junit_dir / junit_name
        junit_exists = junit_path.is_file()
        junit_bytes = junit_path.read_bytes() if junit_exists else b""

        failure_class = outcome.failure_class
        if not failure_class and not junit_exists:
            failure_class = "infrastructure_failure"
        if out_trunc or err_trunc:
            failure_class = failure_class or "output_truncated"

        body: dict[str, Any] = {
            "runner_schema": RUNNER_SCHEMA,
            "task_id": spec.task_id,
            "registration_id": spec.payload["registration_id"],
            "registration_sha256": spec.registration_sha256,
            "spec_digest": spec.spec_digest,
            "attempt": attempt,
            "evaluator": dict(spec.payload["evaluator"]),
            "exit_status": int(outcome.exit_status),
            "failure_class": failure_class,
            "infrastructure": failure_class in INFRASTRUCTURE_CLASSES or failure_class == "output_truncated",
            "stdout_sha256": _sha256_bytes(stdout),
            "stderr_sha256": _sha256_bytes(stderr),
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "output_truncated": bool(out_trunc or err_trunc),
            "junit": (
                {"name": junit_name, "sha256": _sha256_bytes(junit_bytes), "size_bytes": len(junit_bytes)}
                if junit_exists else None
            ),
            "started_at": outcome.started_at or self.clock(),
            "finished_at": outcome.finished_at or self.clock(),
            "detail": outcome.detail,
            # Declared, never inferred.
            "claims": {"contamination_free": False, "guest_equivalent_to_arm_environment": False},
        }
        body["receipt_digest"] = _sha256_payload(body)
        return RunnerReceipt(payload=body)


def _validate_spec(spec: Any) -> None:
    if not isinstance(spec, TaskSpec):
        raise RunnerError("a TaskSpec is required")
    payload = spec.payload
    if len(str(payload.get("spec_digest") or "")) != 64:
        raise RunnerError("task specification is not content-addressed")
    for key in ("task_id", "registration_id", "evidence_dir"):
        if not str(payload.get(key) or "").strip():
            raise RunnerError(f"task specification is missing {key}")
    if not spec.registration_sha256:
        raise RunnerError("task specification is missing the registration digest")
    if int(payload.get("timeout_seconds") or 0) < 1:
        raise RunnerError("task specification has no positive timeout")


def verify_receipt(
    receipt: RunnerReceipt | Mapping[str, Any],
    spec: TaskSpec,
    *,
    min_junit_bytes: int = 1,
) -> tuple[bool, list[str]]:
    """Bind a receipt to its specification.  Returns ``(ok, reasons)``."""
    payload = receipt.to_dict() if isinstance(receipt, RunnerReceipt) else dict(receipt or {})
    reasons: list[str] = []
    if payload.get("runner_schema") != RUNNER_SCHEMA:
        reasons.append("wrong runner schema")
    if payload.get("task_id") != spec.task_id:
        reasons.append("task identity mismatch")
    if payload.get("spec_digest") != spec.spec_digest:
        reasons.append("spec digest mismatch")
    if payload.get("registration_sha256") != spec.registration_sha256:
        reasons.append("registration digest mismatch")
    if payload.get("evaluator") != dict(spec.payload["evaluator"]):
        reasons.append("evaluator identity mismatch")
    body = {k: v for k, v in payload.items() if k != "receipt_digest"}
    if payload.get("receipt_digest") != _sha256_payload(body):
        reasons.append("receipt digest does not match its content")
    if payload.get("failure_class") not in FAILURE_CLASSES:
        reasons.append("unknown failure class")
    junit = payload.get("junit")
    if junit is None:
        reasons.append("no JUnit artifact recorded")
    else:
        if not str(junit.get("name") or "").endswith(".junit.xml"):
            reasons.append("unexpected artifact name")
        size = int(junit.get("size_bytes") or 0)
        if size < min_junit_bytes:
            reasons.append("JUnit artifact is empty")
        if len(str(junit.get("sha256") or "")) != 64:
            reasons.append("JUnit artifact has no digest")
    if payload.get("failure_class") and payload.get("failure_class") != "output_truncated":
        reasons.append("execution reported a failure class")
    return (not reasons), reasons
