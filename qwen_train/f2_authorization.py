"""Experiment J / F2 — signed authorization-record verifier (fail-closed).

Strongest *practical* control for this repo: an owner-signed, canonically
serialised, versioned authorization record, verified against an owner-controlled
trust root, enforced at one central point.

SECURELY DISABLED BY DESIGN: if no trust root is provisioned (the normal state
until the owner signs), every stage is denied. This module never accepts an
unsigned or self-declared record, never treats ``status`` as authorization, and
never invents a key. Canonical JSON (sorted keys, no whitespace) + SHA-256 binds
the signed bytes so semantically identical records cannot acquire ambiguous
signatures.

This module performs verification (and durable single-use consumption via the
separate ``f2_authz_ledger`` module). It does NOT acquire populations, admit
units, or execute any F2 stage.

Binding & replay: a record is only accepted for a protected operation when
every identity that operation requires is bound in the record AND matches the
independently obtained expected value (repository revision from git; artifact
digests recomputed by the entry point; experiment/protocol/arm from the
verified manifest at the lowest boundary). ``verify_and_consume`` then records
one durable consumption per (record digest, execution-binding digest); see
``f2_authz_ledger`` for the exact threat model, including what a local
administrator could still undo.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

SCHEMA = "f2-authorization/v1"
STAGES = ("POP-001", "EXPL-001", "CONF-001")
TRUST_ROOT_ENV = "F2_AUTHORIZATION_TRUST_ROOT"
DEFAULT_TRUST_ROOT = Path(__file__).with_name("authorization") / "trust_root.json"

#: Optional digest pin for the trust-root file. This is PROCESS-CONTROLLED
#: configuration (any principal that can set the environment can set the pin);
#: it is a consistency check, NOT independent key custody.
TRUST_ROOT_DIGEST_ENV = "F2_AUTHORIZATION_TRUST_ROOT_SHA256"

#: Operations each stage may authorise. A record can never authorise an
#: operation outside its own stage (scope escalation is rejected).
STAGE_OPERATIONS = {
    "POP-001": frozenset({"acquire", "register"}),
    "EXPL-001": frozenset({"exploratory_run"}),
    "CONF-001": frozenset({"confirmatory_run"}),
}

# An arm execution has exactly one allowed operation per executable stage.  Keep
# this mapping separate from ``STAGE_OPERATIONS``: POP-001 legitimately has two
# operations but is not an arm-execution stage.
EXECUTION_STAGE_OPERATIONS = {
    "EXPL-001": "exploratory_run",
    "CONF-001": "confirmatory_run",
}

# ---------------------------------------------------------------------------
# Execution-binding contract (strict identity binding)
# ---------------------------------------------------------------------------

#: Canonical names of the identities a signed record may carry and a protected
#: operation may require. A binding may never contain an unknown field name (a
#: misspelling must fail, not silently weaken enforcement).
BINDING_FIELDS = (
    "protocol_digest",
    "population_digest",
    "population_manifest_digest",
    "repository_revision",
    "experiment_id",
    "protocol_version",
    "run_id",
    "rollout_id",
    "arm",
)

#: A binding document additionally carries the stage/operation it is bound to.
BINDING_WITH_STAGE = tuple(BINDING_FIELDS) + ("stage", "operation")

#: Identities each protected operation REQUIRES the signed record to bind.
#: Expected values are obtained from authoritative sources - never from the
#: caller's assertion alone:
#:   * ``repository_revision`` - derived HERE via ``git rev-parse HEAD``
#:     (caller-supplied values are compared, never trusted);
#:   * ``population_manifest_digest`` - recomputed by the protected entry point
#:     from the exact artifact bytes it is about to consume;
#:   * ``experiment_id`` / ``protocol_version`` - taken at the lowest boundary
#:     from the independently VERIFIED frozen arm manifest.
#: A record missing any required binding, or carrying a mismatching one, is
#: denied before any protected side effect.
OPERATION_REQUIRED_BINDINGS: dict[tuple[str, str], frozenset[str]] = {
    ("POP-001", "acquire"): frozenset({"repository_revision"}),
    ("POP-001", "register"): frozenset(
        {"repository_revision", "population_manifest_digest"}
    ),
    ("EXPL-001", "exploratory_run"): frozenset(
        {"repository_revision", "experiment_id", "protocol_version"}
    ),
    ("CONF-001", "confirmatory_run"): frozenset(
        {"repository_revision", "experiment_id", "protocol_version"}
    ),
}

_REQUIRED = (
    "id", "schema", "stage", "operations", "approver",
    "not_before", "not_after", "protocol_digest",
)


class AuthorizationError(RuntimeError):
    """Fail-closed: any missing, invalid, or unverifiable authorization."""


def validate_execution_stage_operation(stage: str, operation: str) -> None:
    """Reject a non-executable or mismatched arm-execution authorization pair."""
    expected = EXECUTION_STAGE_OPERATIONS.get(stage)
    if expected is None:
        raise AuthorizationError(f"unsupported execution stage: {stage!r}")
    if operation != expected:
        raise AuthorizationError(
            f"stage/operation mismatch: {stage!r} requires {expected!r}, "
            f"got {operation!r}"
        )


def canonical_bytes(record: Mapping[str, Any]) -> bytes:
    """Deterministic serialisation of the signed portion of a record."""
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def record_digest(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(record)).hexdigest()


# ---------------------------------------------------------------------------
# Repository identity (authoritative; never caller-supplied)
# ---------------------------------------------------------------------------

def repository_identity(root: Path | str | None = None) -> dict[str, Any]:
    """Return ``{"revision": <git HEAD sha>, "dirty": <bool>}`` for the repo.

    Behaviour by environment (all explicit, none silent):

    * normal clone / ``git worktree`` / detached HEAD: ``git rev-parse HEAD``
      returns the commit that tree is materialised at, so the revision binds
      exactly the checkout that is executing;
    * dirty working tree (tracked modifications staged or not): ``dirty`` is
      ``True``; a protected operation that requires the repository-revision
      binding FAILS CLOSED on a dirty tree, because the executing code cannot
      be proven to equal the bound commit;
    * git binary or metadata unavailable (source archive, stripped checkout):
      raises ``AuthorizationError`` (fail-closed) - the revision cannot be
      established, so no protected operation proceeds.

    The revision is derived here from git itself; a caller-supplied revision is
    only ever compared against this value, never trusted on its own.
    """
    import subprocess

    repo = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    try:
        rev = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthorizationError(
            f"git unavailable; repository revision cannot be established "
            f"({exc}) - fail-closed: protected operation denied"
        ) from exc
    if rev.returncode != 0:
        raise AuthorizationError(
            "repository revision cannot be established (git rev-parse HEAD "
            "failed; not a git checkout?) - fail-closed: protected operation "
            "denied"
        )
    revision = rev.stdout.strip()
    if not revision or len(revision) != 40:
        raise AuthorizationError(
            f"git returned an implausible revision {revision!r} - fail-closed"
        )
    st = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True, timeout=60,
    )
    if st.returncode != 0:
        raise AuthorizationError(
            "working-tree state cannot be established (git status failed) - "
            "fail-closed: protected operation denied"
        )
    return {"revision": revision, "dirty": bool(st.stdout.strip())}


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise AuthorizationError(f"duplicate JSON key rejected: {k!r}")
        out[k] = v
    return out


def _load_json_strict(path: Path) -> Any:
    """Parse JSON rejecting duplicate keys (ambiguous records are refused)."""
    try:
        return json.loads(path.read_text("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except AuthorizationError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise AuthorizationError(f"unreadable/unparseable: {path} ({exc})")


def _trust_root_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.environ.get(TRUST_ROOT_ENV) or DEFAULT_TRUST_ROOT)


def trust_root_digest(path: Path | None = None) -> str:
    """SHA-256 of the trust-root file bytes (for out-of-band fingerprinting)."""
    p = _trust_root_path(path)
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError as exc:
        raise AuthorizationError(f"trust root unreadable: {p} ({exc})") from exc


def _decode_ed25519_public(kid: str, value: Any) -> tuple[bytes, int | None]:
    """Validate one signer entry; return (32-byte raw public key, epoch|None)."""
    epoch: int | None = None
    if isinstance(value, str):
        b64 = value
    elif isinstance(value, dict):
        b64 = value.get("public_key")
        raw_epoch = value.get("epoch")
        if raw_epoch is not None:
            if not isinstance(raw_epoch, int) or isinstance(raw_epoch, bool) or raw_epoch < 0:
                raise AuthorizationError(
                    f"trust root signer {kid!r} has invalid key epoch {raw_epoch!r}"
                )
            epoch = raw_epoch
    else:
        raise AuthorizationError(f"trust root signer {kid!r} has invalid key entry")
    if not isinstance(b64, str) or not b64:
        raise AuthorizationError(f"trust root signer {kid!r} has no key")
    try:
        import base64 as _b64

        raw = _b64.b64decode(b64, validate=True)
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise AuthorizationError(
            f"trust root signer {kid!r} key is not valid base64 ({exc})"
        ) from exc
    if len(raw) != 32:
        raise AuthorizationError(
            f"trust root signer {kid!r} key is {len(raw)} bytes, expected a "
            "32-byte ed25519 public key"
        )
    return raw, epoch


def load_trust_root(
    path: Path | None = None,
) -> tuple[Mapping[str, str], frozenset[str], Mapping[str, int]]:
    """Return ``(signers, revoked_key_ids, key_epochs)``. Absent root => deny.

    * ``signers`` maps ``key_id -> base64(ed25519 raw public key)`` (kept as the
      historical string form so existing callers keep working).
    * ``revoked_key_ids`` comes from the root's optional ``revoked_key_ids``
      list; a signature from a listed key is denied even if the key is still
      present in ``signers`` (the "revoked but not yet removed" state).
    * ``key_epochs`` maps ``key_id -> epoch`` for signer entries that declare
      one (dict form ``{"public_key": ..., "epoch": n}``).
    """
    p = _trust_root_path(path)
    if not p.exists():
        raise AuthorizationError(
            f"no authorization trust root provisioned at {p}; all stages denied"
        )
    root = _load_json_strict(p)
    if not isinstance(root, dict):
        raise AuthorizationError("trust root is not an object")
    signers_raw = root.get("signers")
    if not isinstance(signers_raw, dict) or not signers_raw:
        raise AuthorizationError("trust root contains no signers")
    revoked_raw = root.get("revoked_key_ids", [])
    if not isinstance(revoked_raw, list) or not all(
        isinstance(k, str) and k for k in revoked_raw
    ):
        raise AuthorizationError("trust root revoked_key_ids must be a list of key ids")
    signers: dict[str, str] = {}
    epochs: dict[str, int] = {}
    for kid, value in signers_raw.items():
        if not isinstance(kid, str) or not kid:
            raise AuthorizationError("trust root signer key id must be a non-empty string")
        raw, epoch = _decode_ed25519_public(kid, value)
        import base64 as _b64

        signers[kid] = _b64.b64encode(raw).decode("ascii")
        if epoch is not None:
            epochs[kid] = epoch
    # Optional environment digest pin (process-controlled consistency check).
    pinned = os.environ.get(TRUST_ROOT_DIGEST_ENV, "").strip()
    if pinned:
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual.lower() != pinned.lower():
            raise AuthorizationError(
                f"trust root digest mismatch (pinned {pinned[:12]}…, actual "
                f"{actual[:12]}…) - fail-closed"
            )
    return signers, frozenset(revoked_raw), epochs


def describe_trust_root(path: Path | None = None) -> Mapping[str, Any]:
    """Public trust-root identity for OUT-OF-BAND owner verification.

    Returns only public material (key ids, epochs, file digest) so the owner
    can compare fingerprints through a channel independent of this process.
    It never returns private key material (a trust root holds none).
    """
    signers, revoked, epochs = load_trust_root(path)
    return {
        "path": str(_trust_root_path(path)),
        "digest_sha256": trust_root_digest(path),
        "signer_key_ids": sorted(signers),
        "key_epochs": dict(sorted(epochs.items())),
        "revoked_key_ids": sorted(revoked),
    }


def _parse_ts(value: str) -> _dt.datetime:
    ts = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise AuthorizationError(f"timestamp lacks timezone: {value!r}")
    return ts


def verify_authorization(
    record_path: str | Path,
    *,
    stage: str,
    operation: str,
    protocol_digest: str | None = None,
    population_digest: str | None = None,
    now: _dt.datetime | None = None,
    trust_root: str | Path | None = None,
    revoked_ids: Iterable[str] = (),
    binding: Mapping[str, Any] | None = None,
    repository_root: Path | str | None = None,
) -> Mapping[str, Any]:
    """Verify a signed record authorises ``operation`` at ``stage``. Fail-closed.

    Binding contract: every identity the operation requires
    (``OPERATION_REQUIRED_BINDINGS``) MUST be present in the record and MUST
    equal the independently obtained expected value. Caller-supplied values in
    ``binding`` are expectations to compare, never sources of truth: the
    repository revision is derived from git here, and a protected operation is
    denied when the working tree is dirty (the executing code cannot be proven
    to equal the bound commit).
    """
    if stage not in STAGES:
        raise AuthorizationError(f"unknown stage: {stage!r}")
    if not isinstance(operation, str) or not operation:
        raise AuthorizationError(f"invalid operation: {operation!r}")
    p = Path(str(record_path).strip()) if not isinstance(record_path, Path) else record_path
    if "\x00" in str(p):
        raise AuthorizationError("authorization record path contains a NUL byte")
    if p.is_dir():
        raise AuthorizationError(f"authorization record path is a directory: {p}")
    if not p.exists():
        raise AuthorizationError(f"authorization record not found: {p}")
    doc = _load_json_strict(p)
    if not isinstance(doc, dict):
        raise AuthorizationError("authorization record is not an object")
    record = doc.get("record")
    sig = doc.get("signature")
    if not isinstance(record, dict) or not isinstance(sig, dict):
        raise AuthorizationError("malformed authorization record (record/signature)")

    for field in _REQUIRED:
        if field not in record:
            raise AuthorizationError(f"missing required field: {field}")
    if record["schema"] != SCHEMA:
        raise AuthorizationError(f"unsupported schema: {record['schema']!r}")
    if record["stage"] != stage:
        raise AuthorizationError(
            f"stage mismatch: record {record['stage']!r} != required {stage!r}"
        )
    ops = record["operations"]
    if not isinstance(ops, list) or not all(isinstance(o, str) for o in ops):
        raise AuthorizationError("operations must be a list of strings")
    if len(set(ops)) != len(ops):
        raise AuthorizationError("duplicate operations in scope")
    if operation not in ops:
        raise AuthorizationError(
            f"operation {operation!r} not in authorised scope {sorted(ops)!r}"
        )
    # Scope can never exceed the stage's own operation set.
    if not set(ops).issubset(STAGE_OPERATIONS[stage]):
        raise AuthorizationError(
            f"scope escalation: {sorted(set(ops) - STAGE_OPERATIONS[stage])!r} "
            f"is outside stage {stage}"
        )

    rid = record["id"]
    if not isinstance(rid, str) or not rid:
        raise AuthorizationError("authorization record id must be a non-empty string")
    if rid in set(revoked_ids) or record.get("revoked") is True:
        raise AuthorizationError(f"authorization {rid!r} is revoked")
    if record.get("superseded_by"):
        raise AuthorizationError(
            f"authorization {rid!r} superseded by {record['superseded_by']!r}"
        )

    ts = now or _dt.datetime.now(_dt.timezone.utc)
    nb, na = _parse_ts(record["not_before"]), _parse_ts(record["not_after"])
    if not (nb <= ts <= na):
        raise AuthorizationError(
            f"authorization {rid!r} outside validity window [{nb}, {na}]"
        )

    if protocol_digest is not None and record.get("protocol_digest") != protocol_digest:
        raise AuthorizationError("protocol digest mismatch")
    if population_digest is not None and record.get("population_digest") != population_digest:
        raise AuthorizationError("population digest mismatch")

    # ---- strict execution binding (fail-closed on missing/mismatch) ------
    supplied = dict(binding or {})
    unknown = sorted(set(supplied) - set(BINDING_FIELDS))
    if unknown:
        raise AuthorizationError(f"unknown execution-binding fields: {unknown!r}")
    required = set(OPERATION_REQUIRED_BINDINGS.get((stage, operation), frozenset()))
    _check_binding(record, required, supplied, repository_root)

    # ---- signature verification against the owner-controlled trust root ----
    signers, root_revoked, key_epochs = load_trust_root(trust_root)
    kid = sig.get("key_id")
    if not isinstance(kid, str) or kid not in signers:
        raise AuthorizationError(f"untrusted signer: {kid!r}")
    if kid in root_revoked:
        raise AuthorizationError(f"signer key {kid!r} is revoked in the trust root")
    if sig.get("alg") != "ed25519":
        raise AuthorizationError(f"unsupported signature alg: {sig.get('alg')!r}")
    if sig.get("signer") != record.get("approver"):
        raise AuthorizationError("approver/signer identity mismatch")
    if kid in key_epochs:
        if sig.get("key_epoch") != key_epochs[kid]:
            raise AuthorizationError(
                f"signer key epoch mismatch for {kid!r}: record has "
                f"{sig.get('key_epoch')!r}, trust root declares {key_epochs[kid]!r}"
            )
    raw_sig = sig.get("sig")
    if not isinstance(raw_sig, str) or not raw_sig:
        raise AuthorizationError("missing signature bytes")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(signers[kid]))
        pub.verify(base64.b64decode(raw_sig), canonical_bytes(record))
    except InvalidSignature:
        raise AuthorizationError("invalid signature")
    except AuthorizationError:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure is fail-closed
        raise AuthorizationError(f"signature verification failed: {exc}")
    return record


def _check_binding(
    record: Mapping[str, Any],
    required: set[str],
    supplied: Mapping[str, Any],
    repository_root: Path | str | None,
) -> None:
    """Enforce required bindings. Expected values come from authoritative
    sources: the repository revision is derived from git; every other expected
    value must be supplied by the protected entry point (which recomputes it
    from the artifact it is about to consume) and is compared, never trusted."""
    needs_revision = (
        "repository_revision" in required
        or "repository_revision" in record
        or "repository_revision" in supplied
    )
    repo_ident: dict[str, Any] | None = None
    if needs_revision:
        repo_ident = repository_identity(repository_root)
        expected_rev = repo_ident["revision"]
        if "repository_revision" in required and "repository_revision" not in record:
            raise AuthorizationError(
                "authorization record does not bind the repository revision "
                "(required binding missing) - fail-closed"
            )
        for source, value in (
            ("record", record.get("repository_revision")),
            ("execution binding", supplied.get("repository_revision")),
        ):
            if value is not None and value != expected_rev:
                raise AuthorizationError(
                    f"repository revision mismatch ({source}): bound {value!r} != "
                    f"executing {expected_rev!r}"
                )
        if required and repo_ident["dirty"]:
            raise AuthorizationError(
                "working tree is dirty; a protected operation may only run from "
                "a checkout whose code equals the bound commit - commit or "
                "stash first (fail-closed)"
            )
    for name in sorted(required - {"repository_revision"}):
        if name not in record:
            raise AuthorizationError(
                f"authorization record does not bind {name} "
                "(required binding missing) - fail-closed"
            )
        expected = supplied.get(name)
        if expected is None:
            raise AuthorizationError(
                f"no independently obtained expected value for required "
                f"binding {name!r} - fail-closed"
            )
        if record[name] != expected:
            raise AuthorizationError(
                f"{name} mismatch: record binds {record[name]!r}, execution "
                f"requires {expected!r}"
            )
    # Any optional binding present BOTH in record and expectation must agree.
    for name in sorted(set(record) & set(supplied) - required):
        if name in BINDING_FIELDS and record[name] != supplied[name]:
            raise AuthorizationError(
                f"{name} mismatch: record binds {record[name]!r}, execution "
                f"requires {supplied[name]!r}"
            )


def require_authorized(*, stage: str, operation: str, **kwargs: Any) -> Mapping[str, Any]:
    """Central guard. Production paths MUST call this before a protected op.

    Always fail-closed: with no trust root, this raises for every stage.
    """
    record_path = kwargs.pop("record_path", None)
    if record_path is None:
        record_path = os.environ.get("F2_AUTHORIZATION_RECORD", "")
    if record_path is None or (isinstance(record_path, str) and not record_path.strip()):
        raise AuthorizationError(
            "no authorization record configured (F2_AUTHORIZATION_RECORD unset or "
            "empty) - fail-closed: protected operation denied"
        )
    return verify_authorization(record_path, stage=stage, operation=operation, **kwargs)


class VerifiedAuthorization:
    """Immutable snapshot of a verified authorization, bound to one operation.

    Downstream code MUST use ``record`` (a read-only mapping) rather than
    re-reading the mutable authorization file, closing the in-process
    check-then-use race. It does NOT defend against an actor who controls the
    verifier, the trust root, and the executing process (see module docstring).
    """

    __slots__ = ("record", "digest", "stage", "operation", "binding", "binding_digest")

    def __init__(
        self,
        record: Mapping[str, Any],
        digest: str,
        stage: str,
        operation: str,
        binding: Mapping[str, Any] | None = None,
        binding_digest: str | None = None,
    ):
        self.record = MappingProxyType(dict(record))
        self.digest = digest
        self.stage = stage
        self.operation = operation
        self.binding = MappingProxyType(dict(binding or {}))
        self.binding_digest = binding_digest or ""

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"VerifiedAuthorization(stage={self.stage!r}, op={self.operation!r}, "
            f"digest={self.digest[:12]}…, binding={self.binding_digest[:12]}…)"
        )


def normalize_execution_binding(
    *, stage: str, operation: str, binding: Mapping[str, Any] | None
) -> dict[str, str]:
    """Canonical stage/operation-first execution binding (string values only)."""
    full: dict[str, str] = {"stage": stage, "operation": operation}
    for name, value in dict(binding or {}).items():
        if name not in BINDING_FIELDS:
            raise AuthorizationError(f"unknown execution-binding field: {name!r}")
        if not isinstance(value, str) or not value:
            raise AuthorizationError(f"execution-binding field {name!r} must be a non-empty string")
        full[name] = value
    return full


def verify_and_bind(*, stage: str, operation: str, **kwargs: Any) -> VerifiedAuthorization:
    """Verify once and return an immutable snapshot bound to the operation."""
    binding_in = kwargs.get("binding")
    record = require_authorized(stage=stage, operation=operation, **kwargs)
    full = normalize_execution_binding(
        stage=stage, operation=operation, binding=binding_in
    )
    from qwen_train.f2_authz_ledger import execution_binding_digest

    return VerifiedAuthorization(
        record=record,
        digest=record_digest(record),
        stage=stage,
        operation=operation,
        binding=full,
        binding_digest=execution_binding_digest(full),
    )


def verify_and_consume(
    *,
    stage: str,
    operation: str,
    binding: Mapping[str, Any] | None = None,
    ledger_root: Path | str | None = None,
    **kwargs: Any,
) -> tuple[VerifiedAuthorization, Mapping[str, Any]]:
    """Verify the authorization, then durably consume it ONCE. Fail-closed.

    Order: full verification (signature, scope, dates, revocation, bindings)
    happens BEFORE the durable consumption, so an invalid record is never
    consumed; the consumption (atomic create) happens BEFORE any protected
    side effect, so a second attempt - concurrent or later, in this or any
    other process - is denied. There is no unconsume: a crash after
    consumption leaves the authorization consumed (the fail-closed direction).
    """
    binding_in = dict(binding or {})
    verified = verify_and_bind(
        stage=stage, operation=operation, binding=binding_in, **kwargs
    )
    from qwen_train.f2_authz_ledger import AuthorizationLedger

    ledger = AuthorizationLedger(ledger_root)
    entry = ledger.consume(
        record_digest=verified.digest,
        binding=dict(verified.binding),
        binding_digest=verified.binding_digest,
        stage=stage,
        operation=operation,
    )
    return verified, entry
