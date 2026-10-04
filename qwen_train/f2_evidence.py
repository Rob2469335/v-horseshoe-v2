"""S8 evidence provenance contract for Experiment J F2.

Why this module exists
----------------------
The previous S8 screen accepted a task when two strings were non-empty::

    bool(base_evidence_digest) and bool(gold_evidence_digest)

A digest establishes **integrity** and nothing else. It does not establish that
the artifact was produced for *this* task, at *this* base commit, by an
*authorized* evaluator, nor that the base/gold pair actually demonstrates the
property the readiness contract requires (R5: "Base and gold runs archived
outside the agent workspace with SHA-256. A substitute probe does not satisfy
this."). This module replaces string-presence with a machine-verifiable
contract that separates three distinct questions:

1. **Integrity** - does the record hash to its own recorded digest, and do the
   referenced artifacts match theirs?
2. **Provenance** - was the record produced for the declared task / repository /
   base state, by an authorized evaluator at a declared version, from a declared
   execution?
3. **Scientific validity** - does the paired base/gold evidence establish the
   required relationship (same task; base state does not pass; gold state does)?

These are reported as separate booleans and a single reason state. They are
never collapsed into one boolean internally, so a failure preserves *why*.

Scope discipline
----------------
No network, no service, no database, no signing infrastructure. Pure
deterministic serialization + SHA-256, following the repository's existing
canonical-JSON convention (``json.dumps(..., sort_keys=True,
separators=(",", ":"), ensure_ascii=False)``).

Worker-facing safety: a record carries an **opaque** digest of the execution
state. The gold patch, gold diff, gold commit SHA, and any equivalent oracle
content are never part of a record, and the population manifest carries only
compact evidence identities - never the payload.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "SCHEMA_VERSION",
    "EVIDENCE_STATES",
    "ArtifactRef",
    "EvidenceRecord",
    "EvidenceVerification",
    "build_evidence_record",
    "load_evidence_record",
    "verify_evidence_record",
    "verify_task_evidence",
    "r5_from_evidence",
    "STATE_VERIFIED",
    "STATE_PROVENANCE_NOT_ESTABLISHED",
    "STATE_UNAUTHORIZED_PROCEDURE",
    "STATE_ARTIFACT_ESCAPES_ROOT",
    "LOAD_PARSED",
    "LOAD_MISSING",
    "LOAD_MALFORMED",
    "ROLE_TEST_OUTPUT",
    "ROLE_RUN_LOG",
    "VALID_ARTIFACT_ROLES",
]

SCHEMA_VERSION = "f2_evidence_record_v1"

#: Execution-state identities. A record describes exactly one of these.
STATE_BASE = "base"
STATE_GOLD = "gold"
VALID_EXECUTION_STATES = (STATE_BASE, STATE_GOLD)

#: Execution results. Only these three are representable.
RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_ERROR = "error"
VALID_RESULTS = (RESULT_PASS, RESULT_FAIL, RESULT_ERROR)

#: Optional failure classification, so an execution failure, an environment /
#: infrastructure failure, and an evaluator failure are distinguishable rather
#: than collapsed into "error".
FAILURE_NONE = ""
FAILURE_EXECUTION = "execution"
FAILURE_ENVIRONMENT = "environment"
FAILURE_EVALUATOR = "evaluator"
VALID_FAILURE_CLASSES = (
    FAILURE_NONE,
    FAILURE_EXECUTION,
    FAILURE_ENVIRONMENT,
    FAILURE_EVALUATOR,
)
_FAILURE_CLASS_TO_STATE = {
    FAILURE_EXECUTION: "EXECUTION_FAILURE",
    FAILURE_ENVIRONMENT: "ENVIRONMENT_FAILURE",
    FAILURE_EVALUATOR: "EVALUATOR_FAILURE",
}

#: Distinct, non-collapsible outcomes. VERIFIED is the only accepting state.
STATE_VERIFIED = "VERIFIED"
STATE_MISSING = "MISSING"
STATE_MALFORMED = "MALFORMED"
STATE_UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
STATE_DIGEST_MISMATCH = "DIGEST_MISMATCH"
STATE_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
STATE_EXECUTION_FAILURE = "EXECUTION_FAILURE"
STATE_ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
STATE_EVALUATOR_FAILURE = "EVALUATOR_FAILURE"
STATE_UNAUTHORIZED_PROCEDURE = "UNAUTHORIZED_PROCEDURE"
STATE_ARTIFACT_MISSING = "ARTIFACT_MISSING"
STATE_ARTIFACT_DIGEST_MISMATCH = "ARTIFACT_DIGEST_MISMATCH"
STATE_ARTIFACT_ESCAPES_ROOT = "ARTIFACT_ESCAPES_ROOT"
STATE_SCIENTIFICALLY_INSUFFICIENT = "SCIENTIFICALLY_INSUFFICIENT"

#: Provenance could not be established because a REQUIRED provenance input was
#: absent (no trusted artifact root, or no evaluator authorization supplied).
#: Deliberately distinct from UNAUTHORIZED_PROCEDURE, which means an evaluator
#: WAS declared and the allowlist rejected it. Conflating the two would hide
#: whether the problem is "we have no authority" or "this procedure is denied".
STATE_PROVENANCE_NOT_ESTABLISHED = "PROVENANCE_NOT_ESTABLISHED"

#: A successful PARSE. Explicitly NOT a verification outcome: loading a JSON
#: object must never be readable as "this evidence has been verified".
LOAD_PARSED = "PARSED"
LOAD_MISSING = "MISSING"
LOAD_MALFORMED = "MALFORMED"

EVIDENCE_STATES = (
    STATE_VERIFIED,
    STATE_MISSING,
    STATE_MALFORMED,
    STATE_UNKNOWN_SCHEMA,
    STATE_DIGEST_MISMATCH,
    STATE_IDENTITY_MISMATCH,
    STATE_EXECUTION_FAILURE,
    STATE_ENVIRONMENT_FAILURE,
    STATE_EVALUATOR_FAILURE,
    STATE_UNAUTHORIZED_PROCEDURE,
    STATE_PROVENANCE_NOT_ESTABLISHED,
    STATE_ARTIFACT_MISSING,
    STATE_ARTIFACT_DIGEST_MISMATCH,
    STATE_ARTIFACT_ESCAPES_ROOT,
    STATE_SCIENTIFICALLY_INSUFFICIENT,
)

#: Artifact roles. Bound into the record so a test-output artifact cannot be
#: silently substituted for a run-log artifact (or vice versa).
ROLE_TEST_OUTPUT = "test_output"
ROLE_RUN_LOG = "run_log"
VALID_ARTIFACT_ROLES = (ROLE_TEST_OUTPUT, ROLE_RUN_LOG)

_REQUIRED_STRING_FIELDS = (
    "schema_version",
    "instance_id",
    "repository",
    "base_commit",
    "execution_state_identity",
    "execution_state_digest",
    "test_command",
    "environment_identity",
    "evaluator_identity",
    "evaluator_version",
    "execution_started_at",
    "execution_finished_at",
    "execution_result",
)


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Deterministic UTF-8 canonical JSON (repository convention)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


#: Module-private sentinel. Possession of this object is the ONLY way to
#: construct an ``EvidenceVerification``, which makes the verifier the sole
#: producer of verification results.
_VERIFICATION_PROOF = object()


def _is_sha256(value: str) -> bool:
    s = str(value or "")
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


@dataclass(frozen=True)
class ArtifactRef:
    """A referenced artifact: role, identity, digest, and size. No payload.

    ``role`` is bound so a test-output artifact cannot be silently substituted
    for a run-log artifact. The record already binds producer identity
    (``evaluator_identity``/``evaluator_version``) and execution state
    (``execution_state_identity``/``execution_state_digest``) at the record level,
    so they are not duplicated per artifact.
    """

    name: str
    digest: str
    size_bytes: int | None = None
    role: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "digest": self.digest}
        if self.size_bytes is not None:
            d["size_bytes"] = int(self.size_bytes)
        if self.role:
            d["role"] = self.role
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ArtifactRef":
        return cls(
            name=str(d.get("name") or ""),
            digest=str(d.get("digest") or ""),
            size_bytes=(int(d["size_bytes"]) if d.get("size_bytes") is not None else None),
            role=str(d.get("role") or ""),
        )


@dataclass(frozen=True)
class EvidenceRecord:
    """One canonical, digest-bound record of a single base OR gold execution.

    The digest covers the canonical payload *excluding* ``evidence_record_digest``
    itself, so it can never be self-referential.
    """

    schema_version: str
    instance_id: str
    repository: str
    base_commit: str
    execution_state_identity: str
    execution_state_digest: str
    test_command: str
    environment_identity: str
    evaluator_identity: str
    evaluator_version: str
    execution_started_at: str
    execution_finished_at: str
    execution_result: str
    test_output_artifact: ArtifactRef
    run_log_artifact: ArtifactRef
    failure_class: str = FAILURE_NONE
    evidence_record_digest: str = ""

    # -- canonicalization -------------------------------------------------
    def canonical_payload(self) -> dict[str, Any]:
        """The deterministic payload. Excludes the digest itself.

        TIMESTAMPS ARE DELIBERATELY EXCLUDED (H2/H19). ``execution_started_at``
        and ``execution_finished_at`` are retained on the record for auditability
        but are NOT part of the cryptographic identity: a clock is not a trust
        anchor, and a formatting or skew difference must not change what the
        evidence scientifically IS. Everything that determines the scientific
        identity of the evaluation IS included.
        """
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "execution_state_identity": self.execution_state_identity,
            "execution_state_digest": self.execution_state_digest,
            "test_command": self.test_command,
            "environment_identity": self.environment_identity,
            "evaluator_identity": self.evaluator_identity,
            "evaluator_version": self.evaluator_version,
            "execution_result": self.execution_result,
            "failure_class": self.failure_class,
            "test_output_artifact": self.test_output_artifact.to_dict(),
            "run_log_artifact": self.run_log_artifact.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.canonical_payload())

    def compute_digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def verify_digest(self) -> bool:
        return bool(self.evidence_record_digest) and (
            self.evidence_record_digest == self.compute_digest()
        )

    def to_dict(self) -> dict[str, Any]:
        """Full record. Includes the audit timestamps that ``canonical_payload``
        deliberately excludes from the cryptographic identity."""
        d = self.canonical_payload()
        d["execution_started_at"] = self.execution_started_at
        d["execution_finished_at"] = self.execution_finished_at
        d["evidence_record_digest"] = self.evidence_record_digest
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EvidenceRecord":
        return cls(
            schema_version=str(d.get("schema_version") or ""),
            instance_id=str(d.get("instance_id") or ""),
            repository=str(d.get("repository") or ""),
            base_commit=str(d.get("base_commit") or ""),
            execution_state_identity=str(d.get("execution_state_identity") or ""),
            execution_state_digest=str(d.get("execution_state_digest") or ""),
            test_command=str(d.get("test_command") or ""),
            environment_identity=str(d.get("environment_identity") or ""),
            evaluator_identity=str(d.get("evaluator_identity") or ""),
            evaluator_version=str(d.get("evaluator_version") or ""),
            execution_started_at=str(d.get("execution_started_at") or ""),
            execution_finished_at=str(d.get("execution_finished_at") or ""),
            execution_result=str(d.get("execution_result") or ""),
            failure_class=str(d.get("failure_class") or FAILURE_NONE),
            test_output_artifact=ArtifactRef.from_dict(
                d.get("test_output_artifact") or {}
            ),
            run_log_artifact=ArtifactRef.from_dict(d.get("run_log_artifact") or {}),
            evidence_record_digest=str(d.get("evidence_record_digest") or ""),
        )


def build_evidence_record(
    *,
    instance_id: str,
    repository: str,
    base_commit: str,
    execution_state_identity: str,
    execution_state_digest: str,
    test_command: str,
    environment_identity: str,
    evaluator_identity: str,
    evaluator_version: str,
    execution_started_at: str,
    execution_finished_at: str,
    execution_result: str,
    failure_class: str = FAILURE_NONE,
    test_output_artifact: ArtifactRef | Mapping[str, Any],
    run_log_artifact: ArtifactRef | Mapping[str, Any],
) -> EvidenceRecord:
    """Construct a record and bind it to its own computed digest."""
    rec = EvidenceRecord(
        schema_version=SCHEMA_VERSION,
        instance_id=instance_id,
        repository=repository,
        base_commit=base_commit,
        execution_state_identity=execution_state_identity,
        execution_state_digest=execution_state_digest,
        test_command=test_command,
        environment_identity=environment_identity,
        evaluator_identity=evaluator_identity,
        evaluator_version=evaluator_version,
        execution_started_at=execution_started_at,
        execution_finished_at=execution_finished_at,
        execution_result=execution_result,
        failure_class=failure_class,
        test_output_artifact=(
            test_output_artifact
            if isinstance(test_output_artifact, ArtifactRef)
            else ArtifactRef.from_dict(test_output_artifact)
        ),
        run_log_artifact=(
            run_log_artifact
            if isinstance(run_log_artifact, ArtifactRef)
            else ArtifactRef.from_dict(run_log_artifact)
        ),
    )
    return EvidenceRecord(
        **{**rec.__dict__, "evidence_record_digest": rec.compute_digest()}
    )


@dataclass(frozen=True)
class EvidenceVerification:
    """The result of verifying one record or one base/gold pair.

    Three independent judgements plus a reason. ``ok`` is True only for VERIFIED.

    TRUST BOUNDARY (H3): the Python type layer is not a trust boundary, so this
    object cannot be constructed by an ordinary caller. ``__post_init__`` requires
    the module-private ``_VERIFICATION_PROOF`` sentinel, which only the verifier
    functions in this module possess. A caller therefore cannot manufacture
    ``EvidenceVerification(state="VERIFIED")`` and have the system treat it as
    trusted evidence.
    """

    state: str
    detail: str = ""
    integrity_ok: bool = False
    provenance_ok: bool = False
    scientifically_sufficient: bool = False
    _proof: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._proof is not _VERIFICATION_PROOF:
            raise TypeError(
                "EvidenceVerification must be produced by the S8 verifier "
                "(verify_evidence_record / verify_task_evidence); it cannot be "
                "constructed directly."
            )

    @property
    def ok(self) -> bool:
        return self.state == STATE_VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "integrity_ok": self.integrity_ok,
            "provenance_ok": self.provenance_ok,
            "scientifically_sufficient": self.scientifically_sufficient,
        }


def _fail(state: str, detail: str) -> EvidenceVerification:
    return EvidenceVerification(state=state, detail=detail, _proof=_VERIFICATION_PROOF)


def _hash_file(path: Path) -> tuple[str, int]:
    """Hash the exact bytes of ONE opened handle (TOCTOU-safe).

    Digest and size are derived from a single ``open``/read sequence, so there is
    no stat-then-open window in which the file could be swapped.
    """
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:  # single open; binary => no newline translation
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _resolve_artifact_path(root: Path, name: str) -> tuple[Path | None, str]:
    """Resolve an artifact name safely UNDER ``root``.

    Rejects: empty names, absolute paths (POSIX or Windows drive/UNC), any
    parent-directory component, and anything that escapes ``root`` after
    resolution -- which also catches a symlink or junction pointing outside,
    because ``resolve()`` follows links before containment is checked.
    """
    raw = str(name or "").strip()
    if not raw:
        return None, "artifact reference has no name"
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.drive or raw[0] in ("/", "\\"):
        return None, f"artifact name {raw!r} is an absolute path"
    if any(part == ".." for part in candidate.parts):
        return None, f"artifact name {raw!r} contains a parent-directory component"
    root_resolved = root.resolve()
    resolved = (root_resolved / candidate).resolve()
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        return None, f"artifact {raw!r} escapes the trusted artifact root"
    return resolved, ""


def _check_artifacts(
    rec: EvidenceRecord, artifact_root: Path | None
) -> EvidenceVerification | None:
    """Validate artifact references and, when a root is supplied, re-hash bytes.

    Fails closed when NO trusted artifact root is supplied (H18): artifact
    provenance cannot be established, so the record must not be reportable as
    VERIFIED. Roles are required and must match the slot they occupy, so a
    test-output artifact cannot be substituted for a run-log.
    """
    pairs = (
        (rec.test_output_artifact, ROLE_TEST_OUTPUT),
        (rec.run_log_artifact, ROLE_RUN_LOG),
    )
    for ref, expected_role in pairs:
        if not ref.name:
            return _fail(STATE_MALFORMED, "artifact reference has no name")
        if not _is_sha256(ref.digest):
            return _fail(
                STATE_MALFORMED,
                f"artifact {ref.name!r} digest is not a SHA-256 hex string",
            )
        if not ref.role:
            return _fail(
                STATE_MALFORMED,
                f"artifact {ref.name!r} declares no role (required: one of "
                f"{VALID_ARTIFACT_ROLES})",
            )
        if ref.role not in VALID_ARTIFACT_ROLES:
            return _fail(
                STATE_MALFORMED,
                f"artifact {ref.name!r} has unknown role {ref.role!r}",
            )
        if ref.role != expected_role:
            return _fail(
                STATE_IDENTITY_MISMATCH,
                f"artifact {ref.name!r} declares role {ref.role!r} but occupies "
                f"the {expected_role!r} slot",
            )

    if artifact_root is None:
        return _fail(
            STATE_PROVENANCE_NOT_ESTABLISHED,
            "no trusted artifact root supplied; artifact provenance cannot be "
            "established, so this record is not VERIFIED",
        )

    root = Path(artifact_root)
    for ref in (rec.test_output_artifact, rec.run_log_artifact):
        resolved, why = _resolve_artifact_path(root, ref.name)
        if resolved is None:
            return _fail(STATE_ARTIFACT_ESCAPES_ROOT, why)
        try:
            actual, size = _hash_file(resolved)
        except FileNotFoundError:
            return _fail(STATE_ARTIFACT_MISSING, f"artifact not found: {ref.name!r}")
        except OSError as exc:
            return _fail(
                STATE_ARTIFACT_MISSING, f"artifact unreadable: {ref.name!r}: {exc}"
            )
        if actual != ref.digest:
            return _fail(
                STATE_ARTIFACT_DIGEST_MISMATCH,
                f"artifact {ref.name!r} hashes {actual[:12]}… != recorded "
                f"{ref.digest[:12]}…",
            )
        if ref.size_bytes is not None and int(ref.size_bytes) != size:
            return _fail(
                STATE_ARTIFACT_DIGEST_MISMATCH,
                f"artifact {ref.name!r} size {size} != recorded {ref.size_bytes}",
            )
    return None


def verify_evidence_record(
    record: EvidenceRecord | Mapping[str, Any] | None,
    *,
    artifact_root: Path | str | None = None,
    authorized_evaluators: Mapping[str, tuple[str, ...]] | None = None,
) -> EvidenceVerification:
    """Verify ONE record's integrity and provenance (not the pair relationship)."""
    if record is None:
        return _fail(STATE_MISSING, "no evidence record supplied")
    if isinstance(record, Mapping):
        try:
            record = EvidenceRecord.from_dict(record)
        except Exception as exc:  # noqa: BLE001 - normalize to a reason
            return _fail(STATE_MALFORMED, f"record could not be parsed: {exc}")
    if not isinstance(record, EvidenceRecord):
        return _fail(STATE_MALFORMED, f"unsupported record type: {type(record).__name__}")

    # Required-field presence FIRST, so a missing/empty field is reported as
    # MALFORMED rather than being misread as an unknown schema.
    for name in _REQUIRED_STRING_FIELDS:
        if not str(getattr(record, name) or "").strip():
            return _fail(STATE_MALFORMED, f"missing required field: {name}")

    if record.schema_version != SCHEMA_VERSION:
        return _fail(
            STATE_UNKNOWN_SCHEMA,
            f"unknown schema_version {record.schema_version!r} "
            f"(expected {SCHEMA_VERSION!r})",
        )

    if record.execution_state_identity not in VALID_EXECUTION_STATES:
        return _fail(
            STATE_MALFORMED,
            f"execution_state_identity must be one of {VALID_EXECUTION_STATES}, "
            f"got {record.execution_state_identity!r}",
        )
    if record.execution_result not in VALID_RESULTS:
        return _fail(
            STATE_MALFORMED,
            f"execution_result must be one of {VALID_RESULTS}, "
            f"got {record.execution_result!r}",
        )
    if record.failure_class not in VALID_FAILURE_CLASSES:
        return _fail(
            STATE_MALFORMED,
            f"failure_class must be one of {VALID_FAILURE_CLASSES}, "
            f"got {record.failure_class!r}",
        )
    if not _is_sha256(record.execution_state_digest):
        return _fail(
            STATE_MALFORMED, "execution_state_digest is not a SHA-256 hex string"
        )

    # INTEGRITY
    if not record.verify_digest():
        return _fail(
            STATE_DIGEST_MISMATCH,
            "record digest does not match its canonical payload "
            f"(recorded {record.evidence_record_digest[:12]}…, computed "
            f"{record.compute_digest()[:12]}…)",
        )

    # ARTIFACT INTEGRITY (curator-side only)
    art_fail = _check_artifacts(record, Path(artifact_root) if artifact_root else None)
    if art_fail is not None:
        return art_fail

    # PROVENANCE - authorized evaluator procedure.
    #
    # FAIL CLOSED (H1): the absence of an authorization list is NOT permission.
    # Previously ``authorized_evaluators=None`` disabled the check entirely, which
    # meant any evaluator could be treated as authorized. Absence is now reported
    # as PROVENANCE_NOT_ESTABLISHED, which is deliberately distinct from
    # UNAUTHORIZED_PROCEDURE (a declared evaluator the allowlist rejects).
    if not authorized_evaluators:
        return _fail(
            STATE_PROVENANCE_NOT_ESTABLISHED,
            "no evaluator authorization supplied; the authorized procedure cannot "
            "be established, so this record is not VERIFIED",
        )
    allowed = authorized_evaluators.get(record.evaluator_identity)
    if allowed is None:
        return _fail(
            STATE_UNAUTHORIZED_PROCEDURE,
            f"evaluator {record.evaluator_identity!r} is not an authorized "
            "procedure for S8 evidence",
        )
    if not allowed:
        return _fail(
            STATE_UNAUTHORIZED_PROCEDURE,
            f"evaluator {record.evaluator_identity!r} has an EMPTY authorized "
            "version set; no version of it is authorized",
        )
    if record.evaluator_version not in allowed:
        return _fail(
            STATE_UNAUTHORIZED_PROCEDURE,
            f"evaluator {record.evaluator_identity!r} version "
            f"{record.evaluator_version!r} is not authorized "
            f"(authorized: {sorted(allowed)})",
        )

    # OPERATIONAL FAILURE - classified so execution, environment/infrastructure
    # and evaluator failures are distinguishable, not collapsed into "error".
    if record.failure_class != FAILURE_NONE:
        return _fail(
            _FAILURE_CLASS_TO_STATE[record.failure_class],
            f"record declares a {record.failure_class} failure "
            f"(execution_result={record.execution_result!r})",
        )
    if record.execution_result == RESULT_ERROR:
        return _fail(
            STATE_EXECUTION_FAILURE,
            "record reports an execution error with no failure_class",
        )

    return EvidenceVerification(
        state=STATE_VERIFIED,
        detail="record integrity and provenance verified",
        integrity_ok=True,
        provenance_ok=True,
        scientifically_sufficient=False,  # a single record cannot establish the pair
        _proof=_VERIFICATION_PROOF,
    )


def _single_failure_as_pair(
    v: EvidenceVerification, side: str
) -> EvidenceVerification:
    return EvidenceVerification(
        state=v.state,
        detail=f"{side}: {v.detail}",
        integrity_ok=False,
        provenance_ok=False,
        scientifically_sufficient=False,
        _proof=_VERIFICATION_PROOF,
    )


def verify_task_evidence(
    *,
    task_id: str,
    repository: str,
    base_commit: str,
    base_evidence: EvidenceRecord | Mapping[str, Any] | None,
    gold_evidence: EvidenceRecord | Mapping[str, Any] | None,
    artifact_root: Path | str | None = None,
    authorized_evaluators: Mapping[str, tuple[str, ...]] | None = None,
    expected_gold_state_digest: str | None = None,
) -> EvidenceVerification:
    """Verify the base/gold PAIR for one task.

    Establishes, in order: both records present; each individually verified
    (integrity + provenance); both describe the declared task/repository/base;
    they describe two DIFFERENT states; and the pair demonstrates the required
    scientific property - the base state does not pass and the gold state does.

    ``expected_gold_state_digest``, when supplied, binds the gold record to a
    curator-known reference-state digest (e.g. the Q8 ``reference_digest``)
    WITHOUT that value ever entering a worker-facing manifest.
    """
    if base_evidence is None and gold_evidence is None:
        return _fail(STATE_MISSING, "no base or gold evidence supplied")
    if base_evidence is None:
        return _fail(STATE_MISSING, "base evidence missing")
    if gold_evidence is None:
        return _fail(STATE_MISSING, "gold evidence missing")

    base_v = verify_evidence_record(
        base_evidence,
        artifact_root=artifact_root,
        authorized_evaluators=authorized_evaluators,
    )
    if not base_v.ok:
        return _single_failure_as_pair(base_v, "base")
    gold_v = verify_evidence_record(
        gold_evidence,
        artifact_root=artifact_root,
        authorized_evaluators=authorized_evaluators,
    )
    if not gold_v.ok:
        return _single_failure_as_pair(gold_v, "gold")

    base_rec = (
        base_evidence
        if isinstance(base_evidence, EvidenceRecord)
        else EvidenceRecord.from_dict(base_evidence)
    )
    gold_rec = (
        gold_evidence
        if isinstance(gold_evidence, EvidenceRecord)
        else EvidenceRecord.from_dict(gold_evidence)
    )

    # IDENTITY: both must describe the declared task, and the same one.
    for label, rec in (("base", base_rec), ("gold", gold_rec)):
        if rec.instance_id != task_id:
            return _fail(
                STATE_IDENTITY_MISMATCH,
                f"{label} evidence instance_id {rec.instance_id!r} != task "
                f"{task_id!r}",
            )
        if rec.repository != repository:
            return _fail(
                STATE_IDENTITY_MISMATCH,
                f"{label} evidence repository {rec.repository!r} != {repository!r}",
            )
        if rec.base_commit != base_commit:
            return _fail(
                STATE_IDENTITY_MISMATCH,
                f"{label} evidence base_commit {rec.base_commit[:12]}… != "
                f"{base_commit[:12]}…",
            )

    if base_rec.execution_state_identity != STATE_BASE:
        return _fail(
            STATE_IDENTITY_MISMATCH,
            f"base evidence declares state {base_rec.execution_state_identity!r}",
        )
    if gold_rec.execution_state_identity != STATE_GOLD:
        return _fail(
            STATE_IDENTITY_MISMATCH,
            f"gold evidence declares state {gold_rec.execution_state_identity!r}",
        )

    if base_rec.execution_state_digest == gold_rec.execution_state_digest:
        return _fail(
            STATE_IDENTITY_MISMATCH,
            "base and gold evidence describe the SAME execution state digest; "
            "they cannot be two distinct states",
        )

    if expected_gold_state_digest is not None and (
        gold_rec.execution_state_digest != expected_gold_state_digest
    ):
        return _fail(
            STATE_IDENTITY_MISMATCH,
            "gold evidence state digest does not match the expected reference "
            "state for this task",
        )

    # SCIENTIFIC VALIDITY: the required base/gold property.
    if base_rec.execution_result != RESULT_FAIL:
        return _fail(
            STATE_SCIENTIFICALLY_INSUFFICIENT,
            f"base state result is {base_rec.execution_result!r}; the readiness "
            "contract requires the declared FAIL_TO_PASS to be non-passing at base",
        )
    if gold_rec.execution_result != RESULT_PASS:
        return _fail(
            STATE_SCIENTIFICALLY_INSUFFICIENT,
            f"gold state result is {gold_rec.execution_result!r}; the readiness "
            "contract requires it to pass at gold",
        )

    return EvidenceVerification(
        state=STATE_VERIFIED,
        detail="base/gold pair verified: integrity, provenance, and required "
        "base-fails/gold-passes relationship",
        integrity_ok=True,
        provenance_ok=True,
        scientifically_sufficient=True,
        _proof=_VERIFICATION_PROOF,
    )


def r5_from_evidence(verification: EvidenceVerification) -> bool | None:
    """Map an S8 verification onto the R5 tri-state.

    ``VERIFIED`` -> ``True``. ``MISSING`` -> ``None`` (NOT ESTABLISHED, the
    fail-closed default). Any other failure -> ``False``.

    This is the ONLY sanctioned way to turn R5 true: R5 must never be derived
    from digest presence. A caller holding only ``base_evidence_digest`` /
    ``gold_evidence_digest`` has no way to reach ``True`` through this function.
    """
    if verification.state == STATE_VERIFIED:
        return True
    if verification.state == STATE_MISSING:
        return None
    return False


def load_evidence_record(
    source: EvidenceRecord | Mapping[str, Any] | str | Path | None,
) -> tuple[EvidenceRecord | None, str, str]:
    """Parse a record from a record, mapping, path, or JSON text.

    Returns ``(record_or_None, load_state, detail)`` where ``load_state`` is one
    of ``LOAD_PARSED`` / ``LOAD_MISSING`` / ``LOAD_MALFORMED``. It is deliberately
    NOT a verification state: a successfully parsed JSON object is UNTRUSTED and
    must be passed to :func:`verify_evidence_record` before it means anything.
    Never raises for bad input - a malformed artifact is a *reason*, not an
    exception.
    """
    if source is None:
        return None, LOAD_MISSING, "no evidence source"
    if isinstance(source, EvidenceRecord):
        return source, LOAD_PARSED, ""
    if isinstance(source, Mapping):
        return EvidenceRecord.from_dict(source), LOAD_PARSED, ""
    try:
        text = (
            Path(source).read_text(encoding="utf-8")
            if isinstance(source, (str, Path)) and Path(str(source)).is_file()
            else str(source)
        )
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return None, LOAD_MALFORMED, f"could not read evidence JSON: {exc}"
    if not isinstance(data, Mapping):
        return None, LOAD_MALFORMED, "evidence JSON is not an object"
    return EvidenceRecord.from_dict(data), LOAD_PARSED, ""