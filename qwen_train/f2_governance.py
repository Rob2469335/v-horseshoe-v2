"""F2 evidence governance layer — closes the five S8 governance gaps.

The frozen S8 mechanism (`qwen_train/f2_evidence.py`) answers: *does this record
match its own digests, is it internally consistent, and do the declared results
satisfy the base/gold property?* It deliberately trusts the **declared**
`execution_result` and a **caller-supplied** evaluator allowlist. This module
adds the missing governance layers, following the modern benchmark pattern
(SWE-bench `submit verify` re-grades from retained artifacts; SWE-bench Pro V2
regrades against a pristine image):

  Layer 1  Execution producer  -> immutable ``ExecutionBundle`` + raw artifacts
  Layer 2  Trusted artifact store -> an explicit, contained, digest-verified root
  Layer 3  Authorized evaluator registry -> identity+version+implementation digest
  Layer 4  Immutable execution identity -> canonical digest, no timestamps
  Layer 5  Independent verifier -> DERIVES the result from retained artifacts and
           rejects a producer declaration that disagrees with the evidence.

The five gaps this closes:

1. authorized evaluator/procedure was not a trusted identity  -> Layer 3
2. evaluator implementation identity/digest was not bound     -> Layer 3
3. trusted artifact storage/retention was not established     -> Layer 2
4. immutable execution/run identity was not established       -> Layer 4
5. producer vs independent-verifier authority was not separated -> Layer 5

**JSON saying ``"execution_result": "pass"`` is never sufficient.** If the
retained artifacts cannot independently establish the result, verification fails
with ``SCIENTIFICALLY_INSUFFICIENT`` rather than upgrading the declaration.

S8 remains frozen: this module only *composes* the existing S8 verifier and emits
its results through the existing S8 private-proof mechanism.

No network. No subprocess. No arbitrary execution. Pure read-only verification.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from qwen_train.f2_evidence import (
    ROLE_RUN_LOG,
    ROLE_TEST_OUTPUT,
    STATE_ARTIFACT_DIGEST_MISMATCH,
    STATE_ARTIFACT_ESCAPES_ROOT,
    STATE_ARTIFACT_MISSING,
    STATE_MALFORMED,
    STATE_PROVENANCE_NOT_ESTABLISHED,
    STATE_SCIENTIFICALLY_INSUFFICIENT,
    STATE_UNAUTHORIZED_PROCEDURE,
    STATE_UNKNOWN_SCHEMA,
    ArtifactRef,
    EvidenceVerification,
    build_evidence_record,
    verify_evidence_record,
    verify_task_evidence,
)
# The frozen S8 private-proof token: the ONLY sanctioned way for any verifier to
# emit a verification result. Imported deliberately so this layer cannot invent a
# parallel trust path.
from qwen_train.f2_evidence import _VERIFICATION_PROOF  # noqa: PLC2701

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "EvaluatorAuthorization",
    "EvaluatorRegistry",
    "RetentionPolicy",
    "TrustedArtifactStore",
    "ExecutionIdentity",
    "ExecutionBundle",
    "build_execution_identity",
    "register_result_protocol",
    "registered_result_protocols",
    "derive_result",
    "evaluator_bytes_proven",
    "ROLE_EVALUATOR_IMPLEMENTATION",
    "verify_execution_bundle",
    "verify_governed_pair",
    "SeparationOfDutiesError",
    "SeparationOfDuties",
    "verify_separation_of_duties",
    "GOVERNANCE_LIMITATIONS",
]

BUNDLE_SCHEMA_VERSION = "f2_execution_bundle_v1"

#: Artifact role for the evaluator implementation itself. Deliberately defined
#: HERE, not in the frozen S8 module, so S8's role set is untouched.
ROLE_EVALUATOR_IMPLEMENTATION = "evaluator_implementation"

VALID_RESULTS = ("pass", "fail", "error")
VALID_STATES = ("base", "gold")

class SeparationOfDutiesError(RuntimeError):
    """Raised when a governance record fails the separation requirement."""


@dataclass(frozen=True)
class SeparationOfDuties:
    """Minimum auditable role separation for one run.

    Required by ``EXPERIMENT_J_F2_OPERATOR_HANDOFF.md:655-656``: the operator may
    hold exclusion adjudication only with an explicit, recorded separation. This
    makes that record machine-checkable instead of prose, and fails closed when it
    is absent or when one party would be the sole judge of its own exclusions.
    """

    run_authority: str
    operator: str
    exclusion_adjudicator: str
    independent_regrade: str
    q5_q6_adjudicator: str
    mechanical_exclusion_rules: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_authority": self.run_authority,
            "operator": self.operator,
            "exclusion_adjudicator": self.exclusion_adjudicator,
            "independent_regrade": self.independent_regrade,
            "q5_q6_adjudicator": self.q5_q6_adjudicator,
            "mechanical_exclusion_rules": self.mechanical_exclusion_rules,
        }


def verify_separation_of_duties(
    rec: "SeparationOfDuties | Mapping[str, Any] | None",
) -> tuple[bool, str]:
    """Return ``(ok, detail)``. Fails closed: ``None`` is never acceptable.

    The operator may also serve as exclusion adjudicator ONLY when exclusions are
    decided by the pre-specified mechanical rule set (``F2-IMPL-AUTH-018`` Q5)
    rather than by that person's judgement.
    """
    if rec is None:
        return False, (
            "separation of duties NOT ESTABLISHED -- no governance record supplied"
        )
    if isinstance(rec, Mapping):
        fields = (
            "run_authority",
            "operator",
            "exclusion_adjudicator",
            "independent_regrade",
            "q5_q6_adjudicator",
        )
        missing = [f for f in fields if not str(rec.get(f, "")).strip()]
        if missing:
            return False, f"separation of duties record incomplete: missing {missing}"
        rec = SeparationOfDuties(
            run_authority=str(rec["run_authority"]),
            operator=str(rec["operator"]),
            exclusion_adjudicator=str(rec["exclusion_adjudicator"]),
            independent_regrade=str(rec["independent_regrade"]),
            q5_q6_adjudicator=str(rec["q5_q6_adjudicator"]),
            mechanical_exclusion_rules=bool(
                rec.get("mechanical_exclusion_rules", False)
            ),
        )

    if rec.operator == rec.exclusion_adjudicator and not rec.mechanical_exclusion_rules:
        return False, (
            "separation of duties VIOLATED -- the operator is also the sole exclusion "
            "adjudicator and exclusions are not decided by the pre-specified mechanical "
            "rule set (F2-IMPL-AUTH-018 Q5)"
        )
    if rec.operator == rec.independent_regrade:
        return False, (
            "separation of duties VIOLATED -- the operator also signs the independent "
            "regrade, so no independent verification exists"
        )
    if rec.exclusion_adjudicator == rec.independent_regrade:
        return False, (
            "separation of duties VIOLATED -- one party both adjudicates exclusions and "
            "signs the independent regrade"
        )
    return True, (
        f"separation recorded: operator={rec.operator} "
        f"adjudicator={rec.exclusion_adjudicator} "
        f"regrade={rec.independent_regrade} "
        f"mechanical_rules={rec.mechanical_exclusion_rules}"
    )


#: Honestly recorded limits of what this layer can establish.
GOVERNANCE_LIMITATIONS = {
    "authorized_evaluator": (
        "The evaluator registry is supplied by the caller. WHICH evaluator is "
        "authorized for F2 remains a governance decision, not a code decision."
    ),
    "result_derivation": (
        "Derivation is only possible for a REGISTERED result protocol. The real "
        "F2 evaluator's output format is not yet authorized, so a bundle whose "
        "result_protocol_id is unregistered fails closed."
    ),
    "artifact_store": (
        "A trusted root means this layer knows WHICH namespace it will verify. It "
        "cannot prove the store was not written to by an actor with filesystem "
        "access after the run; that requires operator/OS-level immutability."
    ),
    "run_identity": (
        "Execution identity is derived from content, not issued by a registry. "
        "Two byte-identical execution descriptions share an identity; this is "
        "RECONSTRUCTABLE_IDENTITY, not a registry-issued run id."
    ),
    "reexecution": (
        "The verifier never re-executes. Where artifact semantics cannot establish "
        "the result, the state is SCIENTIFICALLY_INSUFFICIENT / REEXECUTION_REQUIRED."
    ),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _is_sha256(value: str) -> bool:
    s = str(value or "")
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


def _gov_fail(state: str, detail: str) -> EvidenceVerification:
    """Emit a failure through the frozen S8 private-proof mechanism."""
    return EvidenceVerification(state=state, detail=detail, _proof=_VERIFICATION_PROOF)


# ===========================================================================
# Layer 3 - Authorized evaluator registry
# ===========================================================================
@dataclass(frozen=True)
class EvaluatorAuthorization:
    """A complete, machine-checkable evaluator authorization.

    A logical identity or a version alone is insufficient: two builds can share a
    name and version while differing in bytes, so the implementation digest is
    required. ``procedure_id`` and ``protocol_version`` bind the authorized
    procedure, not merely the program.
    """

    evaluator_id: str
    version: str
    implementation_digest: str
    procedure_id: str
    protocol_version: str

    def __post_init__(self) -> None:
        for name in ("evaluator_id", "version", "procedure_id", "protocol_version"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"EvaluatorAuthorization.{name} is required")
        if not _is_sha256(self.implementation_digest):
            raise ValueError(
                "EvaluatorAuthorization.implementation_digest must be a SHA-256 "
                "hex digest of the evaluator implementation (not a timestamp)"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluator_id": self.evaluator_id,
            "version": self.version,
            "implementation_digest": self.implementation_digest,
            "procedure_id": self.procedure_id,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "EvaluatorAuthorization":
        return cls(
            evaluator_id=str(d.get("evaluator_id") or ""),
            version=str(d.get("version") or ""),
            implementation_digest=str(d.get("implementation_digest") or ""),
            procedure_id=str(d.get("procedure_id") or ""),
            protocol_version=str(d.get("protocol_version") or ""),
        )


class EvaluatorRegistry:
    """Fail-closed authorization of evaluator procedures.

    An empty registry authorizes nothing. A candidate is authorized only when
    evaluator_id, version, implementation_digest, procedure_id AND
    protocol_version all match a registered authorization exactly.
    """

    def __init__(self, authorizations: Iterable[EvaluatorAuthorization] = ()) -> None:
        self._by_id: dict[str, list[EvaluatorAuthorization]] = {}
        for auth in authorizations:
            if not isinstance(auth, EvaluatorAuthorization):
                raise TypeError("registry entries must be EvaluatorAuthorization")
            self._by_id.setdefault(auth.evaluator_id, []).append(auth)

    @property
    def is_empty(self) -> bool:
        return not self._by_id

    def authorize(
        self,
        *,
        evaluator_id: str,
        version: str,
        implementation_digest: str,
        procedure_id: str,
        protocol_version: str,
    ) -> tuple[bool, str]:
        if self.is_empty:
            return False, (
                "no evaluator authorization is registered; an empty registry "
                "authorizes nothing"
            )
        candidates = self._by_id.get(str(evaluator_id))
        if not candidates:
            return False, f"evaluator {evaluator_id!r} is not a registered procedure"
        for auth in candidates:
            if (
                auth.version == version
                and auth.implementation_digest == implementation_digest
                and auth.procedure_id == procedure_id
                and auth.protocol_version == protocol_version
            ):
                return True, "authorized"
        # Distinguish the failure modes for the operator.
        if any(a.version == version for a in candidates):
            return False, (
                f"evaluator {evaluator_id!r} version {version!r} is registered but "
                "the implementation digest, procedure id or protocol version does "
                "not match; the implementation bytes differ from the authorized build"
            )
        if any(a.implementation_digest == implementation_digest for a in candidates):
            return False, (
                f"evaluator {evaluator_id!r} implementation matches but version "
                f"{version!r} is not authorized"
            )
        return False, (
            f"evaluator {evaluator_id!r} is registered but no authorization matches "
            "this version + implementation digest + procedure + protocol"
        )

    def authorize_identity(self, identity: "ExecutionIdentity") -> tuple[bool, str]:
        return self.authorize(
            evaluator_id=identity.evaluator_id,
            version=identity.evaluator_version,
            implementation_digest=identity.evaluator_implementation_digest,
            procedure_id=identity.procedure_id,
            protocol_version=identity.protocol_version,
        )

    def as_s8_allowlist(self) -> dict[str, tuple[str, ...]]:
        """Narrow the registry to the frozen S8 ``{id: (versions,)}`` shape.

        Used only as a second, redundant check by the S8 verifier; the rich
        identity check happens in :meth:`authorize`.
        """
        return {
            eid: tuple(sorted({a.version for a in auths}))
            for eid, auths in self._by_id.items()
        }

    @classmethod
    def empty(cls) -> "EvaluatorRegistry":
        return cls(())


# ===========================================================================
# Layer 2 - Trusted artifact store
# ===========================================================================
@dataclass(frozen=True)
class RetentionPolicy:
    """Explicit retention metadata. Governance decides the values."""

    policy_id: str
    min_retention_days: int
    immutable: bool

    def __post_init__(self) -> None:
        if not str(self.policy_id or "").strip():
            raise ValueError("RetentionPolicy.policy_id is required")
        if int(self.min_retention_days) < 0:
            raise ValueError("RetentionPolicy.min_retention_days must be >= 0")


@dataclass(frozen=True)
class TrustedArtifactStore:
    """The single namespace this layer is willing to verify artifacts from.

    Trust here means: the governance layer knows exactly which root it verifies,
    and it will refuse anything that resolves outside it. It does NOT prove the
    root was never written to after the run -- that needs OS/operator-level
    immutability and is recorded as a governance limitation.
    """

    root: Path
    retention: RetentionPolicy

    def resolve(self, name: str) -> tuple[Path | None, str]:
        """Resolve an artifact name safely UNDER the trusted root."""
        raw = str(name or "").strip()
        if not raw:
            return None, "artifact name is empty"
        candidate = Path(raw)
        if candidate.is_absolute() or candidate.drive or raw[0] in ("/", "\\"):
            return None, f"artifact name {raw!r} is an absolute path"
        if any(part == ".." for part in candidate.parts):
            return None, f"artifact name {raw!r} contains a parent-directory component"
        root_resolved = Path(self.root).resolve()
        resolved = (root_resolved / candidate).resolve()
        if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
            return None, f"artifact {raw!r} escapes the trusted artifact root"
        return resolved, ""

    def read_bytes(self, name: str) -> tuple[bytes | None, str]:
        """Read an artifact's bytes through ONE open handle (TOCTOU-safe)."""
        resolved, why = self.resolve(name)
        if resolved is None:
            return None, why
        try:
            with open(resolved, "rb") as fh:
                return fh.read(), ""
        except FileNotFoundError:
            return None, f"artifact not found: {name!r}"
        except OSError as exc:
            return None, f"artifact unreadable: {name!r}: {exc}"

    def verify_ref(
        self,
        ref: ArtifactRef,
        *,
        allowed_roles: tuple[str, ...] = (ROLE_TEST_OUTPUT, ROLE_RUN_LOG),
    ) -> tuple[bool, str]:
        """Containment + role + digest + size for one artifact reference."""
        if not ref.name:
            return False, "artifact reference has no name"
        if not _is_sha256(ref.digest):
            return False, f"artifact {ref.name!r} digest is not a SHA-256 hex string"
        if ref.role not in allowed_roles:
            return False, (
                f"artifact {ref.name!r} has role {ref.role!r}, not one of "
                f"{allowed_roles}"
            )
        resolved, why = self.resolve(ref.name)
        if resolved is None:
            return False, why
        data, why = self.read_bytes(ref.name)
        if data is None:
            return False, why
        actual = _sha256(data)
        if actual != ref.digest:
            return False, (
                f"artifact {ref.name!r} hashes {actual[:12]}… != recorded "
                f"{ref.digest[:12]}…"
            )
        if ref.size_bytes is not None and int(ref.size_bytes) != len(data):
            return False, (
                f"artifact {ref.name!r} size {len(data)} != recorded {ref.size_bytes}"
            )
        return True, ""


def _artifact_state(why: str) -> str:
    """Map a store failure reason onto the frozen S8 state taxonomy."""
    low = why.lower()
    if "escape" in low or "absolute" in low or "parent-directory" in low:
        return STATE_ARTIFACT_ESCAPES_ROOT
    if "not found" in low or "unreadable" in low:
        return STATE_ARTIFACT_MISSING
    if "hashes" in low or "size" in low:
        return STATE_ARTIFACT_DIGEST_MISMATCH
    return STATE_MALFORMED


# ===========================================================================
# Layer 4 - Immutable execution identity
# ===========================================================================
@dataclass(frozen=True)
class ExecutionIdentity:
    """The immutable facts that define a run. Timestamps are NOT included.

    Two byte-identical execution descriptions yield the same digest; changing any
    scientifically meaningful fact changes it.
    """

    instance_id: str
    repository: str
    base_commit: str
    execution_state_identity: str
    execution_state_digest: str
    evaluator_id: str
    evaluator_version: str
    evaluator_implementation_digest: str
    procedure_id: str
    protocol_version: str
    environment_identity: str
    test_command: str
    artifact_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "instance_id",
            "repository",
            "base_commit",
            "execution_state_identity",
            "evaluator_id",
            "evaluator_version",
            "procedure_id",
            "protocol_version",
            "environment_identity",
            "test_command",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"ExecutionIdentity.{name} is required")
        if self.execution_state_identity not in VALID_STATES:
            raise ValueError(
                f"execution_state_identity must be one of {VALID_STATES}"
            )
        if not _is_sha256(self.execution_state_digest):
            raise ValueError("execution_state_digest must be a SHA-256 hex digest")
        if not _is_sha256(self.evaluator_implementation_digest):
            raise ValueError(
                "evaluator_implementation_digest must be a SHA-256 hex digest"
            )
        if tuple(sorted(self.artifact_identities)) != tuple(self.artifact_identities):
            raise ValueError("artifact_identities must be stored sorted")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "execution_state_identity": self.execution_state_identity,
            "execution_state_digest": self.execution_state_digest,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "evaluator_implementation_digest": self.evaluator_implementation_digest,
            "procedure_id": self.procedure_id,
            "protocol_version": self.protocol_version,
            "environment_identity": self.environment_identity,
            "test_command": self.test_command,
            "artifact_identities": list(self.artifact_identities),
        }

    def digest(self) -> str:
        return _sha256(_canonical_bytes(self.canonical_payload()))


# ===========================================================================
# Layer 1 - Execution bundle (producer output)
# ===========================================================================
@dataclass(frozen=True)
class ExecutionBundle:
    """Immutable producer output. A raw, UNTRUSTED object.

    It may declare a result; that declaration is an input to verification, never
    a substitute for it.
    """

    identity: ExecutionIdentity
    declared_result: str
    result_protocol_id: str
    test_output: ArtifactRef
    run_log: ArtifactRef
    started_at: str
    finished_at: str
    #: OPTIONAL. When supplied, the evaluator implementation BYTES are present in
    #: the trusted store and are independently hashed and compared against
    #: ``identity.evaluator_implementation_digest``. Without it, evaluator-byte
    #: provenance is NOT ESTABLISHED (the registry check only proves that a
    #: declared identity matches an authorized entry -- not that those bytes were
    #: the ones that ran).
    implementation_artifact: ArtifactRef | None = None
    schema_version: str = BUNDLE_SCHEMA_VERSION

    def artifact_refs(self) -> tuple[ArtifactRef, ArtifactRef]:
        return (self.test_output, self.run_log)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "schema_version": self.schema_version,
            "identity": self.identity.canonical_payload(),
            "declared_result": self.declared_result,
            "result_protocol_id": self.result_protocol_id,
            "test_output": self.test_output.to_dict(),
            "run_log": self.run_log.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if self.implementation_artifact is not None:
            d["implementation_artifact"] = self.implementation_artifact.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ExecutionBundle":
        ident = ExecutionIdentity(
            **{**dict(d.get("identity") or {}), "artifact_identities": tuple(
                (d.get("identity") or {}).get("artifact_identities") or ()
            )}
        )
        impl = d.get("implementation_artifact")
        return cls(
            identity=ident,
            declared_result=str(d.get("declared_result") or ""),
            result_protocol_id=str(d.get("result_protocol_id") or ""),
            test_output=ArtifactRef.from_dict(d.get("test_output") or {}),
            run_log=ArtifactRef.from_dict(d.get("run_log") or {}),
            started_at=str(d.get("started_at") or ""),
            finished_at=str(d.get("finished_at") or ""),
            implementation_artifact=(
                ArtifactRef.from_dict(impl) if impl is not None else None
            ),
            schema_version=str(d.get("schema_version") or ""),
        )


def _artifact_identity(ref: ArtifactRef) -> str:
    """Canonical descriptor for ONE artifact, bound into the execution identity.

    Binds the artifact's CRYPTOGRAPHIC identity (role, name, digest, size), not
    merely its pathname: mutating artifact bytes without changing the name MUST
    change the execution identity digest.
    """
    size = "" if ref.size_bytes is None else str(int(ref.size_bytes))
    return f"{ref.role}|{ref.name}|{ref.digest}|{size}"


def build_execution_identity(
    *,
    instance_id: str,
    repository: str,
    base_commit: str,
    execution_state_identity: str,
    execution_state_digest: str,
    evaluator: EvaluatorAuthorization,
    environment_identity: str,
    test_command: str,
    test_output: ArtifactRef,
    run_log: ArtifactRef,
) -> ExecutionIdentity:
    """Derive the immutable identity from the execution facts and its artifacts.

    ``artifact_identities`` binds role + name + digest + size for every artifact,
    deterministically ordered. Identity binds *what artifacts were intended*;
    :meth:`TrustedArtifactStore.verify_ref` proves *what bytes were actually
    retained*. The two are complementary, not redundant.
    """
    identities = tuple(
        sorted(_artifact_identity(ref) for ref in (test_output, run_log))
    )
    return ExecutionIdentity(
        instance_id=instance_id,
        repository=repository,
        base_commit=base_commit,
        execution_state_identity=execution_state_identity,
        execution_state_digest=execution_state_digest,
        evaluator_id=evaluator.evaluator_id,
        evaluator_version=evaluator.version,
        evaluator_implementation_digest=evaluator.implementation_digest,
        procedure_id=evaluator.procedure_id,
        protocol_version=evaluator.protocol_version,
        environment_identity=environment_identity,
        test_command=test_command,
        artifact_identities=identities,
    )


# ===========================================================================
# Result derivation - independent of the producer's declaration
# ===========================================================================
_DERIVERS: dict[str, Callable[[bytes, Mapping[str, Any]], tuple[str | None, str]]] = {}


def register_result_protocol(
    protocol_id: str,
    deriver: Callable[[bytes, Mapping[str, Any]], tuple[str | None, str]],
) -> None:
    """Register a result-derivation protocol.

    A deriver receives ``(artifact_bytes, context)`` and returns
    ``(result|None, detail)`` where result is ``pass``/``fail`` or None when the
    bytes cannot establish the result.
    """
    if not str(protocol_id or "").strip():
        raise ValueError("protocol_id is required")
    _DERIVERS[protocol_id] = deriver


def registered_result_protocols() -> tuple[str, ...]:
    return tuple(sorted(_DERIVERS))


def _derive_json_test_report_v1(
    data: bytes, context: Mapping[str, Any]
) -> tuple[str | None, str]:
    """REFERENCE protocol ``json_test_report_v1``.

    Retained for compatibility with bundles produced before the authoritative
    evaluator existed. The AUTHORITATIVE protocol is ``f2_evaluator_report_v1``
    (registered just below), which is what the F2 execution path emits and what
    ``derive_result`` should be pointed at.

    The verdict itself is delegated to
    ``qwen_train.f2_evaluator.verdict_from_report_payload`` rather than being
    re-derived here. This function used to carry its own status set, which had
    drifted from the evaluator's -- it rejected ``missing``, a status the
    evaluator legitimately emits -- so a valid report could be refused as an
    unrecognised status instead of being scored.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"test-output artifact is not parseable JSON: {exc}"
    from qwen_train.f2_evaluator import verdict_from_report_payload

    return verdict_from_report_payload(payload)


register_result_protocol("json_test_report_v1", _derive_json_test_report_v1)


def _register_authoritative_evaluator_protocol() -> None:
    """Register the AUTHORITATIVE F2 result protocol.

    ``json_test_report_v1`` above is a REFERENCE deriver kept for the existing
    governance tests. The protocol registered here is the one the F2 execution
    path actually uses: ``qwen_train.f2_evaluator`` is a real producer that
    turns retained pytest/JUnit evidence plus the declared FAIL_TO_PASS /
    PASS_TO_PASS contract into a deterministic report, and this deriver re-derives
    the verdict from that retained report instead of trusting any producer
    declaration.

    Registration is lazy and failure-tolerant only in the sense that an import
    error must not leave a half-registered protocol behind: a missing evaluator
    means the protocol is absent, and an absent protocol already fails closed in
    ``derive_result``.
    """
    try:
        from qwen_train import f2_evaluator
    except Exception:  # pragma: no cover - evaluator is a hard dependency
        return
    register_result_protocol(
        f2_evaluator.RESULT_PROTOCOL_ID, f2_evaluator.derive_result_protocol
    )


_register_authoritative_evaluator_protocol()


def derive_result(
    bundle: ExecutionBundle, store: TrustedArtifactStore
) -> tuple[str | None, str]:
    """Derive the execution result from retained artifacts.

    Returns ``(result|None, detail)``. ``None`` means the retained evidence
    cannot establish the result, which is a fail-closed outcome, not a pass.
    """
    protocol = _DERIVERS.get(bundle.result_protocol_id)
    if protocol is None:
        return None, (
            f"no result-derivation protocol registered for "
            f"{bundle.result_protocol_id!r} (registered: "
            f"{list(registered_result_protocols())}); the declared result cannot "
            "be independently established"
        )
    data, why = store.read_bytes(bundle.test_output.name)
    if data is None:
        return None, f"cannot read test-output artifact: {why}"
    context = {
        "instance_id": bundle.identity.instance_id,
        "execution_state_identity": bundle.identity.execution_state_identity,
    }
    return protocol(data, context)


# ===========================================================================
# Layer 5 - Independent verifier
# ===========================================================================
def _to_s8_record(bundle: ExecutionBundle, derived_result: str):
    """Build the frozen-S8 record from a VERIFIED bundle.

    The execution identity digest becomes S8's ``execution_state_digest``, and
    S8's ``execution_result`` is the DERIVED result -- never the declaration.
    """
    return build_evidence_record(
        instance_id=bundle.identity.instance_id,
        repository=bundle.identity.repository,
        base_commit=bundle.identity.base_commit,
        execution_state_identity=bundle.identity.execution_state_identity,
        execution_state_digest=bundle.identity.digest(),
        test_command=bundle.identity.test_command,
        environment_identity=bundle.identity.environment_identity,
        evaluator_identity=bundle.identity.evaluator_id,
        evaluator_version=bundle.identity.evaluator_version,
        execution_started_at=bundle.started_at,
        execution_finished_at=bundle.finished_at,
        execution_result=derived_result,
        test_output_artifact=bundle.test_output,
        run_log_artifact=bundle.run_log,
    )


def evaluator_bytes_proven(
    bundle: ExecutionBundle, store: TrustedArtifactStore
) -> bool:
    """True only when the evaluator implementation BYTES are independently proven.

    This is a strictly separate claim from registry authorization. Registry
    authorization proves *a declared identity matches an authorized entry*; it
    does NOT prove that those bytes were the ones that ran. Byte provenance
    requires the implementation artifact to be present in the trusted store and
    to hash to the authorized implementation digest. When it is absent, the
    answer is ``False`` -- NOT ESTABLISHED, never assumed.
    """
    if bundle is None or bundle.implementation_artifact is None:
        return False
    impl = bundle.implementation_artifact
    ok, _ = store.verify_ref(impl, allowed_roles=(ROLE_EVALUATOR_IMPLEMENTATION,))
    return bool(ok and impl.digest == bundle.identity.evaluator_implementation_digest)


def verify_execution_bundle(
    bundle: ExecutionBundle | Mapping[str, Any] | None,
    *,
    store: TrustedArtifactStore,
    registry: EvaluatorRegistry,
) -> EvidenceVerification:
    """Independently verify ONE execution bundle.

    Order: schema -> evaluator authorization -> artifact containment/integrity ->
    result DERIVATION -> declaration comparison -> S8 final proof.
    """
    if bundle is None:
        return _gov_fail(STATE_PROVENANCE_NOT_ESTABLISHED, "no execution bundle supplied")
    if isinstance(bundle, Mapping):
        try:
            bundle = ExecutionBundle.from_dict(bundle)
        except Exception as exc:  # noqa: BLE001
            return _gov_fail(STATE_MALFORMED, f"bundle could not be parsed: {exc}")
    if not isinstance(bundle, ExecutionBundle):
        return _gov_fail(STATE_MALFORMED, f"unsupported bundle type: {type(bundle).__name__}")

    if bundle.schema_version != BUNDLE_SCHEMA_VERSION:
        return _gov_fail(
            STATE_UNKNOWN_SCHEMA,
            f"unknown bundle schema_version {bundle.schema_version!r} "
            f"(expected {BUNDLE_SCHEMA_VERSION!r})",
        )
    if bundle.declared_result not in VALID_RESULTS:
        return _gov_fail(
            STATE_MALFORMED,
            f"declared_result must be one of {VALID_RESULTS}, "
            f"got {bundle.declared_result!r}",
        )

    # Layer 3: evaluator authorization (fail closed).
    ok, why = registry.authorize_identity(bundle.identity)
    if not ok:
        return _gov_fail(STATE_UNAUTHORIZED_PROCEDURE, why)

    # Layer 2: trusted artifact store - containment, role, digest, size.
    for ref in bundle.artifact_refs():
        ok, why = store.verify_ref(ref)
        if not ok:
            return _gov_fail(_artifact_state(why), why)

    # Layer 3b: evaluator implementation BYTES, when the producer supplies them.
    # Absence is NOT an error -- it means byte provenance is NOT ESTABLISHED
    # (see evaluator_bytes_proven); the registry check above still stands.
    if bundle.implementation_artifact is not None:
        impl = bundle.implementation_artifact
        ok, why = store.verify_ref(
            impl, allowed_roles=(ROLE_EVALUATOR_IMPLEMENTATION,)
        )
        if not ok:
            return _gov_fail(
                _artifact_state(why), f"evaluator implementation artifact: {why}"
            )
        if impl.digest != bundle.identity.evaluator_implementation_digest:
            return _gov_fail(
                STATE_UNAUTHORIZED_PROCEDURE,
                "the retained evaluator implementation bytes do not match the "
                "authorized implementation digest in the execution identity",
            )

    # Layer 5: DERIVE the result from retained evidence. Never trust the JSON.
    derived, why = derive_result(bundle, store)
    if derived is None:
        return _gov_fail(
            STATE_SCIENTIFICALLY_INSUFFICIENT,
            f"retained evidence cannot establish the result: {why}",
        )
    if derived != bundle.declared_result:
        return _gov_fail(
            STATE_SCIENTIFICALLY_INSUFFICIENT,
            f"producer declared {bundle.declared_result!r} but the retained "
            f"evidence establishes {derived!r}; the declaration is rejected",
        )

    # Frozen S8 final proof, with the DERIVED result.
    record = _to_s8_record(bundle, derived)
    return verify_evidence_record(
        record,
        artifact_root=store.root,
        authorized_evaluators=registry.as_s8_allowlist(),
    )


def verify_governed_pair(
    base_bundle: ExecutionBundle | Mapping[str, Any] | None,
    gold_bundle: ExecutionBundle | Mapping[str, Any] | None,
    *,
    store: TrustedArtifactStore,
    registry: EvaluatorRegistry,
    task_id: str,
    repository: str,
    base_commit: str,
) -> EvidenceVerification:
    """Independently verify a base/gold PAIR and let frozen S8 emit the proof."""
    if base_bundle is None and gold_bundle is None:
        return _gov_fail(STATE_PROVENANCE_NOT_ESTABLISHED, "no base or gold bundle supplied")
    if base_bundle is None:
        return _gov_fail(STATE_PROVENANCE_NOT_ESTABLISHED, "base bundle missing")
    if gold_bundle is None:
        return _gov_fail(STATE_PROVENANCE_NOT_ESTABLISHED, "gold bundle missing")

    bv = verify_execution_bundle(base_bundle, store=store, registry=registry)
    if not bv.ok:
        return _gov_fail(bv.state, f"base: {bv.detail}")
    gv = verify_execution_bundle(gold_bundle, store=store, registry=registry)
    if not gv.ok:
        return _gov_fail(gv.state, f"gold: {gv.detail}")

    b = base_bundle if isinstance(base_bundle, ExecutionBundle) else ExecutionBundle.from_dict(base_bundle)
    g = gold_bundle if isinstance(gold_bundle, ExecutionBundle) else ExecutionBundle.from_dict(gold_bundle)
    b_derived, _ = derive_result(b, store)
    g_derived, _ = derive_result(g, store)

    return verify_task_evidence(
        task_id=task_id,
        repository=repository,
        base_commit=base_commit,
        base_evidence=_to_s8_record(b, b_derived),
        gold_evidence=_to_s8_record(g, g_derived),
        artifact_root=store.root,
        authorized_evaluators=registry.as_s8_allowlist(),
    )