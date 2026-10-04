"""F2 admission — the single gate that decides whether a task's T/X evidence may
enter the F2 confirmatory analysis.

This module composes the already-frozen layers; it introduces no new scientific
semantics:

* R1-R8 task readiness (``runtime_v2.services.task_readiness``) - authoritative,
  enforced first and fail-closed;
* the authorized F2 protocol ``f2_experiment_j_v1`` (``qwen_train.f2_protocol``) -
  independent regrade of both arms, including evaluator-byte provenance,
  artifact integrity, delivery ordering, endpoint reconstruction and declaration
  rejection;
* the T/X clean-room rule - X must be a pure deterministic removal of L from T.

A task is ADMISSIBLE only when readiness is READY, the readiness declaration
agrees with the evidence bundles on task identity / base commit /
``relevant_file_set``, and the paired regrade (which itself enforces the clean
room) verifies. Anything missing or contradictory fails closed.

No network, no subprocess, no model call, no time, no randomness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from qwen_train.f2_governance import EvaluatorRegistry, TrustedArtifactStore
from qwen_train.f2_protocol import (
    F2_CONTROL_ARM,
    F2_TREATMENT_ARM,
    F2Bundle,
    F2Result,
    regrade_f2_pair,
)
from runtime_v2.services.task_readiness import (
    ReadinessEvidence,
    TaskReadiness,
    TaskReadinessError,
    compute_relevant_file_set_hash,
    evaluate_readiness,
)

__all__ = ["F2Admission", "admit_f2_task"]


@dataclass(frozen=True)
class F2Admission:
    """The admission verdict. ``admissible`` is True only when every layer passes."""

    admissible: bool
    detail: str
    readiness: dict[str, Any]
    protocol_state: str
    clean_room_ok: bool
    results: Mapping[str, F2Result]

    def to_dict(self) -> dict[str, Any]:
        return {
            "admissible": self.admissible,
            "detail": self.detail,
            "readiness": self.readiness,
            "protocol_state": self.protocol_state,
            "clean_room_ok": self.clean_room_ok,
            "results": {k: v.to_dict() for k, v in self.results.items()},
        }


def _not_admissible(detail: str, **kw: Any) -> F2Admission:
    return F2Admission(
        admissible=False,
        detail=detail,
        readiness=kw.get("readiness", {}),
        protocol_state=kw.get("protocol_state", "NOT_EVALUATED"),
        clean_room_ok=bool(kw.get("clean_room_ok", False)),
        results=kw.get("results", {}),
    )


def admit_f2_task(
    *,
    readiness: TaskReadiness | None,
    readiness_evidence: ReadinessEvidence | None,
    store: TrustedArtifactStore,
    registry: EvaluatorRegistry,
    relevant_file_set: Sequence[str],
    t_bundle: F2Bundle | Mapping[str, Any] | None,
    x_bundle: F2Bundle | Mapping[str, Any] | None,
) -> F2Admission:
    """Decide whether one task's paired T/X evidence is admissible.

    Order (fail closed at the first unmet layer):

    1. **R1-R8 readiness.** Missing readiness or evidence is NOT ESTABLISHED.
    2. **Readiness/evidence agreement.** The declaration must match the bundles on
       task identity, base commit and ``relevant_file_set``.
    3. **Paired regrade** under ``f2_experiment_j_v1``, which enforces evaluator
       authorization, evaluator-byte provenance, artifact integrity, delivery
       provenance, endpoint reconstruction, task-outcome derivation, declaration
       rejection and the T/X clean room.
    """
    # 1. Readiness (authoritative, first, fail closed).
    if readiness is None or readiness_evidence is None:
        return _not_admissible(
            "task readiness is NOT ESTABLISHED (no declaration or no evidence supplied)"
        )
    try:
        verdict = evaluate_readiness(readiness_evidence)
    except TaskReadinessError as exc:
        return _not_admissible(f"readiness evidence is malformed: {exc}")
    readiness_doc = verdict.to_dict()
    if not verdict.ready:
        return _not_admissible(
            f"R1-R8 not satisfied; unmet: {list(verdict.unmet)}", readiness=readiness_doc
        )

    if t_bundle is None or x_bundle is None:
        return _not_admissible(
            "both T and X evidence bundles are required", readiness=readiness_doc
        )
    # Gold boundary BEFORE parsing: a raw mapping must never smuggle gold material
    # past the scan by being converted to a bundle first.
    from qwen_train.f2_protocol import find_forbidden_gold

    for label, b in (("T", t_bundle), ("X", x_bundle)):
        if isinstance(b, Mapping):
            leaked = find_forbidden_gold(b)
            if leaked:
                return _not_admissible(
                    f"{label} bundle carries forbidden gold material at {leaked!r}",
                    readiness=readiness_doc,
                )
    if isinstance(t_bundle, Mapping):
        t_bundle = F2Bundle.from_dict(t_bundle)
    if isinstance(x_bundle, Mapping):
        x_bundle = F2Bundle.from_dict(x_bundle)

    # 2. Readiness declaration must agree with the evidence bundles.
    rfs_hash = compute_relevant_file_set_hash(tuple(relevant_file_set))
    try:
        declared_rfs_hash = compute_relevant_file_set_hash(tuple(readiness.relevant_file_set))
    except Exception as exc:  # noqa: BLE001
        return _not_admissible(
            f"readiness relevant_file_set is malformed: {exc}", readiness=readiness_doc
        )
    if declared_rfs_hash != rfs_hash:
        return _not_admissible(
            "the readiness declaration's relevant_file_set does not match the frozen "
            "task set used for regrading",
            readiness=readiness_doc,
        )
    for label, b in (("T", t_bundle), ("X", x_bundle)):
        if b.instance_id != readiness.task_id:
            return _not_admissible(
                f"readiness task_id {readiness.task_id!r} != {label} instance_id "
                f"{b.instance_id!r}",
                readiness=readiness_doc,
            )
        if b.base_commit != readiness.base_commit:
            return _not_admissible(
                f"readiness base_commit {readiness.base_commit[:12]}… != {label} "
                f"base_commit {b.base_commit[:12]}…",
                readiness=readiness_doc,
            )
        if b.relevant_file_set_hash != rfs_hash:
            return _not_admissible(
                f"{label} relevant_file_set_hash does not match the frozen task set",
                readiness=readiness_doc,
            )

    # 3. Paired regrade (includes the clean room).
    verification, results = regrade_f2_pair(
        t_bundle,
        x_bundle,
        store=store,
        registry=registry,
        relevant_file_set=relevant_file_set,
    )
    if not verification.ok:
        return _not_admissible(
            verification.detail,
            readiness=readiness_doc,
            protocol_state=verification.state,
            results=results,
        )

    if set(results) != {F2_TREATMENT_ARM, F2_CONTROL_ARM}:
        return _not_admissible(
            "paired regrade did not return both T and X results",
            readiness=readiness_doc,
            protocol_state=verification.state,
        )

    return F2Admission(
        admissible=True,
        detail=(
            "admissible: R1-R8 READY, readiness agrees with the evidence, and the "
            "paired T/X regrade verified under f2_experiment_j_v1"
        ),
        readiness=readiness_doc,
        protocol_state=verification.state,
        clean_room_ok=True,
        results=results,
    )