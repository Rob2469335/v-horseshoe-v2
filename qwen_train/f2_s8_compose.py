"""S8 production composition root: assemble governed bundles from real execution.

Why this module exists
----------------------
``f2_s8_producer`` wires the session to the *canonical* evaluator, evidence and
governance modules, but its ``verify_evidence``/``verify_pair`` callables were
fed ``base_evidence=None``/``gold_evidence=None`` and no trusted store, registry
or bundles.  As written, admission was therefore *structurally impossible*: every
task failed closed at ``PROVENANCE_NOT_ESTABLISHED`` regardless of what executed.

This module supplies the missing production step.  Given the retained output of
one base/gold execution (the authoritative evaluator report and the run log), it:

1. retains those bytes under the operator-provisioned **trusted artifact store**;
2. derives each side's immutable ``ExecutionBundle`` (identity + artifact refs +
   declared result) with :func:`qwen_train.f2_governance.build_execution_identity`;
3. binds ``run_session``'s three verifiers to the real
   :func:`qwen_train.f2_evidence.verify_task_evidence` and
   :func:`qwen_train.f2_governance.verify_governed_pair`.

Nothing here invents scientific facts.  The evaluator identity/version/procedure,
the trusted store root and retention, the environment identity, and the opaque
base/gold state digests are all **operator-supplied**; when any is absent the
loader fails closed and names exactly what is missing (mirroring the established
``f2_arm_worker._load_bundle_governance`` pattern).  The declared result is never
trusted: the governance verifier re-derives it from the retained report.

This is the production counterpart to the synthetic in-test composition used by
``tests/test_f2_s8_producer.py``.  It performs no execution itself; the guest
run is an injected ``execute`` seam (:class:`GuestEvidence`), so this module has
no subprocess, no Hyper-V, no network and no credential.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from qwen_train import f2_evaluator as evaluator
from qwen_train import f2_evidence as evidence
from qwen_train import f2_governance as governance
from qwen_train.f2_s8_factory import ADMITTED_OUTCOME, ExecutionResult
from qwen_train.f2_s8_runner import publish_atomic
from qwen_train.f2_s8_session import (
    SessionError,
    TaskSpec,
    require_gates,
    run_session,
)

__all__ = [
    "S8ComposeError",
    "GovernanceInputs",
    "GuestEvidence",
    "S8_GOVERNANCE_ENV",
    "load_governance_from_env",
    "retain_artifact",
    "guest_evidence_from_junit",
    "assemble_governed_bundle",
    "build_governed_verifiers",
    "produce_governed_task",
]

ROLE_TEST_OUTPUT = evidence.ROLE_TEST_OUTPUT
ROLE_RUN_LOG = evidence.ROLE_RUN_LOG
ROLE_EVALUATOR_IMPLEMENTATION = governance.ROLE_EVALUATOR_IMPLEMENTATION

#: The exact operator-supplied inputs the governed path requires.  Absence of
#: any one is a fail-closed condition naming what must be supplied.
S8_GOVERNANCE_ENV: tuple[tuple[str, str], ...] = (
    ("SWARM_S8_ARTIFACT_ROOT", "trusted artifact store root (absolute path)"),
    ("SWARM_S8_ARTIFACT_RETENTION_DAYS", "artifact retention in days (integer)"),
    ("SWARM_S8_EVALUATOR_ID", "authorized evaluator identity"),
    ("SWARM_S8_EVALUATOR_VERSION", "authorized evaluator version"),
    ("SWARM_S8_EVALUATOR_IMPL", "evaluator implementation artifact path"),
    ("SWARM_S8_EVALUATOR_PROCEDURE", "authorized evaluator procedure identity"),
    ("SWARM_S8_ENVIRONMENT_IDENTITY", "execution environment identity"),
    ("SWARM_S8_BASE_STATE_DIGEST", "opaque digest of the base execution state"),
    ("SWARM_S8_GOLD_STATE_DIGEST", "opaque digest of the gold execution state"),
)


class S8ComposeError(SessionError):
    """The production composition contract was violated."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    s = str(value or "")
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


@dataclass(frozen=True)
class GovernanceInputs:
    """Operator-supplied governance facts.  None is derived, defaulted or faked."""

    store: governance.TrustedArtifactStore
    registry: governance.EvaluatorRegistry
    evaluator: governance.EvaluatorAuthorization
    environment_identity: str
    base_state_digest: str
    gold_state_digest: str
    implementation_name: str = ""
    implementation_bytes: bytes = b""

    def __post_init__(self) -> None:
        if not str(self.environment_identity or "").strip():
            raise S8ComposeError("environment_identity is required")
        for label, value in (
            ("base_state_digest", self.base_state_digest),
            ("gold_state_digest", self.gold_state_digest),
        ):
            if not _is_sha256(value):
                raise S8ComposeError(f"{label} must be a 64-hex SHA-256 digest")
        if self.base_state_digest == self.gold_state_digest:
            raise S8ComposeError(
                "base and gold state digests are identical; they cannot describe "
                "two distinct execution states"
            )
        if bool(self.implementation_name) != bool(self.implementation_bytes):
            raise S8ComposeError(
                "implementation_name and implementation_bytes must be supplied together"
            )
        if self.implementation_bytes:
            digest = _sha256(self.implementation_bytes)
            if digest != self.evaluator.implementation_digest:
                raise S8ComposeError(
                    "the retained evaluator implementation bytes do not hash to the "
                    "authorized implementation_digest"
                )

    def state_digest(self, side: str) -> str:
        return self.base_state_digest if side == "base" else self.gold_state_digest


def load_governance_from_env(env: Mapping[str, str] | None = None) -> GovernanceInputs:
    """Build the governance inputs from environment variables, fail-closed.

    Any missing required variable raises and names the operator-supplied value
    that must be provided.  Nothing is invented or defaulted.
    """
    source = os.environ if env is None else env
    missing = [
        f"{name} ({label})" for name, label in S8_GOVERNANCE_ENV
        if not str(source.get(name, "")).strip()
    ]
    if missing:
        raise S8ComposeError(
            "S8 fail-closed: governance inputs are missing: " + "; ".join(missing)
            + ". The governed path needs an operator-provisioned trusted store and "
            "evaluator authorization; none is fabricated."
        )

    root = Path(source["SWARM_S8_ARTIFACT_ROOT"])
    if not root.is_absolute():
        raise S8ComposeError("SWARM_S8_ARTIFACT_ROOT must be an absolute path")
    try:
        retention_days = int(source["SWARM_S8_ARTIFACT_RETENTION_DAYS"])
    except ValueError as exc:
        raise S8ComposeError(
            "SWARM_S8_ARTIFACT_RETENTION_DAYS must be an integer"
        ) from exc

    impl_path = Path(source["SWARM_S8_EVALUATOR_IMPL"])
    if not impl_path.is_file():
        raise S8ComposeError(
            f"evaluator implementation artifact not found: {impl_path}"
        )
    implementation_bytes = impl_path.read_bytes()

    auth = governance.EvaluatorAuthorization(
        evaluator_id=source["SWARM_S8_EVALUATOR_ID"],
        version=source["SWARM_S8_EVALUATOR_VERSION"],
        implementation_digest=_sha256(implementation_bytes),
        procedure_id=source["SWARM_S8_EVALUATOR_PROCEDURE"],
        protocol_version=evaluator.PROTOCOL_VERSION,
    )
    store = governance.TrustedArtifactStore(
        root, governance.RetentionPolicy("f2_s8_retention", retention_days, True)
    )
    return GovernanceInputs(
        store=store,
        registry=governance.EvaluatorRegistry([auth]),
        evaluator=auth,
        environment_identity=source["SWARM_S8_ENVIRONMENT_IDENTITY"],
        base_state_digest=source["SWARM_S8_BASE_STATE_DIGEST"],
        gold_state_digest=source["SWARM_S8_GOLD_STATE_DIGEST"],
        implementation_name=impl_path.name,
        implementation_bytes=implementation_bytes,
    )


@dataclass(frozen=True)
class GuestEvidence:
    """The retained output of one guest-side base/gold execution.

    ``report`` is the authoritative evaluator report bytes; ``run_log`` is the
    retained run log.  A non-empty ``failure_class`` means the guest reported an
    infrastructure failure and the side is not assemblable.
    """

    report: bytes
    run_log: bytes
    started_at: str
    finished_at: str
    failure_class: str = ""


def guest_evidence_from_junit(
    *,
    spec: TaskSpec,
    side: str,
    junit_bytes: bytes,
    run_log: bytes,
    started_at: str = "",
    finished_at: str = "",
    work_dir: Path | str | None = None,
) -> GuestEvidence:
    """Run the authoritative evaluator over retained guest JUnit bytes.

    This is the host-side step that turns the raw JUnit artifact returned over
    the disk channel into the evaluator report the governed composition requires.
    It refuses to invent a result: :func:`qwen_train.f2_evaluator.produce_report`
    derives the verdict from the JUnit and the declared test contract, and any
    unparseable evidence raises rather than becoming a pass.
    """
    import tempfile

    if side not in ("base", "gold"):
        raise S8ComposeError(f"unknown side {side!r}")
    if not junit_bytes:
        raise S8ComposeError(f"{side} JUnit evidence is empty; cannot evaluate")
    with tempfile.TemporaryDirectory(dir=work_dir) as td:
        junit_path = Path(td) / f"{spec.task_id}.{side}.junit.xml"
        junit_path.write_bytes(junit_bytes)
        out_path = Path(td) / f"{spec.task_id}.{side}.report.json"
        try:
            evaluator.produce_report(
                junit_path=junit_path,
                fail_to_pass=list(spec.payload["fail_to_pass"]),
                pass_to_pass=list(spec.payload["pass_to_pass"]),
                out_path=out_path,
                instance_id=spec.task_id,
                repository=str(spec.payload["repo"]),
                base_commit=str(spec.payload["base_commit"]),
                execution_state_identity=side,
            )
        except evaluator.EvaluatorError as exc:
            raise S8ComposeError(
                f"{side} JUnit evidence could not be evaluated: {exc}"
            ) from exc
        report = out_path.read_bytes()
    return GuestEvidence(
        report=report, run_log=run_log, started_at=started_at, finished_at=finished_at
    )


def retain_artifact(store_root: Path | str, name: str, data: bytes) -> str:
    """Write ``data`` under the trusted root atomically; return its SHA-256.

    The name must be a safe relative path (no traversal, no absolute path): the
    trusted store re-validates containment when it reads the artifact back.
    """
    root = Path(store_root)
    candidate = Path(name)
    if not name or candidate.is_absolute() or candidate.drive or name[0] in ("/", "\\"):
        raise S8ComposeError(f"unsafe artifact name {name!r}")
    if any(part == ".." for part in candidate.parts):
        raise S8ComposeError(f"artifact name {name!r} contains a parent component")
    return publish_atomic(root / candidate, data)


def _artifact_ref(name: str, data: bytes, role: str) -> evidence.ArtifactRef:
    return evidence.ArtifactRef(
        name=name, digest=_sha256(data), size_bytes=len(data), role=role
    )


def assemble_governed_bundle(
    *,
    spec: TaskSpec,
    side: str,
    guest: GuestEvidence,
    inputs: GovernanceInputs,
) -> governance.ExecutionBundle:
    """Build one side's immutable execution bundle from retained guest output.

    Fails closed when the guest declared an infrastructure failure, when the
    report is not an authentic evaluator report, when it cannot establish a
    result, or when it does not describe this task/state.
    """
    if side not in governance.VALID_STATES:
        raise S8ComposeError(f"unknown side {side!r}; expected {governance.VALID_STATES}")
    if guest.failure_class:
        raise S8ComposeError(
            f"{side} reported infrastructure failure {guest.failure_class!r}; "
            "an infrastructure outcome is never assemblable as scientific evidence"
        )
    try:
        payload = evaluator.parse_report(guest.report)
    except evaluator.EvaluatorError as exc:
        raise S8ComposeError(f"{side} report is not an authentic evaluator report: {exc}") from exc
    verdict, why = evaluator.verdict_from_report_payload(payload)
    if verdict is None:
        raise S8ComposeError(f"{side} report cannot establish a result: {why}")
    if str(payload.get("instance_id") or "") != spec.task_id:
        raise S8ComposeError(
            f"{side} report instance_id {payload.get('instance_id')!r} != task "
            f"{spec.task_id!r}"
        )
    if str(payload.get("execution_state_identity") or "") not in ("", side):
        raise S8ComposeError(
            f"{side} report declares state {payload.get('execution_state_identity')!r}"
        )

    report_name = f"{spec.task_id}.{side}.report.json"
    log_name = f"{spec.task_id}.{side}.run.log"
    retain_artifact(inputs.store.root, report_name, guest.report)
    retain_artifact(inputs.store.root, log_name, guest.run_log)
    test_output = _artifact_ref(report_name, guest.report, ROLE_TEST_OUTPUT)
    run_log = _artifact_ref(log_name, guest.run_log, ROLE_RUN_LOG)

    implementation_artifact = None
    if inputs.implementation_bytes:
        retain_artifact(
            inputs.store.root, inputs.implementation_name, inputs.implementation_bytes
        )
        implementation_artifact = _artifact_ref(
            inputs.implementation_name, inputs.implementation_bytes,
            ROLE_EVALUATOR_IMPLEMENTATION,
        )

    identity = governance.build_execution_identity(
        instance_id=spec.task_id,
        repository=str(spec.payload["repo"]),
        base_commit=str(spec.payload["base_commit"]),
        execution_state_identity=side,
        execution_state_digest=inputs.state_digest(side),
        evaluator=inputs.evaluator,
        environment_identity=inputs.environment_identity,
        test_command=str(spec.payload["test_cmd"]),
        test_output=test_output,
        run_log=run_log,
    )
    return governance.ExecutionBundle(
        identity=identity,
        declared_result=verdict,
        result_protocol_id=evaluator.RESULT_PROTOCOL_ID,
        test_output=test_output,
        run_log=run_log,
        started_at=guest.started_at,
        finished_at=guest.finished_at,
        implementation_artifact=implementation_artifact,
    )


def _record_for(bundle: governance.ExecutionBundle) -> evidence.EvidenceRecord:
    """The frozen-S8 evidence record for a verified bundle.

    Mirrors ``f2_governance._to_s8_record``: the identity digest becomes S8's
    ``execution_state_digest`` and S8's ``execution_result`` is the bundle's
    (already governance-derived) declared result.
    """
    ident = bundle.identity
    return evidence.build_evidence_record(
        instance_id=ident.instance_id,
        repository=ident.repository,
        base_commit=ident.base_commit,
        execution_state_identity=ident.execution_state_identity,
        execution_state_digest=ident.digest(),
        test_command=ident.test_command,
        environment_identity=ident.environment_identity,
        evaluator_identity=ident.evaluator_id,
        evaluator_version=ident.evaluator_version,
        execution_started_at=bundle.started_at,
        execution_finished_at=bundle.finished_at,
        execution_result=bundle.declared_result,
        test_output_artifact=bundle.test_output,
        run_log_artifact=bundle.run_log,
    )


def build_governed_verifiers(
    *,
    spec: TaskSpec,
    inputs: GovernanceInputs,
    base_bundle: governance.ExecutionBundle,
    gold_bundle: governance.ExecutionBundle,
) -> tuple[
    Callable[[TaskSpec, ExecutionResult], str],
    Callable[[TaskSpec, ExecutionResult], bool],
    Callable[[TaskSpec, ExecutionResult], bool],
]:
    """Return the three session verifiers bound to the real governance contracts."""

    def evaluate(active: TaskSpec, _result: ExecutionResult) -> str:
        # The S8 contract on the *declared* (governance-derived) results.  This is
        # one of three independent checks; verify_pair re-derives from artifacts.
        ok = (
            base_bundle.declared_result == evidence.RESULT_FAIL
            and gold_bundle.declared_result == evidence.RESULT_PASS
        )
        return "pass" if ok else "fail"

    def verify_evidence(active: TaskSpec, _result: ExecutionResult) -> bool:
        verification = evidence.verify_task_evidence(
            task_id=active.task_id,
            repository=str(active.payload["repo"]),
            base_commit=str(active.payload["base_commit"]),
            base_evidence=_record_for(base_bundle),
            gold_evidence=_record_for(gold_bundle),
            artifact_root=inputs.store.root,
            authorized_evaluators=inputs.registry.as_s8_allowlist(),
        )
        return bool(verification.ok)

    def verify_pair(active: TaskSpec, _result: ExecutionResult) -> bool:
        verification = governance.verify_governed_pair(
            base_bundle,
            gold_bundle,
            store=inputs.store,
            registry=inputs.registry,
            task_id=active.task_id,
            repository=str(active.payload["repo"]),
            base_commit=str(active.payload["base_commit"]),
        )
        return bool(verification.ok)

    return evaluate, verify_evidence, verify_pair


def _result_from_bundles(
    spec: TaskSpec,
    base_bundle: governance.ExecutionBundle,
    gold_bundle: governance.ExecutionBundle,
) -> ExecutionResult:
    """A transport-level ExecutionResult summarising the assembled bundles."""
    admitted = (
        base_bundle.declared_result == evidence.RESULT_FAIL
        and gold_bundle.declared_result == evidence.RESULT_PASS
    )
    if not admitted:
        outcome = (
            "base_pass"
            if base_bundle.declared_result == evidence.RESULT_PASS
            else "gold_fail"
        )
        return ExecutionResult(
            task_id=spec.task_id, outcome=outcome, attempt=1,
            attempt_id=f"{spec.task_id}#1", reason="S8 contract not satisfied",
        )
    ref = gold_bundle.test_output
    return ExecutionResult(
        task_id=spec.task_id,
        outcome=ADMITTED_OUTCOME,
        attempt=1,
        attempt_id=f"{spec.task_id}#1",
        disk_id=f"{spec.task_id}-diff",
        evidence_name=ref.name,
        evidence_sha256=ref.digest,
        evidence_size=int(ref.size_bytes or 0),
        evaluator=_evaluator_dict(base_bundle),
    )


def _evaluator_dict(bundle: governance.ExecutionBundle) -> dict[str, str]:
    ident = bundle.identity
    return {
        "evaluator_id": ident.evaluator_id,
        "version": ident.evaluator_version,
        "implementation_digest": ident.evaluator_implementation_digest,
        "procedure_id": ident.procedure_id,
        "protocol_version": ident.protocol_version,
    }


def produce_governed_task(
    *,
    spec: TaskSpec,
    gates: Mapping[str, Any],
    adapter: Any,
    ledger: Any,
    inputs: GovernanceInputs,
    execute: Callable[[TaskSpec, str], GuestEvidence],
    policy: Any = None,
    expected_registration_sha256: str = "",
    ts: str = "",
) -> Any:
    """The production entry point for one task.

    Gates are checked first.  The ``execute`` seam returns the retained guest
    output for a side; the composition then assembles the governed bundles and
    runs the session with the real evidence/governance verifiers.  Admission is
    written only when all three verifiers accept.
    """
    require_gates(gates)
    if expected_registration_sha256 and spec.registration_sha256 != expected_registration_sha256:
        raise SessionError("task spec does not belong to the pinned operative registration")

    base_bundle = assemble_governed_bundle(
        spec=spec, side="base", guest=execute(spec, "base"), inputs=inputs
    )
    gold_bundle = assemble_governed_bundle(
        spec=spec, side="gold", guest=execute(spec, "gold"), inputs=inputs
    )
    result = _result_from_bundles(spec, base_bundle, gold_bundle)
    evaluate, verify_evidence, verify_pair = build_governed_verifiers(
        spec=spec, inputs=inputs, base_bundle=base_bundle, gold_bundle=gold_bundle
    )
    return run_session(
        spec=spec,
        adapter=adapter,
        runner=lambda _spec: result,
        ledger=ledger,
        evaluate=evaluate,
        verify_evidence=verify_evidence,
        verify_pair=verify_pair,
        policy=policy,
        expected_registration_sha256=expected_registration_sha256,
        ts=ts,
    )
