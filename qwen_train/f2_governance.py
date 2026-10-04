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
    "verify_execution_bundle",
    "verify_governed_pair",
    "GOVERNANCE_LIMITATIONS",
]

BUNDLE_SCHEMA_VERSION = "f2_execution_bundle_v1"

VALID_RESULTS = ("pass", "fail", "error")
VALID_STATES = ("base", "gold")

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

    def verify_ref(self, ref: ArtifactRef) -> tuple[bool, str]:
        """Containment + role + digest + size for one artifact reference."""
        if not ref.name:
            return False, "artifact reference has no name"
        if not _is_sha256(ref.digest):
            return False, f"artifact {ref.name!r} digest is not a SHA-256 hex string"
        if ref.role not in (ROLE_TEST_OUTPUT, ROLE_RUN_LOG):
            return False, f"artifact {ref.name!r} has an invalid role {ref.role!r}"
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
    schema_version: str = BUNDLE_SCHEMA_VERSION

    def artifact_refs(self) -> tuple[ArtifactRef, ArtifactRef]:
        return (self.test_output, self.run_log)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "identity": self.identity.canonical_payload(),
            "declared_result": self.declared_result,
            "result_protocol_id": self.result_protocol_id,
            "test_output": self.test_output.to_dict(),
            "run_log": self.run_log.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ExecutionBundle":
        ident = ExecutionIdentity(
            **{**dict(d.get("identity") or {}), "artifact_identities": tuple(
                (d.get("identity") or {}).get("artifact_identities") or ()
            )}
        )
        return cls(
            identity=ident,
            declared_result=str(d.get("declared_result") or ""),
            result_protocol_id=str(d.get("result_protocol_id") or ""),
            test_output=ArtifactRef.from_dict(d.get("test_output") or {}),
            run_log=ArtifactRef.from_dict(d.get("run_log") or {}),
            started_at=str(d.get("started_at") or ""),
            finished_at=str(d.get("finished_at") or ""),
            schema_version=str(d.get("schema_version") or ""),
        )


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
    """Derive the immutable identity from the execution facts and its artifacts."""
    identities = tuple(
        sorted(
            f"{ref.role}:{ref.name}"
            for ref in (test_output, run_log)
        )
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

    Expects a JSON object ``{"fail_to_pass": {"<nodeid>": "passed"|"failed"|
    "error"}}``. Returns ``pass`` iff every declared FAIL_TO_PASS node passed,
    ``fail`` if any failed or errored, and ``None`` when the artifact cannot
    establish the result.

    This is a REFERENCE deriver: the real F2 evaluator output format is not yet
    authorized, so a bundle naming any unregistered protocol fails closed.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"test-output artifact is not parseable JSON: {exc}"
    if not isinstance(payload, Mapping):
        return None, "test-output artifact is not a JSON object"
    f2p = payload.get("fail_to_pass")
    if not isinstance(f2p, Mapping) or not f2p:
        return None, "test-output artifact has no non-empty 'fail_to_pass' map"
    statuses = {str(v).lower() for v in f2p.values()}
    if not statuses <= {"passed", "failed", "error", "skipped"}:
        return None, f"unrecognised test statuses: {sorted(statuses)}"
    if statuses <= {"passed"}:
        return "pass", "all declared FAIL_TO_PASS nodes passed"
    return "fail", f"FAIL_TO_PASS statuses: {sorted(statuses)}"


register_result_protocol("json_test_report_v1", _derive_json_test_report_v1)


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