"""S8 production composition layer: canonical evaluator + evidence + governance.

Wires the session orchestration to the repository's *real* contracts:

    qwen_train.f2_evaluator.produce_report      (base/gold verdict from retained JUnit)
    qwen_train.f2_evidence.verify_task_evidence (S8 evidence verification)
    qwen_train.f2_governance.verify_governed_pair (governed pair verification)

Nothing here executes a task: the base and gold executions are produced by an
injected ``execute`` callable (the authorized runner), and the composition simply
verifies what that runner reported.  Every step fails closed -- if a canonical
verifier is unavailable or rejects, the session records evidence as missing or
invalid and never admits.

This module does not re-implement any evaluator, evidence schema, governance rule
or readiness predicate; it calls the canonical ones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train import f2_evaluator as evaluator
from qwen_train import f2_evidence as evidence
from qwen_train import f2_governance as governance
from qwen_train.f2_s8_factory import ExecutionResult
from qwen_train.f2_s8_runner import RunnerReceipt, verify_receipt
from qwen_train.f2_s8_session import (
    SessionError,
    TaskSpec,
    require_gates,
    run_session,
)

__all__ = [
    "canonical_report",
    "base_fails_gold_passes",
    "compose_execution_result",
    "build_canonical_verifiers",
    "produce_registered_task",
]


def canonical_report(
    *, junit_path: Path, spec: TaskSpec, out_path: Path
) -> dict[str, Any]:
    """Produce the canonical evaluator report for one retained JUnit artifact."""
    return evaluator.produce_report(
        junit_path=junit_path,
        fail_to_pass=list(spec.payload["fail_to_pass"]),
        pass_to_pass=list(spec.payload["pass_to_pass"]),
        out_path=out_path,
        instance_id=spec.task_id,
        repository=str(spec.payload["repo"]),
        base_commit=str(spec.payload["base_commit"]),
        execution_state_identity=spec.spec_digest,
    )


def base_fails_gold_passes(*, base_report: Mapping[str, Any], gold_report: Mapping[str, Any]) -> bool:
    """The S8 contract: base must NOT pass and gold MUST pass the declared nodes."""
    return base_report.get("declared_result") == "fail" and gold_report.get("declared_result") == "pass"


def compose_execution_result(
    *,
    spec: TaskSpec,
    base_receipt: RunnerReceipt | None,
    gold_receipt: RunnerReceipt | None,
    base_report: Mapping[str, Any] | None,
    gold_report: Mapping[str, Any] | None,
) -> ExecutionResult:
    """Map verified receipts and canonical reports onto one terminal outcome."""
    attempt = 1
    for label, receipt in (("base", base_receipt), ("gold", gold_receipt)):
        if receipt is None:
            return _infra(spec, attempt, "infrastructure_failure", f"{label} receipt missing")
        if receipt.payload.get("failure_class") == "timeout":
            return _infra(spec, attempt, "timeout", f"{label} timed out")
        if receipt.payload.get("failure_class"):
            return _infra(spec, attempt, "infrastructure_failure",
                          f"{label} reported {receipt.payload['failure_class']}")
    if base_report is None or gold_report is None:
        return _infra(spec, attempt, "infrastructure_failure", "evaluator report missing")

    if not base_fails_gold_passes(base_report=base_report, gold_report=gold_report):
        if base_report.get("declared_result") == "pass":
            outcome = "base_pass"
        elif gold_report.get("declared_result") == "fail":
            outcome = "gold_fail"
        else:
            outcome = "test_not_run"
        return _sci(spec, attempt, outcome, "S8 contract not satisfied")

    junit = (gold_receipt.payload.get("junit") or {}) if gold_receipt else {}
    return ExecutionResult(
        task_id=spec.task_id,
        outcome="base_fail_gold_pass",
        attempt=attempt,
        attempt_id=f"{spec.task_id}#{attempt}",
        disk_id=f"{spec.task_id}-diff",
        evidence_name=str(junit.get("name") or ""),
        evidence_sha256=str(junit.get("sha256") or ""),
        evidence_size=int(junit.get("size_bytes") or 0),
        evaluator=dict(spec.payload["evaluator"]),
    )


def _infra(spec: TaskSpec, attempt: int, outcome: str, reason: str) -> ExecutionResult:
    return ExecutionResult(
        task_id=spec.task_id, outcome=outcome, attempt=attempt,
        attempt_id=f"{spec.task_id}#{attempt}", reason=reason,
    )


def _sci(spec: TaskSpec, attempt: int, outcome: str, reason: str) -> ExecutionResult:
    return ExecutionResult(
        task_id=spec.task_id, outcome=outcome, attempt=attempt,
        attempt_id=f"{spec.task_id}#{attempt}", reason=reason,
    )


def build_canonical_verifiers(
    *,
    artifact_root: Path,
    authorized_evaluators: Mapping[str, Sequence[str]] | None,
    store: Any = None,
    registry: Any = None,
    base_bundle: Mapping[str, Any] | None = None,
    gold_bundle: Mapping[str, Any] | None = None,
) -> tuple[Callable[[TaskSpec, ExecutionResult], str],
           Callable[[TaskSpec, ExecutionResult], bool],
           Callable[[TaskSpec, ExecutionResult], bool]]:
    """Return the three verifiers ``run_session`` injects, bound to canonical code.

    ``verify_pair`` fails closed when the governed-pair inputs the canonical
    verifier requires (a trusted store, an evaluator registry and both execution
    bundles) are not supplied.
    """

    def evaluate(spec: TaskSpec, result: ExecutionResult) -> str:
        junit = Path(artifact_root) / str(spec.payload["evidence_dir"]) / result.evidence_name
        report = canonical_report(
            junit_path=junit, spec=spec,
            out_path=Path(artifact_root) / f"{spec.task_id}.report.json",
        )
        return str(report.get("declared_result") or "fail")

    def verify_evidence(spec: TaskSpec, result: ExecutionResult) -> bool:
        verification = evidence.verify_task_evidence(
            task_id=spec.task_id,
            repository=str(spec.payload["repo"]),
            base_commit=str(spec.payload["base_commit"]),
            base_evidence=None,
            gold_evidence=None,
            artifact_root=Path(artifact_root),
            authorized_evaluators=dict(authorized_evaluators or {}),
        )
        return bool(getattr(verification, "ok", False))

    def verify_pair(spec: TaskSpec, result: ExecutionResult) -> bool:
        if store is None or registry is None or base_bundle is None or gold_bundle is None:
            return False  # fail closed: governed-pair inputs are not available
        verification = governance.verify_governed_pair(
            base_bundle, gold_bundle, store=store, registry=registry,
            task_id=spec.task_id, repository=str(spec.payload["repo"]),
            base_commit=str(spec.payload["base_commit"]),
        )
        return bool(getattr(verification, "ok", False))

    return evaluate, verify_evidence, verify_pair


def produce_registered_task(
    *,
    spec: TaskSpec,
    gates: Mapping[str, Any],
    adapter: Any,
    execute: Callable[[TaskSpec, str], RunnerReceipt],
    ledger: Any,
    artifact_root: Path,
    authorized_evaluators: Mapping[str, Sequence[str]] | None = None,
    store: Any = None,
    registry: Any = None,
    base_bundle: Mapping[str, Any] | None = None,
    gold_bundle: Mapping[str, Any] | None = None,
    policy: Any = None,
    expected_registration_sha256: str = "",
    ts: str = "",
) -> Any:
    """Compose the full authorized pipeline.  Gates are checked first."""
    require_gates(gates)
    if expected_registration_sha256 and spec.registration_sha256 != expected_registration_sha256:
        raise SessionError("task spec does not belong to the pinned operative registration")

    base_receipt = execute(spec, "base")
    gold_receipt = execute(spec, "gold")
    for label, receipt in (("base", base_receipt), ("gold", gold_receipt)):
        ok, reasons = verify_receipt(receipt, spec)
        if not ok:
            raise SessionError(f"{label} receipt failed verification: {', '.join(reasons)}")

    base_report = gold_report = None
    if not base_receipt.payload.get("failure_class") and not gold_receipt.payload.get("failure_class"):
        base_report = canonical_report(
            junit_path=Path(artifact_root) / str(spec.payload["evidence_dir"])
            / str((base_receipt.payload.get("junit") or {}).get("name") or ""),
            spec=spec, out_path=Path(artifact_root) / f"{spec.task_id}.base.report.json",
        )
        gold_report = canonical_report(
            junit_path=Path(artifact_root) / str(spec.payload["evidence_dir"])
            / str((gold_receipt.payload.get("junit") or {}).get("name") or ""),
            spec=spec, out_path=Path(artifact_root) / f"{spec.task_id}.gold.report.json",
        )

    result = compose_execution_result(
        spec=spec, base_receipt=base_receipt, gold_receipt=gold_receipt,
        base_report=base_report, gold_report=gold_report,
    )
    evaluate, verify_evidence, verify_pair = build_canonical_verifiers(
        artifact_root=artifact_root, authorized_evaluators=authorized_evaluators,
        store=store, registry=registry, base_bundle=base_bundle, gold_bundle=gold_bundle,
    )
    return run_session(
        spec=spec, adapter=adapter, runner=lambda _spec: result, ledger=ledger,
        evaluate=evaluate, verify_evidence=verify_evidence, verify_pair=verify_pair,
        policy=policy, expected_registration_sha256=expected_registration_sha256, ts=ts,
    )
