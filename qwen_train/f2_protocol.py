"""Authorized F2 evaluation protocol — ``f2_experiment_j_v1``.

This is the scientific protocol for Experiment J's F2 phase. It is a
provenance-first, **independently regraded**, paired T/X protocol: the
worker/producer is NOT authoritative for the scientific result. The verifier
reconstructs the primary endpoint from retained RAW BEHAVIORAL EVIDENCE and
independently derives the secondary task outcome, then rejects any producer
declaration that disagrees.

Pipeline (SOTA evaluation model)::

    CONTROLLED EXECUTION -> IMMUTABLE EVIDENCE BUNDLE -> TRUSTED ARTIFACT STORE
      -> INDEPENDENT REGRADER -> FROZEN F2 ENDPOINT -> PAIRED T/X STATISTICS

Scientific objective (F0, unchanged): does delivery and utilization of a genuine
lesson L change a fresh worker's observable first-relevant-edit behavior versus
the identical frozen treatment artifact with L removed?

PRIMARY endpoint (F0, unchanged): first qualifying filesystem edit within
``k = 12``, strictly AFTER lesson delivery, whose path is in the task's frozen
``relevant_file_set`` and whose operation is one of
``{write, patch, edit, create}``. No qualifying edit by step 12 => censored at 12.
Task success is SECONDARY and is never synonymous with the primary endpoint.

The endpoint is computed by the FROZEN detector
:func:`qwen_train.f2_endpoint.qualifying_first_edit`; this module does not
reimplement it. Delivery ordering is enforced here by filtering the raw step
records to those strictly after the verified delivery event.

No network. No subprocess. No model call. No time, randomness or mutable global
state may affect the verdict. Read-only verification.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from qwen_train.f2_endpoint import (
    DEFAULT_HORIZON_STEPS,
    QUALIFYING_OPERATIONS,
    freeze_endpoint,
    qualifying_first_edit,
)
from qwen_train.f2_evidence import (
    STATE_ARTIFACT_MISSING,
    STATE_IDENTITY_MISMATCH,
    STATE_MALFORMED,
    STATE_PROVENANCE_NOT_ESTABLISHED,
    STATE_SCIENTIFICALLY_INSUFFICIENT,
    STATE_UNAUTHORIZED_PROCEDURE,
    STATE_UNKNOWN_SCHEMA,
    STATE_VERIFIED,
    ArtifactRef,
    EvidenceVerification,
    _VERIFICATION_PROOF,  # noqa: PLC2701 - the frozen S8 proof mechanism
)
from qwen_train.f2_governance import (
    EvaluatorAuthorization,
    EvaluatorRegistry,
    TrustedArtifactStore,
)

__all__ = [
    "F2_PROTOCOL_ID",
    "F2_ARMS",
    "F2_TREATMENT_ARM",
    "F2_CONTROL_ARM",
    "F2_C0_ARM",
    "F2DeliveryEvidence",
    "F2Bundle",
    "F2Result",
    "derive_f2_result",
    "regrade_f2",
    "verify_f2_clean_room",
    "regrade_f2_pair",
    "find_forbidden_gold",
    "FORBIDDEN_GOLD_KEYS",
    "ROLE_LESSON_BLOCK",
    "ROLE_DELIVERY_EVIDENCE",
    "EXECUTION_STATE_BY_ARM",
]

#: The single authorized F2 result protocol. Do NOT reuse the generic
#: ``json_test_report_v1`` reference deriver as the scientific F2 protocol.
F2_PROTOCOL_ID = "f2_experiment_j_v1"

F2_TREATMENT_ARM = "T"
F2_CONTROL_ARM = "X"
F2_C0_ARM = "C0"
F2_ARMS = (F2_TREATMENT_ARM, F2_CONTROL_ARM, F2_C0_ARM)

#: Artifact roles specific to the F2 protocol. Defined HERE, not in the frozen
#: governance module, so the S8/governance role sets are untouched. A lesson
#: artifact is NOT evaluator implementation; a delivery-evidence artifact is its
#: own role.
ROLE_LESSON_BLOCK = "lesson_block"
ROLE_DELIVERY_EVIDENCE = "delivery_evidence"

#: Scientifically correct execution-state identities. X is the CONTROL arm (the
#: identical artifact with L removed), NOT the gold/reference state: gold is
#: curator-side and never enters the worker-facing identity model.
EXECUTION_STATE_BY_ARM = {
    F2_TREATMENT_ARM: "treatment",
    F2_CONTROL_ARM: "control",
    F2_C0_ARM: "control_empty",
}

#: Keys that must never appear anywhere in an F2 bundle: gold material is
#: curator-side only and must not cross into the worker-facing evidence path.
FORBIDDEN_GOLD_KEYS = frozenset(
    {
        "gold_patch",
        "gold_diff",
        "gold_hunk",
        "gold_source",
        "gold_commit",
        "reference_commit",
        "fixing_commit",
        "solution",
        "patch_content",
        "oracle",
    }
)

_TASK_OUTCOME_STATUSES = ("passed", "failed", "error", "skipped")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _fail(state: str, detail: str) -> EvidenceVerification:
    """Emit through the frozen S8 private-proof mechanism."""
    return EvidenceVerification(state=state, detail=detail, _proof=_VERIFICATION_PROOF)


def _ok(detail: str) -> EvidenceVerification:
    return EvidenceVerification(
        state=STATE_VERIFIED,
        detail=detail,
        integrity_ok=True,
        provenance_ok=True,
        scientifically_sufficient=True,
        _proof=_VERIFICATION_PROOF,
    )


def _find_forbidden(obj: Any, path: str = "") -> str:
    """Recursively locate any forbidden gold key. Returns a path or ''."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            key = str(k).lower()
            if key in FORBIDDEN_GOLD_KEYS:
                return f"{path}.{k}" if path else str(k)
            found = _find_forbidden(v, f"{path}.{k}" if path else str(k))
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found = _find_forbidden(v, f"{path}[{i}]")
            if found:
                return found
    return ""


def find_forbidden_gold(obj: Any) -> str:
    """Public wrapper: locate any forbidden gold key in a raw bundle mapping.

    Admission receives raw mappings and must reject gold material BEFORE it is
    parsed into an :class:`F2Bundle`, so this is exposed rather than private.
    """
    return _find_forbidden(obj)


# ===========================================================================
# Delivery provenance
# ===========================================================================
@dataclass(frozen=True)
class F2DeliveryEvidence:
    """The verified lesson-delivery event. The endpoint must be strictly after it."""

    arm: str
    lesson_block_hash: str
    final_prompt_hash: str
    delivery_timestamp: str
    treatment_artifact_hash: str

    def __post_init__(self) -> None:
        if self.arm not in F2_ARMS:
            raise ValueError(f"arm must be one of {F2_ARMS}, got {self.arm!r}")
        for name in ("final_prompt_hash", "delivery_timestamp", "treatment_artifact_hash"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"F2DeliveryEvidence.{name} is required")
        if len(self.final_prompt_hash) != 64 or len(self.treatment_artifact_hash) != 64:
            raise ValueError("final_prompt_hash and treatment_artifact_hash must be SHA-256 hex")
        if self.arm == F2_TREATMENT_ARM and not self.lesson_block_hash:
            raise ValueError("arm T must carry a lesson_block_hash")
        if self.arm != F2_TREATMENT_ARM and self.lesson_block_hash:
            raise ValueError(f"arm {self.arm} must NOT carry a lesson_block_hash")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "lesson_block_hash": self.lesson_block_hash,
            "final_prompt_hash": self.final_prompt_hash,
            "delivery_timestamp": self.delivery_timestamp,
            "treatment_artifact_hash": self.treatment_artifact_hash,
        }

    def digest(self) -> str:
        return _sha256(_canonical_bytes(self.canonical_payload()))

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "F2DeliveryEvidence":
        return cls(
            arm=str(d.get("arm") or ""),
            lesson_block_hash=str(d.get("lesson_block_hash") or ""),
            final_prompt_hash=str(d.get("final_prompt_hash") or ""),
            delivery_timestamp=str(d.get("delivery_timestamp") or ""),
            treatment_artifact_hash=str(d.get("treatment_artifact_hash") or ""),
        )


# ===========================================================================
# Evidence bundle (producer output - UNTRUSTED)
# ===========================================================================
@dataclass(frozen=True)
class F2Bundle:
    """Immutable producer output for ONE F2 arm. Raw and UNTRUSTED."""

    instance_id: str
    repository: str
    base_commit: str
    relevant_file_set_hash: str
    horizon_k: int
    qualifying_operations: tuple[str, ...]
    delivery: F2DeliveryEvidence
    behavioral_artifact: ArtifactRef
    task_outcome_artifact: ArtifactRef
    treatment_artifact: ArtifactRef
    #: RETAINED delivery evidence. The verifier authenticates the delivery event
    #: from these bytes, never from a producer-declared field alone.
    delivery_artifact: ArtifactRef
    evaluator: EvaluatorAuthorization
    implementation_artifact: ArtifactRef
    declared_endpoint: bool
    declared_first_edit_step: int | None
    declared_task_success: bool
    started_at: str = ""
    finished_at: str = ""
    lesson_block_artifact: ArtifactRef | None = None
    protocol_id: str = F2_PROTOCOL_ID

    @property
    def arm(self) -> str:
        return self.delivery.arm

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "protocol_id": self.protocol_id,
            "instance_id": self.instance_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "relevant_file_set_hash": self.relevant_file_set_hash,
            "horizon_k": self.horizon_k,
            "qualifying_operations": list(self.qualifying_operations),
            "delivery": self.delivery.canonical_payload(),
            "behavioral_artifact": self.behavioral_artifact.to_dict(),
            "task_outcome_artifact": self.task_outcome_artifact.to_dict(),
            "treatment_artifact": self.treatment_artifact.to_dict(),
            "delivery_artifact": self.delivery_artifact.to_dict(),
            "evaluator": self.evaluator.to_dict(),
            "implementation_artifact": self.implementation_artifact.to_dict(),
            "declared_endpoint": self.declared_endpoint,
            "declared_first_edit_step": self.declared_first_edit_step,
            "declared_task_success": self.declared_task_success,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if self.lesson_block_artifact is not None:
            d["lesson_block_artifact"] = self.lesson_block_artifact.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "F2Bundle":
        lb = d.get("lesson_block_artifact")
        return cls(
            instance_id=str(d.get("instance_id") or ""),
            repository=str(d.get("repository") or ""),
            base_commit=str(d.get("base_commit") or ""),
            relevant_file_set_hash=str(d.get("relevant_file_set_hash") or ""),
            horizon_k=int(d.get("horizon_k") or 0),
            qualifying_operations=tuple(d.get("qualifying_operations") or ()),
            delivery=F2DeliveryEvidence.from_dict(d.get("delivery") or {}),
            behavioral_artifact=ArtifactRef.from_dict(d.get("behavioral_artifact") or {}),
            task_outcome_artifact=ArtifactRef.from_dict(d.get("task_outcome_artifact") or {}),
            treatment_artifact=ArtifactRef.from_dict(d.get("treatment_artifact") or {}),
            delivery_artifact=ArtifactRef.from_dict(d.get("delivery_artifact") or {}),
            evaluator=EvaluatorAuthorization.from_dict(d.get("evaluator") or {}),
            implementation_artifact=ArtifactRef.from_dict(d.get("implementation_artifact") or {}),
            declared_endpoint=bool(d.get("declared_endpoint")),
            declared_first_edit_step=(
                int(d["declared_first_edit_step"])
                if d.get("declared_first_edit_step") is not None
                else None
            ),
            declared_task_success=bool(d.get("declared_task_success")),
            started_at=str(d.get("started_at") or ""),
            finished_at=str(d.get("finished_at") or ""),
            lesson_block_artifact=(ArtifactRef.from_dict(lb) if lb is not None else None),
            protocol_id=str(d.get("protocol_id") or ""),
        )


# ===========================================================================
# Derived scientific result (verifier output)
# ===========================================================================
@dataclass(frozen=True)
class F2Result:
    """The independently derived F2 scientific result.

    PRIMARY and SECONDARY are structurally distinct: ``first_edit_step`` /
    ``endpoint`` describe behavioural response to lesson delivery; ``task_success``
    is secondary and is never synonymous with it.
    """

    protocol_id: str
    instance_id: str
    arm: str
    execution_identity: str
    delivery_evidence_identity: str
    relevant_file_set_hash: str
    horizon_k: int
    qualifying_operations: tuple[str, ...]
    first_edit_step: int | None
    first_edit_path: str | None
    first_edit_operation: str | None
    delivery_timestamp: str
    endpoint: bool
    censored: bool
    task_success: bool | None
    behavioral_artifact_digest: str
    task_outcome_artifact_digest: str
    treatment_artifact_digest: str
    delivery_artifact_digest: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "instance_id": self.instance_id,
            "arm": self.arm,
            "execution_identity": self.execution_identity,
            "delivery_evidence_identity": self.delivery_evidence_identity,
            "relevant_file_set_hash": self.relevant_file_set_hash,
            "horizon_k": self.horizon_k,
            "qualifying_operations": list(self.qualifying_operations),
            "first_edit_step": self.first_edit_step,
            "first_edit_path": self.first_edit_path,
            "first_edit_operation": self.first_edit_operation,
            "delivery_timestamp": self.delivery_timestamp,
            "endpoint": self.endpoint,
            "censored": self.censored,
            "task_success": self.task_success,
            "behavioral_artifact_digest": self.behavioral_artifact_digest,
            "task_outcome_artifact_digest": self.task_outcome_artifact_digest,
            "treatment_artifact_digest": self.treatment_artifact_digest,
            "delivery_artifact_digest": self.delivery_artifact_digest,
        }

    def digest(self) -> str:
        return _sha256(_canonical_bytes(self.canonical_payload()))

    def to_dict(self) -> dict[str, Any]:
        d = self.canonical_payload()
        d["result_digest"] = self.digest()
        return d


# ===========================================================================
# Evidence readers
# ===========================================================================
def _read_json(store: TrustedArtifactStore, ref: ArtifactRef, label: str) -> tuple[Any, str]:
    data, why = store.read_bytes(ref.name)
    if data is None:
        return None, f"{label}: {why}"
    try:
        return json.loads(data.decode("utf-8")), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{label}: not parseable JSON: {exc}"


def _derive_task_outcome(store: TrustedArtifactStore, ref: ArtifactRef) -> tuple[bool | None, str]:
    """Independently derive the SECONDARY task outcome from retained evidence.

    Expects ``{"fail_to_pass": {"<nodeid>": "passed"|"failed"|"error"|"skipped"}}``.
    Returns ``True`` only when every declared FAIL_TO_PASS node passed; ``None``
    when the evidence cannot establish it (fail closed).
    """
    payload, why = _read_json(store, ref, "task-outcome artifact")
    if payload is None:
        return None, why
    if not isinstance(payload, Mapping):
        return None, "task-outcome artifact is not a JSON object"
    f2p = payload.get("fail_to_pass")
    if not isinstance(f2p, Mapping) or not f2p:
        return None, "task-outcome artifact has no non-empty 'fail_to_pass' map"
    statuses = {str(v).lower() for v in f2p.values()}
    if not statuses <= set(_TASK_OUTCOME_STATUSES):
        return None, f"unrecognised test statuses: {sorted(statuses)}"
    return statuses <= {"passed"}, "all declared FAIL_TO_PASS nodes passed" if statuses <= {"passed"} else f"statuses {sorted(statuses)}"


def _verified_delivery(
    bundle: F2Bundle, store: TrustedArtifactStore
) -> tuple[F2DeliveryEvidence | None, str]:
    """Authenticate the delivery event from RETAINED bytes (Finding A).

    The producer's declared ``delivery`` is only a CLAIM. The retained
    delivery-evidence artifact is authoritative: it is read from the trusted
    store, its digest is verified, and its canonical content must equal the
    declared delivery record EXACTLY. Endpoint ordering then uses the timestamp
    from the verified artifact, so a producer cannot change which behavioural
    records count by editing a declared field.

    The verifier never substitutes its own wall-clock time.
    """
    data, why = store.read_bytes(bundle.delivery_artifact.name)
    if data is None:
        return None, f"delivery evidence: {why}"
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"delivery evidence is not parseable JSON: {exc}"
    if not isinstance(payload, Mapping):
        return None, "delivery evidence is not a JSON object"
    try:
        retained = F2DeliveryEvidence.from_dict(payload)
    except Exception as exc:  # noqa: BLE001
        return None, f"delivery evidence is malformed: {exc}"
    if _canonical_bytes(retained.canonical_payload()) != _canonical_bytes(
        bundle.delivery.canonical_payload()
    ):
        return None, (
            "the producer-declared delivery record does not match the RETAINED "
            "delivery evidence bytes; the delivery event cannot be authenticated"
        )
    return retained, ""


def _reconstruct_endpoint(
    bundle: F2Bundle,
    store: TrustedArtifactStore,
    relevant_file_set: Sequence[str],
    delivery_timestamp: str,
) -> tuple[dict[str, Any] | None, str, str]:
    """Reconstruct the PRIMARY endpoint from RAW behavioral evidence.

    Returns ``(endpoint|None, status, detail)`` where ``status`` is one of:

    * ``"endpoint"`` - a qualifying edit was found; ``endpoint`` is populated.
    * ``"censored"`` - the run has post-delivery steps but no qualifying edit by
      ``k``; this is a legitimate right-censored observation.
    * ``"insufficient"`` - the evidence cannot establish the endpoint at all
      (no post-delivery steps, or malformed records). FAIL CLOSED: a missing
      event stream must never be inferred as a censored run.

    Delivery ordering is enforced here: only steps STRICTLY after the verified
    delivery timestamp are considered.
    """
    payload, why = _read_json(store, bundle.behavioral_artifact, "behavioral artifact")
    if payload is None:
        return None, "insufficient", why
    if not isinstance(payload, list):
        return None, "insufficient", "behavioral artifact is not a JSON array of step records"
    records: list[Mapping[str, Any]] = []
    for i, r in enumerate(payload):
        if not isinstance(r, Mapping):
            return None, "insufficient", f"behavioral record {i} is not an object"
        for name in ("step_id", "timestamp", "function_name", "operation", "path"):
            if name not in r:
                return None, "insufficient", f"behavioral record {i} is missing {name!r}"
        records.append(r)

    ts = delivery_timestamp
    post = [r for r in records if str(r["timestamp"]) > ts]
    if not post:
        return None, "insufficient", (
            "no behavioral step occurs strictly after the delivery timestamp; the "
            "endpoint cannot be established (a missing event stream is NOT a censored run)"
        )

    detector_records = [
        {
            "function_name": str(r["function_name"]),
            "arguments": {"operation": str(r["operation"]), "path": str(r["path"])},
            "extra": {"step_id": int(r["step_id"])},
        }
        for r in post
    ]
    spec = freeze_endpoint(
        task_id=bundle.instance_id,
        relevant_file_set=relevant_file_set,
        horizon_steps=bundle.horizon_k,
        derivation_evidence=("f2_experiment_j_v1",),
    )
    hit = qualifying_first_edit(detector_records, spec)
    if hit is None:
        return None, "censored", f"censored: no qualifying edit by step {bundle.horizon_k}"
    return {
        "step_id": int(hit["step_id"]),
        "path": str(hit["relevant_file"]),
        "operation": str(hit["operation"]),
    }, "endpoint", "endpoint established"


def _execution_identity(bundle: F2Bundle) -> str:
    """Canonical execution identity for the RESULT.

    Audit timestamps are EXCLUDED so that identical evidence yields an identical
    scientific result (F2 determinism requirement).
    """
    d = bundle.to_dict()
    d.pop("started_at", None)
    d.pop("finished_at", None)
    return _sha256(_canonical_bytes(d))


# ===========================================================================
# Independent regrading
# ===========================================================================
def derive_f2_result(
    bundle: F2Bundle,
    *,
    store: TrustedArtifactStore,
    relevant_file_set: Sequence[str],
) -> tuple[F2Result | None, str]:
    """Derive the F2 scientific result from retained evidence. No trust in the bundle.

    ``relevant_file_set`` is supplied CURATOR-SIDE (from the frozen Q8 manifest);
    its hash must match the bundle's declared ``relevant_file_set_hash``.
    """
    if bundle.protocol_id != F2_PROTOCOL_ID:
        return None, f"wrong protocol {bundle.protocol_id!r} (expected {F2_PROTOCOL_ID!r})"
    if bundle.horizon_k != DEFAULT_HORIZON_STEPS:
        return None, f"horizon k must be {DEFAULT_HORIZON_STEPS}, got {bundle.horizon_k}"
    if tuple(sorted(bundle.qualifying_operations)) != tuple(sorted(QUALIFYING_OPERATIONS)):
        return None, (
            f"qualifying_operations must be exactly {sorted(QUALIFYING_OPERATIONS)}, "
            f"got {sorted(bundle.qualifying_operations)}"
        )
    spec = freeze_endpoint(
        task_id=bundle.instance_id,
        relevant_file_set=relevant_file_set,
        horizon_steps=bundle.horizon_k,
        derivation_evidence=("f2_experiment_j_v1",),
    )
    if spec.relevant_file_set_hash != bundle.relevant_file_set_hash:
        return None, "relevant_file_set does not match the bundle's declared hash"

    # The delivery event is authenticated from RETAINED bytes BEFORE it governs
    # endpoint ordering (Finding A).
    delivery, why = _verified_delivery(bundle, store)
    if delivery is None:
        return None, why

    endpoint, status, why = _reconstruct_endpoint(
        bundle, store, relevant_file_set, delivery.delivery_timestamp
    )
    if status == "insufficient":
        return None, why
    task_success, task_why = _derive_task_outcome(store, bundle.task_outcome_artifact)
    if task_success is None:
        return None, f"task outcome cannot be established: {task_why}"

    return F2Result(
        protocol_id=F2_PROTOCOL_ID,
        instance_id=bundle.instance_id,
        arm=bundle.arm,
        execution_identity=_execution_identity(bundle),
        delivery_evidence_identity=delivery.digest(),
        relevant_file_set_hash=spec.relevant_file_set_hash,
        horizon_k=bundle.horizon_k,
        qualifying_operations=tuple(sorted(bundle.qualifying_operations)),
        first_edit_step=(endpoint["step_id"] if endpoint else None),
        first_edit_path=(endpoint["path"] if endpoint else None),
        first_edit_operation=(endpoint["operation"] if endpoint else None),
        delivery_timestamp=delivery.delivery_timestamp,
        endpoint=status == "endpoint",
        censored=status == "censored",
        task_success=task_success,
        behavioral_artifact_digest=bundle.behavioral_artifact.digest,
        task_outcome_artifact_digest=bundle.task_outcome_artifact.digest,
        treatment_artifact_digest=bundle.treatment_artifact.digest,
        delivery_artifact_digest=bundle.delivery_artifact.digest,
    ), ""


def regrade_f2(
    bundle: F2Bundle | Mapping[str, Any] | None,
    *,
    store: TrustedArtifactStore,
    registry: EvaluatorRegistry,
    relevant_file_set: Sequence[str],
    expected_arm: str | None = None,
) -> EvidenceVerification:
    """Independently regrade ONE F2 arm and emit the frozen S8 proof.

    Order: shape/gold-boundary -> protocol -> evaluator authorization ->
    evaluator BYTES (required for F2) -> artifact integrity -> delivery ->
    endpoint reconstruction -> task outcome -> declaration comparison.
    """
    if bundle is None:
        return _fail(STATE_PROVENANCE_NOT_ESTABLISHED, "no F2 bundle supplied")
    if isinstance(bundle, Mapping):
        leaked = _find_forbidden(bundle)
        if leaked:
            return _fail(
                STATE_MALFORMED, f"forbidden gold material present at {leaked!r}"
            )
        try:
            bundle = F2Bundle.from_dict(bundle)
        except Exception as exc:  # noqa: BLE001
            return _fail(STATE_MALFORMED, f"F2 bundle could not be parsed: {exc}")
    if not isinstance(bundle, F2Bundle):
        return _fail(STATE_MALFORMED, f"unsupported bundle type: {type(bundle).__name__}")

    if bundle.protocol_id != F2_PROTOCOL_ID:
        return _fail(
            STATE_UNKNOWN_SCHEMA,
            f"wrong protocol {bundle.protocol_id!r} (expected {F2_PROTOCOL_ID!r})",
        )
    if expected_arm is not None and bundle.arm != expected_arm:
        return _fail(
            STATE_IDENTITY_MISMATCH,
            f"arm {bundle.arm!r} does not match expected {expected_arm!r}",
        )

    # Evaluator authorization AND the exact authorized protocol.
    #
    # Authorization is looked up directly from the evaluator fields. It does NOT
    # route through a governance ExecutionIdentity, because that identity's state
    # vocabulary is base/gold and X is NOT gold (Finding B).
    ok, why = registry.authorize(
        evaluator_id=bundle.evaluator.evaluator_id,
        version=bundle.evaluator.version,
        implementation_digest=bundle.evaluator.implementation_digest,
        procedure_id=bundle.evaluator.procedure_id,
        protocol_version=bundle.evaluator.protocol_version,
    )
    if not ok:
        return _fail(STATE_UNAUTHORIZED_PROCEDURE, why)
    if bundle.evaluator.protocol_version != F2_PROTOCOL_ID:
        return _fail(
            STATE_UNAUTHORIZED_PROCEDURE,
            f"evaluator is not authorized for protocol {F2_PROTOCOL_ID!r} "
            f"(authorization protocol_version={bundle.evaluator.protocol_version!r})",
        )

    # F2 REQUIRES evaluator implementation bytes (a digest string is not provenance).
    if bundle.implementation_artifact is None or not bundle.implementation_artifact.name:
        return _fail(
            STATE_PROVENANCE_NOT_ESTABLISHED,
            "F2 requires the evaluator implementation artifact; a SHA-256-shaped "
            "digest string alone is not evaluator-byte provenance",
        )

    # Artifact integrity for every referenced artifact.
    refs = [
        bundle.behavioral_artifact,
        bundle.task_outcome_artifact,
        bundle.treatment_artifact,
        bundle.delivery_artifact,
        bundle.implementation_artifact,
    ]
    if bundle.lesson_block_artifact is not None:
        refs.append(bundle.lesson_block_artifact)
    for ref in refs:
        ok, why = store.verify_ref(ref, allowed_roles=_roles_for(ref, bundle))
        if not ok:
            return _fail(_artifact_state(why), why)

    if not _f2_evaluator_bytes_proven(bundle, store):
        return _fail(
            STATE_UNAUTHORIZED_PROCEDURE,
            "the retained evaluator implementation bytes do not match the "
            "authorized implementation digest",
        )

    # Delivery event authenticated from retained bytes (Finding A).
    delivery, why = _verified_delivery(bundle, store)
    if delivery is None:
        return _fail(STATE_PROVENANCE_NOT_ESTABLISHED, why)

    # Treatment/lesson consistency is enforced by the clean room on the pair;
    # here we require the retained treatment bytes to hash to the declared hash.
    treatment_bytes, why = store.read_bytes(bundle.treatment_artifact.name)
    if treatment_bytes is None:
        return _fail(STATE_ARTIFACT_MISSING, f"treatment artifact: {why}")
    if _sha256(treatment_bytes) != bundle.delivery.treatment_artifact_hash:
        return _fail(
            STATE_IDENTITY_MISMATCH,
            "retained treatment bytes do not hash to the delivery record's "
            "treatment_artifact_hash",
        )

    result, why = derive_f2_result(bundle, store=store, relevant_file_set=relevant_file_set)
    if result is None:
        return _fail(STATE_SCIENTIFICALLY_INSUFFICIENT, why)

    # Producer declaration is an INPUT; disagreement is rejected.
    if bool(bundle.declared_endpoint) != bool(result.endpoint):
        return _fail(
            STATE_SCIENTIFICALLY_INSUFFICIENT,
            f"producer declared endpoint={bundle.declared_endpoint} but the retained "
            f"behavioral evidence establishes endpoint={result.endpoint}",
        )
    if bundle.declared_first_edit_step != result.first_edit_step:
        return _fail(
            STATE_SCIENTIFICALLY_INSUFFICIENT,
            f"producer declared first_edit_step={bundle.declared_first_edit_step} but "
            f"the retained evidence establishes {result.first_edit_step}",
        )
    if bool(bundle.declared_task_success) != bool(result.task_success):
        return _fail(
            STATE_SCIENTIFICALLY_INSUFFICIENT,
            f"producer declared task_success={bundle.declared_task_success} but the "
            f"retained evidence establishes {result.task_success}",
        )

    return _ok(
        f"F2 arm {result.arm} regraded: endpoint={result.endpoint} "
        f"first_edit_step={result.first_edit_step} task_success={result.task_success}"
    )


def _artifact_state(why: str) -> str:
    from qwen_train.f2_governance import _artifact_state as _gov_state

    return _gov_state(why)


def _f2_evaluator_bytes_proven(bundle: F2Bundle, store: TrustedArtifactStore) -> bool:
    """Evaluator-BYTE provenance for F2, independent of the governance identity model.

    Registry authorization proves only that a declared identity matches an
    authorized entry. Byte provenance additionally requires the retained
    implementation artifact to be present in the trusted store and to hash to the
    authorized implementation digest.

    This deliberately does NOT route through a governance ``ExecutionIdentity``:
    that vocabulary is base/gold and X is not gold (Finding B).
    """
    from qwen_train.f2_governance import ROLE_EVALUATOR_IMPLEMENTATION

    if bundle.implementation_artifact is None or not bundle.implementation_artifact.name:
        return False
    ok, _ = store.verify_ref(
        bundle.implementation_artifact, allowed_roles=(ROLE_EVALUATOR_IMPLEMENTATION,)
    )
    return bool(
        ok
        and bundle.implementation_artifact.digest
        == bundle.evaluator.implementation_digest
    )


def _roles_for(ref: ArtifactRef, bundle: F2Bundle) -> tuple[str, ...]:
    from qwen_train.f2_evidence import ROLE_RUN_LOG, ROLE_TEST_OUTPUT
    from qwen_train.f2_governance import ROLE_EVALUATOR_IMPLEMENTATION

    if ref is bundle.implementation_artifact:
        return (ROLE_EVALUATOR_IMPLEMENTATION,)
    if ref is bundle.lesson_block_artifact:
        # A lesson artifact is NOT evaluator implementation (Finding D).
        return (ROLE_LESSON_BLOCK,)
    if ref is bundle.delivery_artifact:
        return (ROLE_DELIVERY_EVIDENCE,)
    return (ROLE_TEST_OUTPUT, ROLE_RUN_LOG)


# ===========================================================================
# T/X clean room
# ===========================================================================
def verify_f2_clean_room(
    t_bundle: F2Bundle, x_bundle: F2Bundle, *, store: TrustedArtifactStore
) -> tuple[bool, str]:
    """Prove X is a pure deterministic derivation of T with only L removed."""
    if t_bundle.arm != F2_TREATMENT_ARM:
        return False, f"first bundle must be arm T, got {t_bundle.arm!r}"
    if x_bundle.arm != F2_CONTROL_ARM:
        return False, f"second bundle must be arm X, got {x_bundle.arm!r}"
    for name in (
        "instance_id",
        "repository",
        "base_commit",
        "relevant_file_set_hash",
        "horizon_k",
        "protocol_id",
    ):
        if getattr(t_bundle, name) != getattr(x_bundle, name):
            return False, f"clean-room violation: {name} differs between T and X"
    if t_bundle.qualifying_operations != x_bundle.qualifying_operations:
        return False, "clean-room violation: qualifying_operations differ"
    if t_bundle.evaluator != x_bundle.evaluator:
        return False, "clean-room violation: evaluator/procedure differ between T and X"
    if not t_bundle.delivery.lesson_block_hash:
        return False, "arm T carries no lesson_block_hash"
    if x_bundle.delivery.lesson_block_hash:
        return False, "arm X must not carry a lesson_block_hash"
    if t_bundle.lesson_block_artifact is None:
        return False, "arm T carries no lesson block artifact to verify removal against"

    lb, why = store.read_bytes(t_bundle.lesson_block_artifact.name)
    if lb is None:
        return False, f"lesson block artifact unreadable: {why}"
    if _sha256(lb) != t_bundle.delivery.lesson_block_hash:
        return False, "lesson block artifact does not hash to lesson_block_hash"

    t_txt, why = store.read_bytes(t_bundle.treatment_artifact.name)
    if t_txt is None:
        return False, f"T treatment artifact unreadable: {why}"
    x_txt, why = store.read_bytes(x_bundle.treatment_artifact.name)
    if x_txt is None:
        return False, f"X treatment artifact unreadable: {why}"

    block = lb.decode("utf-8", errors="replace")
    if block not in t_txt.decode("utf-8", errors="replace"):
        return False, "lesson block text is not present in the T treatment artifact"
    expected_x = t_txt.decode("utf-8", errors="replace").replace(block, "", 1).encode("utf-8")
    if expected_x != x_txt:
        return False, (
            "X is not a pure removal of L from T: the artifacts differ by more than "
            "the lesson block"
        )
    return True, "X is T with exactly the lesson block removed"


def regrade_f2_pair(
    t_bundle: F2Bundle | Mapping[str, Any] | None,
    x_bundle: F2Bundle | Mapping[str, Any] | None,
    *,
    store: TrustedArtifactStore,
    registry: EvaluatorRegistry,
    relevant_file_set: Sequence[str],
) -> tuple[EvidenceVerification, dict[str, F2Result]]:
    """Regrade a paired T/X F2 comparison and return both derived results."""
    results: dict[str, F2Result] = {}
    if t_bundle is None or x_bundle is None:
        return _fail(STATE_PROVENANCE_NOT_ESTABLISHED, "both T and X bundles are required"), results
    # Gold boundary BEFORE parsing: a raw mapping must never smuggle gold material
    # past the scan by being converted to a bundle first.
    for label, b in (("T", t_bundle), ("X", x_bundle)):
        if isinstance(b, Mapping):
            leaked = _find_forbidden(b)
            if leaked:
                return _fail(
                    STATE_MALFORMED,
                    f"{label} bundle carries forbidden gold material at {leaked!r}",
                ), results
    if isinstance(t_bundle, Mapping):
        t_bundle = F2Bundle.from_dict(t_bundle)
    if isinstance(x_bundle, Mapping):
        x_bundle = F2Bundle.from_dict(x_bundle)

    tv = regrade_f2(t_bundle, store=store, registry=registry,
                    relevant_file_set=relevant_file_set, expected_arm=F2_TREATMENT_ARM)
    if not tv.ok:
        return _fail(tv.state, f"T: {tv.detail}"), results
    xv = regrade_f2(x_bundle, store=store, registry=registry,
                    relevant_file_set=relevant_file_set, expected_arm=F2_CONTROL_ARM)
    if not xv.ok:
        return _fail(xv.state, f"X: {xv.detail}"), results

    ok, why = verify_f2_clean_room(t_bundle, x_bundle, store=store)
    if not ok:
        return _fail(STATE_IDENTITY_MISMATCH, why), results

    t_res, _ = derive_f2_result(t_bundle, store=store, relevant_file_set=relevant_file_set)
    x_res, _ = derive_f2_result(x_bundle, store=store, relevant_file_set=relevant_file_set)
    results[F2_TREATMENT_ARM] = t_res
    results[F2_CONTROL_ARM] = x_res
    return _ok(
        f"paired T/X regraded: T endpoint={t_res.endpoint} X endpoint={x_res.endpoint}"
    ), results