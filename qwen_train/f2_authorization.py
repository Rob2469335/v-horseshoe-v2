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
one durable consumption PER GRANT (the canonical record digest - one grant
authorizes exactly one execution, whatever the binding); see
``f2_authz_ledger`` for the exact threat model, including what a local
administrator could still undo.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import re
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

SCHEMA = "f2-authorization/v1"
STAGES = ("POP-001", "EXPL-001", "CONF-001", "S8-001")
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
    "S8-001": frozenset({"base_gold_production", "build_probe"}),
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
    "acquired_inputs_digest",
    "candidate_manifest_digest",
    "registration_sha256",
    "repository_revision",
    "experiment_id",
    "protocol_version",
    "run_id",
    "rollout_id",
    "arm",
    "candidate_pool_digest",
    "registration_manifest_sha256",
    "probe_pool_digest",
    "probe_selection_digest",
    "ordering_policy",
    "allowed_identities",
    "network_policy",
    "retention_policy",
    "awards_credits",
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
#:   * ``acquired_inputs_digest`` - recomputed by the registration entry point
#:     as the canonical digest of the COMPLETE acquired input set (each file
#:     verified against its producer-written provenance, all read exactly
#:     once), so the owner's signature anchors every consumed input, not just
#:     the union report;
#:   * ``experiment_id`` / ``protocol_version`` / ``arm`` - taken at the
#:     lowest boundary from the independently VERIFIED frozen arm manifest
#:     (the orchestrator may only cross-check values that later must equal
#:     the manifest; a caller cannot substitute the arm without the record
#:     and the verified manifest disagreeing at the lowest boundary).
#: A record missing any required binding, or carrying a mismatching one, is
#: denied before any protected side effect.
OPERATION_REQUIRED_BINDINGS: dict[tuple[str, str], frozenset[str]] = {
    ("POP-001", "acquire"): frozenset({"repository_revision"}),
    ("POP-001", "register"): frozenset(
        {"repository_revision", "population_manifest_digest", "acquired_inputs_digest"}
    ),
    ("EXPL-001", "exploratory_run"): frozenset(
        {"repository_revision", "experiment_id", "protocol_version", "arm"}
    ),
    # S8 base/gold evidence production (the governed pilot). Bound to the exact
    # candidate manifest and registration being run, and to the repository
    # revision the code was taken from -- so a grant authorises ONE manifest at
    # ONE revision, never "a pilot" in the abstract.
    ("S8-001", "base_gold_production"): frozenset(
        {
            "repository_revision",
            "candidate_manifest_digest",
            "registration_sha256",
            "registration_manifest_sha256",
        }
    ),
    ("S8-001", "build_probe"): frozenset(
        {
            "repository_revision",
            "registration_sha256",
            "registration_manifest_sha256",
            "probe_pool_digest",
            "probe_selection_digest",
            "ordering_policy",
            "allowed_identities",
            "network_policy",
            "retention_policy",
            "awards_credits",
        }
    ),
    ("CONF-001", "confirmatory_run"): frozenset(
        {"repository_revision", "experiment_id", "protocol_version", "arm"}
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


def _validate_signed_value(value: Any, path: str = "$") -> None:
    """Reject signed-payload values whose JSON encoding is ambiguous or invalid.

    The record schemas use only strings, integers, booleans, null, arrays, and
    objects with string keys. Floats (including NaN/Infinity) and non-string
    object keys are refused rather than being silently coerced by ``json``.
    """
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise AuthorizationError(
                    f"signed payload object key at {path} must be a string, "
                    f"got {type(key).__name__}"
                )
            _validate_signed_value(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_signed_value(child, f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        raise AuthorizationError(
            f"signed payload contains a float at {path}; floats are not permitted"
        )
    raise AuthorizationError(
        f"signed payload contains unsupported type {type(value).__name__} at {path}"
    )


def canonical_bytes(record: Mapping[str, Any]) -> bytes:
    """Deterministic serialisation of the signed portion of a record.

    This is the repo's JCS-compatible subset: ASCII object keys are sorted,
    whitespace is omitted, and NaN/Infinity/floats/non-string keys are refused.
    It is not a full RFC 8785 implementation for arbitrary non-ASCII key sets.
    """
    def _to_json_compat(obj: Any) -> Any:
        if isinstance(obj, Mapping):
            return {k: _to_json_compat(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_to_json_compat(v) for v in obj]
        return obj

    _validate_signed_value(record)
    return json.dumps(
        _to_json_compat(record),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
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
    *,
    require_pin: bool = True,
) -> tuple[Mapping[str, str], frozenset[str], Mapping[str, int], frozenset[str]]:
    """Return ``(signers, revoked_key_ids, key_epochs, revoked_record_ids)``.

    Absent root => deny. The trust root is the OWNER-controlled trust
    configuration file; its revocation lists are the authoritative revocation
    source in this design (there is no external revocation service).

    PIN POLICY (fail closed): ``require_pin=True`` (every protected
    verification path) REQUIRES ``F2_AUTHORIZATION_TRUST_ROOT_SHA256`` to be
    set and to equal the SHA-256 of the loaded root file. A missing pin is a
    denial, not a pass: a missing/unset pin can no longer silently disable
    integrity checking. The pin is process-controlled configuration - it
    defeats accidental root substitution and forces any redirect of the root
    file to also falsify the pin explicitly; it is NOT independent custody
    (see ``describe_trust_root``). ``require_pin=False`` exists ONLY for the
    owner-facing fingerprinting helper, which performs no authorization and
    may be used to establish the pin in the first place.

    * ``signers`` maps ``key_id -> base64(ed25519 raw public key)`` (kept as the
      historical string form so existing callers keep working).
    * ``revoked_key_ids`` comes from the root's optional ``revoked_key_ids``
      list; a signature from a listed key is denied even if the key is still
      present in ``signers`` (the "revoked but not yet removed" state).
    * ``key_epochs`` maps ``key_id -> epoch`` for signer entries that declare
      one (dict form ``{"public_key": ..., "epoch": n}``).
    * ``revoked_record_ids`` comes from the root's optional
      ``revoked_record_ids`` list: individual signed grants (by record ``id``)
      the owner has revoked post-signing WITHOUT mutating the signed record.
      This is record-level revocation within the existing trust model: it is
      only as durable and independent as the trust-root file itself - it does
      NOT defend against a principal who can rewrite the trust root.
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
    revoked_records_raw = root.get("revoked_record_ids", [])
    if not isinstance(revoked_records_raw, list) or not all(
        isinstance(r, str) and r for r in revoked_records_raw
    ):
        raise AuthorizationError(
            "trust root revoked_record_ids must be a list of record ids"
        )
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
    # Digest pin. REQUIRED on every protected verification path: a missing
    # pin denies; a wrong pin denies. The pin env is process-controlled (see
    # the pin policy above) - it cannot turn a signature/revocation/binding
    # failure into success, only attest which root file was intended.
    pinned = os.environ.get(TRUST_ROOT_DIGEST_ENV, "").strip()
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if require_pin:
        if not pinned:
            raise AuthorizationError(
                f"trust root digest pin required but not set "
                f"({TRUST_ROOT_DIGEST_ENV}); protected verification is "
                "fail-closed: denied. Establish the pin out-of-band with "
                "describe_trust_root() and the owner's authenticated channel."
            )
        if actual.lower() != pinned.lower():
            raise AuthorizationError(
                f"trust root digest mismatch (pinned {pinned[:12]}…, actual "
                f"{actual[:12]}…) - fail-closed"
            )
    elif pinned and actual.lower() != pinned.lower():
        # Even the fingerprinting helper refuses a root that contradicts an
        # EXPLICITLY set pin (a wrong pin must never read as success).
        raise AuthorizationError(
            f"trust root digest mismatch (pinned {pinned[:12]}…, actual "
            f"{actual[:12]}…) - fail-closed"
        )
    return signers, frozenset(revoked_raw), epochs, frozenset(revoked_records_raw)


def describe_trust_root(path: Path | None = None) -> Mapping[str, Any]:
    """Public trust-root identity for OUT-OF-BAND owner verification.

    Returns only public material (key ids, epochs, revocation lists, file
    digest) so the owner can compare fingerprints through a channel
    independent of this process and ESTABLISH the digest pin. It never
    returns private key material (a trust root holds none). This helper
    performs no authorization; it is the one caller allowed to load a root
    without a pin (``require_pin=False``) - and it still refuses a root that
    contradicts an explicitly set pin.
    """
    signers, revoked, epochs, revoked_records = load_trust_root(
        path, require_pin=False
    )
    return {
        "path": str(_trust_root_path(path)),
        "digest_sha256": trust_root_digest(path),
        "signer_key_ids": sorted(signers),
        "key_epochs": dict(sorted(epochs.items())),
        "revoked_key_ids": sorted(revoked),
        "revoked_record_ids": sorted(revoked_records),
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
    rec_schema = record.get("schema")
    if rec_schema not in (SCHEMA, "f2-authorization/v2"):
        raise AuthorizationError(f"unsupported schema: {rec_schema!r}")
    if rec_schema == SCHEMA:
        if "budget" in record:
            raise AuthorizationError("v1 authorization record cannot declare 'budget'")
        if "candidate_pool_digest" in record:
            raise AuthorizationError("v1 authorization record cannot declare 'candidate_pool_digest'")
        if "registration_manifest_sha256" in record:
            raise AuthorizationError(
                "v1 authorization record cannot declare 'registration_manifest_sha256'"
            )
    elif rec_schema == "f2-authorization/v2":
        from qwen_train.f2_authz_budget import budget_of

        budget = budget_of(record)
        if budget is None:
            raise AuthorizationError("v2 authorization record must declare valid 'budget'")
        ops = record.get("operations") or []
        is_probe = "build_probe" in ops or operation == "build_probe"
        if is_probe:
            target_credited = record.get("target_credited")
            if (
                isinstance(target_credited, bool)
                or not isinstance(target_credited, int)
                or target_credited != 0
            ):
                raise AuthorizationError(
                    "PROBE_CREDITS_FORBIDDEN: v2 probe authorization target_credited must be 0"
                )
            if record.get("awards_credits") is not False:
                raise AuthorizationError(
                    "PROBE_CREDITS_FORBIDDEN: v2 probe authorization awards_credits must be false"
                )
            for hex_key in ("probe_pool_digest", "probe_selection_digest", "registration_manifest_sha256"):
                val = record.get(hex_key)
                if not isinstance(val, str) or not re.fullmatch(r"[0-9a-f]{64}", val):
                    raise AuthorizationError(
                        f"v2 probe authorization record is missing a 64-hex {hex_key}"
                    )
            ordering_policy = record.get("ordering_policy")
            if ordering_policy not in {
                "REGISTRATION_ORDER_UNBUILT",
                "REGISTRATION_ORDER_RAW",
                "ASCII_SORTED_UNBUILT",
            }:
                raise AuthorizationError(
                    f"v2 probe authorization declares invalid ordering_policy: {ordering_policy!r}"
                )
            allowed_identities = record.get("allowed_identities")
            if (
                not isinstance(allowed_identities, (list, tuple))
                or not allowed_identities
                or not all(isinstance(x, str) and x for x in allowed_identities)
            ):
                raise AuthorizationError(
                    "v2 probe authorization requires non-empty allowed_identities list of strings"
                )
            network_policy = record.get("network_policy")
            if network_policy not in {
                "OFFLINE_WHEELHOUSE_ONLY",
                "PYPI_INSTALL_ONLY_COLLECTION_OFFLINE",
                "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
            }:
                raise AuthorizationError(
                    f"v2 probe authorization declares invalid network_policy: {network_policy!r}"
                )
            retention_policy = record.get("retention_policy")
            if retention_policy not in {
                "RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE",
                "DELETE_ALL_AFTER_CLASSIFICATION",
            }:
                raise AuthorizationError(
                    f"v2 probe authorization declares invalid retention_policy: {retention_policy!r}"
                )
            if "target_compatible" in record:
                target_compatible = record.get("target_compatible")
                max_total = int(budget["max_total_tasks"])
                if (
                    isinstance(target_compatible, bool)
                    or not isinstance(target_compatible, int)
                    or target_compatible < 0
                    or target_compatible > max_total
                ):
                    raise AuthorizationError(
                        "v2 probe authorization target_compatible must be an integer "
                        f"in 0..{max_total}"
                    )
        else:
            target_credited = record.get("target_credited")
            if (
                isinstance(target_credited, bool)
                or not isinstance(target_credited, int)
                or target_credited < 300
            ):
                raise AuthorizationError(
                    "v2 authorization target_credited must be an integer >= 300"
                )
            if record.get("awards_credits") is False:
                raise AuthorizationError(
                    "v2 production authorization awards_credits must not be false"
                )
            pool_digest = record.get("candidate_pool_digest")
            if not isinstance(pool_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", pool_digest):
                raise AuthorizationError("v2 authorization record is missing a 64-hex candidate_pool_digest")
            if (stage, operation) == ("S8-001", "base_gold_production"):
                reg_manifest = record.get("registration_manifest_sha256")
                if not isinstance(reg_manifest, str) or not re.fullmatch(r"[0-9a-f]{64}", reg_manifest):
                    raise AuthorizationError(
                        "v2 authorization record is missing a 64-hex registration_manifest_sha256"
                    )
            elif "registration_manifest_sha256" in record:
                reg_manifest = record["registration_manifest_sha256"]
                if not isinstance(reg_manifest, str) or not re.fullmatch(r"[0-9a-f]{64}", reg_manifest):
                    raise AuthorizationError(
                        "v2 authorization record declares a malformed registration_manifest_sha256"
                    )
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
    # ``revoked``/``superseded_by`` are only meaningful when the OWNER signed
    # them into the record; the caller-supplied ``revoked_ids`` argument is a
    # SUPPLEMENTARY filter (e.g. an in-memory list), NOT an authoritative
    # revocation source - the authoritative post-signing record revocation is
    # the trust root's ``revoked_record_ids`` (checked after the trust root is
    # loaded below).
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
    if rec_schema == SCHEMA:
        required.discard("registration_manifest_sha256")
    if "candidate_manifest_digest" not in record:
        # A pool-scoped grant may omit the per-batch manifest digest entirely;
        # when the signed record carries one it remains a REQUIRED binding and
        # cannot be dropped by a caller that omits the expected value.
        required.discard("candidate_manifest_digest")
    _check_binding(record, required, supplied, repository_root)

    # ---- signature verification against the owner-controlled trust root ----
    signers, root_revoked, key_epochs, root_revoked_records = load_trust_root(
        trust_root
    )
    kid = sig.get("key_id")
    if not isinstance(kid, str) or kid not in signers:
        raise AuthorizationError(f"untrusted signer: {kid!r}")
    if kid in root_revoked:
        raise AuthorizationError(f"signer key {kid!r} is revoked in the trust root")
    if rid in root_revoked_records:
        raise AuthorizationError(
            f"authorization {rid!r} is revoked via the trust root's "
            "revoked_record_ids (owner-controlled revocation)"
        )
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
        rec_val = list(record[name]) if isinstance(record[name], (list, tuple)) else record[name]
        exp_val = list(expected) if isinstance(expected, (list, tuple)) else expected
        if rec_val != exp_val:
            raise AuthorizationError(
                f"{name} mismatch: record binds {record[name]!r}, execution "
                f"requires {expected!r}"
            )
    # Any optional binding present BOTH in record and expectation must agree.
    for name in sorted(set(record) & set(supplied) - required):
        if name in BINDING_FIELDS:
            rec_val = list(record[name]) if isinstance(record[name], (list, tuple)) else record[name]
            exp_val = list(supplied[name]) if isinstance(supplied[name], (list, tuple)) else supplied[name]
            if rec_val != exp_val:
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
    if verified.record.get("schema") == "f2-authorization/v2":
        raise AuthorizationError(
            "verify_and_consume does not support v2 authorizations; "
            "use verify_and_reserve to enforce grant budgets"
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


def _reject_budget_root_reparse(root: Path | str) -> None:
    """Refuse a budget root reached through a symlink or Windows junction.

    Both ``is_symlink()`` and ``is_junction()`` are checked because a Windows
    directory junction is a reparse point but is NOT reported as a symlink.
    Every existing path component is checked, not just the leaf.

    This is a check-before-use guard. A same-user actor can still replace a
    path component between this check and the later open (CWE-367); complete
    closure requires handle-relative APIs or a separate OS security boundary.
    """
    path = Path(root)
    for component in (path, *path.parents):
        try:
            if component.is_symlink() or component.is_junction():
                raise AuthorizationError(
                    "budget root path component is a symlink or junction: "
                    f"{component}"
                )
        except OSError as exc:
            raise AuthorizationError(
                "budget root path component cannot be inspected: "
                f"{component} ({exc})"
            ) from exc


def verify_and_reserve(
    *,
    stage: str,
    operation: str,
    task_count: int = 1,
    task_ids: list[str] | None = None,
    manifest_digest: str = "",
    binding: Mapping[str, Any] | None = None,
    ledger_root: Path | str | None = None,
    budget_ledger_root: Path | str | None = None,
    execution_id: str = "",
    **kwargs: Any,
) -> tuple[VerifiedAuthorization, Mapping[str, Any]]:
    """Verify, then claim ONE budget slot and task units against a v2 grant.

    A v1 grant is rejected fail-closed under owner policy OD-2: live execution
    requires a v2 grant with explicit budget limits.
    """
    binding_in = dict(binding or {})
    verified = verify_and_bind(
        stage=stage, operation=operation, binding=binding_in, **kwargs
    )
    if verified.record.get("schema") != "f2-authorization/v2":
        raise AuthorizationError(
            f"verify_and_reserve requires schema 'f2-authorization/v2', "
            f"got {verified.record.get('schema')!r}"
        )

    from qwen_train import f2_authz_budget as budget_module

    record_manifest = verified.record.get("candidate_manifest_digest")
    supplied_manifest = verified.binding.get("candidate_manifest_digest")
    if record_manifest is not None:
        if not isinstance(record_manifest, str) or not record_manifest:
            raise AuthorizationError(
                "v2 authorization carries a malformed candidate_manifest_digest"
            )
        if not supplied_manifest:
            raise AuthorizationError(
                "candidate_manifest_digest is required by the signed authorization "
                "but no independently obtained value was supplied - fail-closed"
            )
        if supplied_manifest != record_manifest:
            raise AuthorizationError(
                f"candidate_manifest_digest mismatch: record binds "
                f"{record_manifest!r}, execution requires {supplied_manifest!r}"
            )
        if manifest_digest and manifest_digest != supplied_manifest:
            raise AuthorizationError(
                "manifest_digest mismatch: caller supplied "
                f"{manifest_digest!r}, signed candidate_manifest_digest is "
                f"{supplied_manifest!r}"
            )
        if not manifest_digest:
            manifest_digest = supplied_manifest

    pool_digest = verified.record.get("candidate_pool_digest")
    if not isinstance(pool_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", pool_digest):
        raise AuthorizationError(
            "v2 authorization is missing a 64-hex candidate_pool_digest"
        )
    supplied_pool = verified.binding.get("candidate_pool_digest")
    if supplied_pool is not None and supplied_pool != pool_digest:
        raise AuthorizationError(
            "candidate_pool_digest binding mismatch: caller supplied "
            f"{supplied_pool}, authorization binds {pool_digest}"
        )

    if (stage, operation) == ("S8-001", "base_gold_production"):
        reg_manifest = verified.record.get("registration_manifest_sha256")
        if not isinstance(reg_manifest, str) or not re.fullmatch(r"[0-9a-f]{64}", reg_manifest):
            raise AuthorizationError(
                "v2 authorization is missing a 64-hex registration_manifest_sha256"
            )
        supplied_reg = verified.binding.get("registration_manifest_sha256")
        if not supplied_reg:
            raise AuthorizationError(
                "no independently obtained expected value for required "
                "binding 'registration_manifest_sha256' - fail-closed"
            )
        if supplied_reg != reg_manifest:
            raise AuthorizationError(
                f"registration_manifest_sha256 mismatch: record binds "
                f"{reg_manifest!r}, execution requires {supplied_reg!r}"
            )

    effective_budget_root = (
        Path(budget_ledger_root)
        if budget_ledger_root is not None
        else budget_module.PRODUCTION_BUDGET_ROOT
    )
    if effective_budget_root is not None:
        _reject_budget_root_reparse(effective_budget_root)

    # manifest_digest is the per-batch digest; candidate_pool_digest remains
    # separately in the reservation binding. The two are never conflated.
    entry = budget_module.reserve(
        record=dict(verified.record),
        binding=dict(verified.binding),
        binding_digest=verified.binding_digest,
        stage=stage,
        operation=operation,
        execution_id=execution_id or str(verified.record.get("id") or ""),
        task_count=task_count,
        task_ids=task_ids,
        manifest_digest=manifest_digest,
        ledger_root=budget_ledger_root,
    )
    return verified, entry
