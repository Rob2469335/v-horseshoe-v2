"""F2 execution-prerequisite readiness checker (F2-IMPL-AUTH-020).

PURE, READ-ONLY, FAIL-CLOSED. This module does NOT execute the experiment, does
NOT contact a service, and FABRICATES NOTHING. Given the evidence that has been
supplied (environment variables + caller-supplied artifacts), it reports -- for
every prerequisite of the authorized F2 execution -- whether that prerequisite
is ESTABLISHED from evidence, and names the exact blocker when it is not.

A prerequisite is satisfied ONLY when its evidence is explicitly present and
well-formed. Absent / unknown / partially-present is NOT a pass (the same
fail-closed discipline as ``task_readiness.evaluate_readiness``, which handles
the per-task R1-R8 gate; this module handles the EXPERIMENT-level prerequisites).

The checker never reads a secret's value; it only checks presence. It never
infers network isolation from configuration. It never promotes an unproven
population or memory record.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "PrerequisiteCheck",
    "F2ReadinessReport",
    "READINESS_ITEMS",
    "evaluate_f2_readiness",
    "FROZEN_MIN_PAIRS",
]

#: Frozen F2 design minimum (AUTH-013): n = 300 paired observations.
FROZEN_MIN_PAIRS = 300

_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")

#: Ordered prerequisite ids (stable contract for reports/tests).
READINESS_ITEMS: tuple[str, ...] = (
    "distiller_model_identity",
    "weights_digest",
    "evaluator_identity",
    "evaluator_implementation_digest",
    "trusted_artifact_store",
    "receipt_key",
    "protected_population",
    "base_artifacts",
    "gold_artifacts",
    "t_manifest",
    "x_derivation",
    "c0_independent",
    "clean_room_isolation",
    "q9_no_egress",
    "q10_q12_q13_authorization",
    "delivery_instrumentation",
    "regrade_bundle_verification",
    "statistical_analysis",
)

#: Prerequisites that inevitably require an operator/curator action (cannot be
#: produced by this repository alone). Used to classify the blocker, not to pass.
_OPERATOR_ONLY = frozenset(
    {
        "distiller_model_identity",
        "weights_digest",
        "evaluator_identity",
        "evaluator_implementation_digest",
        "trusted_artifact_store",
        "receipt_key",
        "protected_population",
        "base_artifacts",
        "gold_artifacts",
        "clean_room_isolation",
        "q9_no_egress",
        "q10_q12_q13_authorization",
    }
)


@dataclass(frozen=True)
class PrerequisiteCheck:
    item: str
    satisfied: bool
    status: str  # ESTABLISHED / NOT ESTABLISHED / REQUIRES AUTHORIZATION / OPERATOR ACTION REQUIRED
    detail: str
    evidence_required: str
    where: str
    failure_behavior: str
    operator_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "satisfied": self.satisfied,
            "status": self.status,
            "detail": self.detail,
            "evidence_required": self.evidence_required,
            "where": self.where,
            "failure_behavior": self.failure_behavior,
            "operator_action": self.operator_action,
        }


@dataclass(frozen=True)
class F2ReadinessReport:
    checks: tuple[PrerequisiteCheck, ...]
    ready: bool = False
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Per-prerequisite evaluators. Each returns (satisfied, detail, operator_action).
# ---------------------------------------------------------------------------
def _env(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "") or "").strip()


def _check_model_identity(env: Mapping[str, str]):
    v = _env(env, "SWARM_DISTILLER_MODEL")
    if v:
        return True, f"SWARM_DISTILLER_MODEL is set ({v})", ""
    return False, "SWARM_DISTILLER_MODEL is absent", (
        "Set SWARM_DISTILLER_MODEL to the exact distiller model id"
    )


def _check_weights_digest(env: Mapping[str, str]):
    v = _env(env, "SWARM_DISTILLER_WEIGHTS_DIGEST")
    if not v:
        return False, "SWARM_DISTILLER_WEIGHTS_DIGEST is absent", (
            "Provide the real weights digest (sha256) of the distiller weights"
        )
    if not _SHA256_RE.match(v):
        return False, "SWARM_DISTILLER_WEIGHTS_DIGEST is not a sha256 hex digest", (
            "Provide a valid sha256 digest (64 hex chars, optional 'sha256:' prefix)"
        )
    return True, "weights digest present and well-formed (value not printed)", ""


def _check_evaluator_identity(env: Mapping[str, str]):
    eid = _env(env, "SWARM_F2_EVALUATOR_ID")
    ver = _env(env, "SWARM_F2_EVALUATOR_VERSION")
    proc = _env(env, "SWARM_F2_EVALUATOR_PROCEDURE")
    # The authorized evaluator identity is FIVE fields (f2_governance
    # .EvaluatorAuthorization): id, version, implementation_digest, procedure_id
    # and protocol_version. `protocol_version` is the repository constant
    # F2_PROTOCOL_ID, so three of the five are operator-supplied. Requiring only
    # two of them let READY=True coexist with a bundle path that fails closed at
    # f2_arm_worker._load_bundle_governance, because that loader demands
    # SWARM_F2_EVALUATOR_PROCEDURE. This check now covers every operator-supplied
    # identity field rather than expanding the authorized 18-item contract.
    if eid and ver and proc:
        return True, (
            f"evaluator id/version/procedure set ({eid} / {ver} / {proc})"
        ), ""
    missing = [
        k
        for k, val in (
            ("SWARM_F2_EVALUATOR_ID", eid),
            ("SWARM_F2_EVALUATOR_VERSION", ver),
            ("SWARM_F2_EVALUATOR_PROCEDURE", proc),
        )
        if not val
    ]
    return False, "missing " + ", ".join(missing), (
        "Authorize and set the evaluator id, version and procedure id "
        "(protocol_version is the repository constant F2_PROTOCOL_ID)"
    )


def _check_evaluator_impl(env: Mapping[str, str]):
    p = _env(env, "SWARM_F2_EVALUATOR_IMPL")
    if not p:
        return False, "SWARM_F2_EVALUATOR_IMPL is absent", (
            "Provide the evaluator implementation artifact path"
        )
    path = Path(p)
    if not path.is_file():
        return False, f"evaluator implementation artifact not found: {p}", (
            "Provide an existing evaluator implementation artifact"
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return True, f"implementation digest computed from bytes ({digest[:12]}...)", ""


def _check_delivery_instrumentation(env: Mapping[str, str]):
    """Delivery capability must EXIST **and** be enabled.

    Capability presence alone was insufficient. ``f2_arm_worker`` gates governed
    bundle emission on ``SWARM_F2_EMIT_BUNDLE`` and refuses to start without
    ``SWARM_F2_TASK_OUTCOME_REPORT``; with either unset the arm runs, produces
    no evidence chain, and the run cannot be regraded. A readiness gate that
    reported READY=True in that state was reporting on a capability the execution
    path would never use.

    The outcome report is required to be *configured*, not to exist: it is a
    per-arm product of the run, so demanding the file at readiness time would be
    a category error. The authoritative evaluator
    (``qwen_train.f2_evaluator.produce_report``) is what writes it during the run.
    """
    ok, detail, remedy = _check_code_presence(
        "qwen_train.f2_arm_worker", "emit_worker_bundle", "delivery/bundle"
    )
    if not ok:
        return False, detail, remedy

    emit = _env(env, "SWARM_F2_EMIT_BUNDLE")
    if emit.strip().lower() not in ("1", "true", "yes", "on"):
        return False, "SWARM_F2_EMIT_BUNDLE is not enabled", (
            "Set SWARM_F2_EMIT_BUNDLE=1 so governed bundles and receipts are emitted"
        )

    outcome = _env(env, "SWARM_F2_TASK_OUTCOME_REPORT")
    if not outcome:
        return False, "SWARM_F2_TASK_OUTCOME_REPORT is absent", (
            "Designate the path the authoritative evaluator writes the "
            "task-outcome report to (qwen_train.f2_evaluator.produce_report)"
        )

    try:
        from qwen_train import f2_evaluator  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - fail closed
        return False, f"authoritative evaluator is not importable: {exc}", (
            "Restore qwen_train/f2_evaluator.py"
        )
    return True, f"delivery capability present and enabled ({detail})", ""


def _check_trusted_store(env: Mapping[str, str]):
    root = _env(env, "SWARM_F2_ARTIFACT_ROOT")
    days = _env(env, "SWARM_F2_ARTIFACT_RETENTION_DAYS")
    if not root:
        return False, "SWARM_F2_ARTIFACT_ROOT is absent", (
            "Designate an absolute trusted artifact-store root"
        )
    if not Path(root).is_absolute():
        return False, "SWARM_F2_ARTIFACT_ROOT is not absolute", (
            "Set an absolute store root"
        )
    try:
        d = int(days)
        if d < 1:
            raise ValueError
    except ValueError:
        return False, "SWARM_F2_ARTIFACT_RETENTION_DAYS is missing/not an integer", (
            "Set the artifact retention in whole days (>=1)"
        )
    return True, f"trusted store root + retention ({d}d) configured", ""


def _check_receipt_key(env: Mapping[str, str]):
    # Presence only -- NEVER read or print the value.
    if _env(env, "SWARM_RECEIPT_KEY"):
        return True, "SWARM_RECEIPT_KEY is present (value not read)", ""
    return False, "SWARM_RECEIPT_KEY is absent", (
        "Provision the receipt signing key (operator governance capability)"
    )


def _check_population(supplied: Mapping[str, Any]):
    rep = supplied.get("population")
    if rep is None:
        return False, "no population report supplied", (
            "Supply the screened population report (admitted count + provenance)"
        )
    admitted = rep.get("admitted") if isinstance(rep, Mapping) else getattr(rep, "admitted", None)
    try:
        admitted = int(admitted)
    except (TypeError, ValueError):
        return False, "population report has no integer 'admitted' count", (
            "Supply a screened population report"
        )
    if admitted < FROZEN_MIN_PAIRS:
        return False, (
            f"admitted={admitted} < frozen minimum {FROZEN_MIN_PAIRS}"
        ), (
            f"Acquire/curate a protected post-cutoff population of >= {FROZEN_MIN_PAIRS} "
            "admissible tasks (oversampled)"
        )
    return True, f"admitted={admitted} >= {FROZEN_MIN_PAIRS}", ""


def _check_artifacts(supplied: Mapping[str, Any], key: str, label: str):
    ev = supplied.get(key)
    ok = bool(ev.get("verified")) if isinstance(ev, Mapping) else bool(ev)
    if ok:
        return True, f"{label} evidence supplied and verified", ""
    return False, f"{label} evidence absent/unverified", (
        f"Provide protected {label} evidence verified by the authorized evaluator"
    )


def _check_manifest(supplied: Mapping[str, Any], key: str, label: str):
    man = supplied.get(key)
    if man is None:
        return False, f"no {label} manifest supplied", f"Freeze and supply the {label} manifest"
    try:
        from runtime_v2.services.f2_freeze import verify_manifest

        verify_manifest(man)
    except Exception as exc:  # noqa: BLE001 - fail closed
        return False, f"{label} manifest failed verification: {type(exc).__name__}", (
            f"Supply a genuine frozen {label} manifest"
        )
    return True, f"{label} manifest verifies", ""


def _check_clean_room(supplied: Mapping[str, Any]):
    ev = supplied.get("clean_room")
    if isinstance(ev, Mapping) and ev.get("isolated") is True:
        return True, "clean-room isolation attested", ""
    return False, "clean-room isolation not attested", (
        "Run the arm under the parent-owned clean-room boundary and attest it"
    )


def _check_no_egress(supplied: Mapping[str, Any]):
    """Never inferred from configuration.

    Two accepted forms, in descending order of strength:

    * ``no_egress_attestation`` -- a ``qwen_train.f2_isolation`` attestation.
      The checker RE-DERIVES the verdict from the recorded probes, policy
      identity, negative control and arm binding. The caller's claim is ignored
      entirely; a supplied attestation can only ever be confirmed or refuted.
    * ``no_egress`` -- the legacy seven-string receipt. Retained because it is
      the AUTH-020 contract, but it is caller-asserted and strictly weaker.
    """
    att = supplied.get("no_egress_attestation")
    if att is not None:
        try:
            from qwen_train.f2_isolation import verify_isolation_attestation
        except Exception as exc:  # noqa: BLE001 - fail closed
            return False, f"isolation attestation cannot be verified: {exc}", (
                "Restore qwen_train/f2_isolation.py"
            )
        try:
            verdict = verify_isolation_attestation(
                att, required_services=tuple(supplied.get("required_local_services") or ())
            )
        except Exception as exc:  # noqa: BLE001 - fail closed
            return False, f"isolation attestation is malformed: {exc}", (
                "Provide a well-formed f2_isolation attestation record"
            )
        if verdict.satisfied:
            return True, (
                "no-egress attestation verified: "
                f"{len(verdict.satisfied_dimensions)} observed and satisfied, "
                f"{len(verdict.policy_declared_dimensions)} policy-asserted, "
                f"digest {verdict.attestation_digest[:12]}"
            ), ""
        return False, f"isolation attestation not established: {verdict.detail}", (
            "Enforce egress denial, then re-run the attestation probes"
        )

    ev = supplied.get("no_egress")
    if not isinstance(ev, Mapping):
        return False, "no no-egress probe evidence supplied", (
            "Run the confirmatory event under enforced no-egress and provide "
            "either an f2_isolation attestation or the probe receipt"
        )
    required_denied = ("https", "http", "tcp", "udp", "ipv6", "proxy")
    denied = all(ev.get(k) == "denied" for k in required_denied)
    loopback = ev.get("loopback") == "ok"
    if denied and loopback:
        return True, (
            "no-egress probe receipt (caller-asserted): all egress denied, "
            "loopback ok -- prefer an f2_isolation attestation"
        ), ""
    return False, (
        "no-egress probe incomplete: "
        + ", ".join(f"{k}={ev.get(k)!r}" for k in required_denied + ("loopback",))
    ), "Provide complete enforced no-egress probe evidence"


def _check_authorizations(supplied: Mapping[str, Any]):
    auth = supplied.get("authorizations")
    if not isinstance(auth, Mapping):
        return False, "no Q10/Q12/Q13 authorization state supplied", (
            "Record operator authorization for Q10, Q12 and Q13"
        )
    missing = [k for k in ("q10", "q12", "q13") if auth.get(k) is not True]
    if missing:
        return False, "unauthorized: " + ", ".join(missing), (
            "Obtain operator authorization for " + ", ".join(missing)
        )
    return True, "Q10/Q12/Q13 authorized", ""


def _check_code_presence(module: str, attr: str, label: str):
    try:
        mod = __import__(module, fromlist=[attr])
        getattr(mod, attr)
    except Exception as exc:  # noqa: BLE001
        return False, f"{label} capability missing: {type(exc).__name__}", ""
    return True, f"{label} capability present", ""


def evaluate_f2_readiness(
    *,
    env: Mapping[str, str] | None = None,
    supplied: Mapping[str, Any] | None = None,
) -> F2ReadinessReport:
    """Evaluate every F2 execution prerequisite. READY iff ALL are satisfied."""
    env = os.environ if env is None else env
    supplied = supplied or {}

    results: dict[str, tuple[bool, str, str]] = {
        "distiller_model_identity": _check_model_identity(env),
        "weights_digest": _check_weights_digest(env),
        "evaluator_identity": _check_evaluator_identity(env),
        "evaluator_implementation_digest": _check_evaluator_impl(env),
        "trusted_artifact_store": _check_trusted_store(env),
        "receipt_key": _check_receipt_key(env),
        "protected_population": _check_population(supplied),
        "base_artifacts": _check_artifacts(supplied, "base_artifacts", "base"),
        "gold_artifacts": _check_artifacts(supplied, "gold_artifacts", "gold"),
        "t_manifest": _check_manifest(supplied, "t_manifest", "T"),
        "x_derivation": _check_manifest(supplied, "x_manifest", "X"),
        "c0_independent": _check_manifest(supplied, "c0_manifest", "C0"),
        "clean_room_isolation": _check_clean_room(supplied),
        "q9_no_egress": _check_no_egress(supplied),
        "q10_q12_q13_authorization": _check_authorizations(supplied),
        "delivery_instrumentation": _check_delivery_instrumentation(env),
        "regrade_bundle_verification": _check_code_presence(
            "qwen_train.f2_protocol", "regrade_f2", "independent regrade"
        ),
        "statistical_analysis": _check_code_presence(
            "qwen_train.f2_analysis", "independent_reconstruction", "analysis"
        ),
    }

    meta: dict[str, tuple[str, str, str]] = {
        "distiller_model_identity": (
            "exact distiller model identifier",
            "SWARM_DISTILLER_MODEL (operator-supplied env)",
            "F2 arm cannot render; fail closed",
        ),
        "weights_digest": (
            "sha256 of the distiller weights",
            "SWARM_DISTILLER_WEIGHTS_DIGEST",
            "reproducibility unproven; fail closed",
        ),
        "evaluator_identity": (
            "authorized evaluator id + version + procedure id "
            "(protocol_version is the repository constant)",
            "SWARM_F2_EVALUATOR_ID / _VERSION / _PROCEDURE",
            "no trusted evaluator; fail closed",
        ),
        "evaluator_implementation_digest": (
            "sha256 of the evaluator implementation artifact",
            "SWARM_F2_EVALUATOR_IMPL file",
            "evaluator bytes unproven; fail closed",
        ),
        "trusted_artifact_store": (
            "absolute store root + retention policy",
            "SWARM_F2_ARTIFACT_ROOT / _RETENTION_DAYS",
            "no durable trusted store; fail closed",
        ),
        "receipt_key": (
            "receipt signing capability present",
            "SWARM_RECEIPT_KEY (presence only)",
            "no signed receipt; fail closed",
        ),
        "protected_population": (
            f">= {FROZEN_MIN_PAIRS} admitted post-cutoff tasks",
            "screened population report",
            "underpowered/invalid population; forbid execution",
        ),
        "base_artifacts": (
            "verified base-state evidence",
            "authorized evaluator + trusted store",
            "S8 fails; task inadmissible",
        ),
        "gold_artifacts": (
            "verified gold-state evidence (protected)",
            "authorized evaluator + trusted store",
            "S8 fails; task inadmissible",
        ),
        "t_manifest": (
            "frozen T manifest (active lessons)",
            "verify_manifest-valid artifact",
            "no treatment; fail closed",
        ),
        "x_derivation": (
            "X manifest deterministically derived from T",
            "verify_manifest-valid artifact",
            "no control; fail closed",
        ),
        "c0_independent": (
            "independent empty-treatment C0 manifest",
            "verify_manifest-valid artifact",
            "no empty control; fail closed",
        ),
        "clean_room_isolation": (
            "attested clean-room boundary",
            "execution evidence",
            "contamination risk; forbid execution",
        ),
        "q9_no_egress": (
            "enforced no-egress probe receipt",
            "execution evidence",
            "contamination; forbid execution",
        ),
        "q10_q12_q13_authorization": (
            "operator authorization for Q10/Q12/Q13",
            "authorization record",
            "unauthorized run; forbid execution",
        ),
        "delivery_instrumentation": (
            "worker->bundle delivery capability, with governed emission "
            "ENABLED and the evaluator report destination configured",
            "qwen_train.f2_arm_worker.emit_worker_bundle + "
            "SWARM_F2_EMIT_BUNDLE + SWARM_F2_TASK_OUTCOME_REPORT",
            "no evidence chain; fail closed",
        ),
        "regrade_bundle_verification": (
            "independent regrade capability",
            "qwen_train.f2_protocol.regrade_f2",
            "producer-authoritative; fail closed",
        ),
        "statistical_analysis": (
            "frozen analysis + independent reconstruction",
            "qwen_train.f2_analysis.independent_reconstruction",
            "no valid analysis; fail closed",
        ),
    }

    checks = []
    for item in READINESS_ITEMS:
        ok, detail, action = results[item]
        evidence, where, failure = meta[item]
        if ok:
            status = "ESTABLISHED"
        elif item in _OPERATOR_ONLY:
            status = "OPERATOR ACTION REQUIRED"
        else:
            status = "NOT ESTABLISHED"
        checks.append(
            PrerequisiteCheck(
                item=item,
                satisfied=ok,
                status=status,
                detail=detail,
                evidence_required=evidence,
                where=where,
                failure_behavior=failure,
                operator_action=action,
            )
        )

    blockers = tuple(c.item for c in checks if not c.satisfied)
    return F2ReadinessReport(checks=tuple(checks), ready=not blockers, blockers=blockers)
