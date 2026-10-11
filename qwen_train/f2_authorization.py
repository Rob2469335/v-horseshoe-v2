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

This module performs verification only. It does NOT acquire populations, admit
units, or execute any F2 stage.
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


def load_trust_root(path: Path | None = None) -> Mapping[str, str]:
    """Return {key_id: base64(ed25519 public key)}. Absent root => deny."""
    p = _trust_root_path(path)
    if not p.exists():
        raise AuthorizationError(
            f"no authorization trust root provisioned at {p}; all stages denied"
        )
    root = _load_json_strict(p)
    signers = root.get("signers")
    if not isinstance(signers, dict) or not signers:
        raise AuthorizationError("trust root contains no signers")
    for kid, pub in signers.items():
        if not isinstance(pub, str) or not pub:
            raise AuthorizationError(f"trust root signer {kid!r} has no key")
    return signers


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
) -> Mapping[str, Any]:
    """Verify a signed record authorises ``operation`` at ``stage``. Fail-closed."""
    if stage not in STAGES:
        raise AuthorizationError(f"unknown stage: {stage!r}")
    p = Path(record_path)
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

    # ---- signature verification against the owner-controlled trust root ----
    signers = load_trust_root(trust_root)
    kid = sig.get("key_id")
    if not isinstance(kid, str) or kid not in signers:
        raise AuthorizationError(f"untrusted signer: {kid!r}")
    if sig.get("alg") != "ed25519":
        raise AuthorizationError(f"unsupported signature alg: {sig.get('alg')!r}")
    if sig.get("signer") != record.get("approver"):
        raise AuthorizationError("approver/signer identity mismatch")
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
    except Exception as exc:  # noqa: BLE001 - any failure is fail-closed
        raise AuthorizationError(f"signature verification failed: {exc}")
    return record


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

    __slots__ = ("record", "digest", "stage", "operation")

    def __init__(self, record: Mapping[str, Any], digest: str, stage: str, operation: str):
        self.record = MappingProxyType(dict(record))
        self.digest = digest
        self.stage = stage
        self.operation = operation

    def __repr__(self) -> str:  # noqa: D105
        return f"VerifiedAuthorization(stage={self.stage!r}, op={self.operation!r}, digest={self.digest[:12]}…)"


def verify_and_bind(*, stage: str, operation: str, **kwargs: Any) -> VerifiedAuthorization:
    """Verify once and return an immutable snapshot bound to the operation."""
    record = require_authorized(stage=stage, operation=operation, **kwargs)
    return VerifiedAuthorization(
        record=record, digest=record_digest(record), stage=stage, operation=operation
    )
