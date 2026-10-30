"""Owner-operated signing CLI for Experiment J / F2 authorization records.

Purpose
-------
Produce the exact envelope the repository verifier
(:mod:`qwen_train.f2_authorization`) accepts::

    {"record": <record>, "signature": {alg, key_id, key_epoch, signer, sig}}

with ``sig = base64(ed25519_sign(private_key, canonical_bytes(record)))``.

The owner runs this locally, under their own control, against their own
owner-controlled private key.  This module NEVER generates, embeds, prints,
logs, copies or transmits private key material: it loads the key with
``cryptography`` only to produce one signature and to compare the derived
PUBLIC key against the trust root (a mismatch fails closed).

Safety properties (all fail closed)
-----------------------------------
* refuses to overwrite an existing signed output file;
* refuses a record whose required fields / stage / operation are wrong;
* refuses outside the record's ``[not_before, not_after]`` window, always
  evaluated against REAL UTC (``--now`` is a preflight-only diagnostic and is
  rejected on a signing run);
* refuses unless the trust root loads under its required SHA-256 pin;
* refuses unless the approver is a live, unrevoked trust-root signer;
* refuses unless the signing key's public half equals that signer's public key;
* refuses unless the record's revision/manifest/registration bindings equal the
  operator-supplied expected values AND the live worktree is at that revision;
* refuses unless the complete record content equals an owner-approved
  expected-grant spec whose bytes are tracked, unmodified, at the bound
  revision and match an operator-supplied digest pin (see GOVERNANCE GAP in
  the module docstring footer: the repository has no independent owner
  signature on that spec);
* refuses unless the record's structure passes the same budget / network /
  identity / selection loaders the runtime gate uses;
* refuses to sign without a TTY and an exactly typed confirmation of the
  record digest prefix (no unattended signing oracle);
* appends one audit line to an explicitly supplied path OUTSIDE the repository;
* never consumes a grant, reserves budget, or starts a pilot - it only signs and,
  with ``--verify-only``, re-verifies non-consumingly.

Usage
-----
Sign (owner; interactive terminal, key path never shared with an agent)::

    python -m qwen_train.f2_s8_sign_grant \
        --record <unsigned.json> --out <signed.json> \
        --key <owner.pem> --trust-root <trust_root.json> \
        --trust-root-sha256 <root-sha256> \
        --repo <execution-worktree> \
        --expect-revision <rev> \
        --expect-manifest-sha256 <sha> \
        --expect-registration-sha256 <sha> \
        --expected-grant <worktree-relative spec.json> \
        --expected-grant-sha256 <spec-sha256> \
        --audit-log <absolute path outside the worktree>

The expected-grant spec is::

    {"schema": "f2-expected-grant/v1",
     "approved_record_sha256": "<sha256 of canonical_bytes(approved_record)>",
     "approved_record": { ...expected record, WITHOUT repository_revision... }}

Provenance rule: the spec must live inside ``--repo``, be TRACKED at the bound
revision, have bytes byte-identical to ``<revision>:<spec>``, and hash to the
supplied pin.  The revision itself is excluded from the spec because it is bound
to live git and re-checked against ``--expect-revision``; committing it into the
spec would create a hash self-reference no commit can satisfy.  That ties the
signed substance to a reviewable commit; it does NOT provide independent owner
custody of the spec (there is no CODEOWNERS entry and no second signature in
this repository), so a contributor who can commit can change a future spec.
Owner action is required before relying on it.

Verify an already-signed record without consuming it::

    python -m qwen_train.f2_s8_sign_grant --verify-only \
        --record <signed.json> --trust-root <trust_root.json> \
        --repo <execution-worktree> \
        --expect-revision <rev> \
        --expect-manifest-sha256 <sha> \
        --expect-registration-sha256 <sha>
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from qwen_train import f2_authorization as A

_SCHEMAS = {A.SCHEMA, "f2-authorization/v2"}
_REQUIRED = (
    "id", "schema", "stage", "operations", "approver",
    "not_before", "not_after", "protocol_digest",
)

#: Owner-approved expected-grant spec schema.  The spec pins the ENTIRE record
#: (not a subset of fields) so no signed field can escape owner review.
_SPEC_SCHEMA = "f2-expected-grant/v1"
_SPEC_KEYS = frozenset({"schema", "approved_record_sha256", "approved_record"})
_HEX64 = re.compile(r"[0-9a-f]{64}")

#: Fields the expected-grant spec may not carry: the revision is bound to live
#: git at signing time and re-checked against ``--expect-revision``, so it must
#: never be baked into a file that is committed at that same revision (that
#: would be a hash self-reference no commit can satisfy).
_SPEC_EXCLUDED = frozenset({"repository_revision"})


#: Network policies the runtime gate accepts (mirror of the set enforced in
#: ``f2_authorization.verify_authorization``).  Kept here so the signer refuses
#: an unsignable value BEFORE a signature exists.
_NETWORK_POLICIES = frozenset({
    "OFFLINE_WHEELHOUSE_ONLY",
    "PYPI_INSTALL_ONLY_COLLECTION_OFFLINE",
    "CONTROLLED_ACQUISITION_OFFLINE_EXECUTION",
})


def _fail(msg: str) -> "SystemExit":
    return SystemExit(f"REFUSING: {msg}")


def _load_record_strict(path: Path) -> dict[str, Any]:
    """Parse a JSON signed-record *document* or a bare record.

    Accepts either a bare record object (what ``--record`` normally points at)
    or an already-signed ``{record, signature}`` document (``--verify-only``).
    Rejects duplicate keys.
    """
    try:
        raw = path.read_text("utf-8")
    except OSError as exc:
        raise _fail(f"record unreadable: {path} ({exc})") from exc

    def _no_dup(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in pairs:
            if k in out:
                raise _fail(f"duplicate JSON key rejected: {k!r}")
            out[k] = v
        return out

    try:
        doc = json.loads(raw, object_pairs_hook=_no_dup)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise _fail(f"record is not valid JSON: {path} ({exc})") from exc
    if not isinstance(doc, dict):
        raise _fail("record document is not a JSON object")
    return doc


def _record_from_doc(doc: Mapping[str, Any], *, signed: bool) -> dict[str, Any]:
    if signed:
        record = doc.get("record")
        if not isinstance(record, dict):
            raise _fail("signed document has no 'record' object")
        return record
    # Bare record: tolerate an accidental envelope on the sign path.
    if "record" in doc and isinstance(doc["record"], dict) and "signature" in doc:
        return doc["record"]
    return dict(doc)


def _parse_ts(value: str) -> _dt.datetime:
    ts = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise _fail(f"timestamp lacks timezone: {value!r}")
    return ts


def _within(path: Path, root: Path) -> bool:
    """True only when resolved ``path`` is strictly inside resolved ``root``."""
    p = os.path.normcase(str(Path(path).resolve()))
    r = os.path.normcase(str(Path(root).resolve()))
    return p != r and p.startswith(r + os.sep)


def _git(repo: Path, *args: str) -> tuple[int, bytes, bytes]:
    """Run a read-only git command; returns (returncode, stdout, stderr) bytes."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, check=False,
        )
    except OSError as exc:
        raise _fail(f"git is unavailable; cannot establish provenance ({exc})") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _load_expected_grant(spec_path: Path) -> tuple[dict[str, Any], str]:
    """Load and self-check an owner-approved expected-grant spec.

    Returns ``(approved_record, spec_digest)``.  The spec must declare exactly
    the three keys of ``_SPEC_KEYS`` and its ``approved_record_sha256`` must
    equal the canonical digest of its ``approved_record`` object; otherwise the
    spec is internally inconsistent and is refused (a tampered spec must not
    silently approve a record).
    """
    doc = _load_record_strict(spec_path)
    if set(doc) != _SPEC_KEYS:
        raise _fail(
            f"expected-grant spec keys must be exactly {sorted(_SPEC_KEYS)}, "
            f"got {sorted(doc)}"
        )
    if doc.get("schema") != _SPEC_SCHEMA:
        raise _fail(f"expected-grant spec schema must be {_SPEC_SCHEMA!r}")
    rec = doc.get("approved_record")
    if not isinstance(rec, dict):
        raise _fail("expected-grant spec has no 'approved_record' object")
    if _SPEC_EXCLUDED & set(rec):
        raise _fail(
            "expected-grant spec may not pin "
            f"{sorted(_SPEC_EXCLUDED)}: the revision is bound to live git"
        )
    digest = hashlib.sha256(A.canonical_bytes(rec)).hexdigest()
    if doc.get("approved_record_sha256") != digest:
        raise _fail(
            "expected-grant spec is internally inconsistent: approved_record_sha256 "
            f"{doc.get('approved_record_sha256')!r} != canonical record digest {digest}"
        )
    return rec, hashlib.sha256(spec_path.read_bytes()).hexdigest()


def _require_spec_match(record: Mapping[str, Any], approved: Mapping[str, Any]) -> None:
    """Refuse unless every signable field equals the owner-approved value.

    The revision is excluded from the spec (it is checked against live git and
    ``--expect-revision``), so it is required in the record and must not appear
    in the spec.  Every other key must match exactly: a field cannot be added,
    removed or altered relative to owner review.
    """
    record_keys = set(record) - _SPEC_EXCLUDED
    approved_keys = set(approved)
    if record_keys != approved_keys:
        added = sorted(record_keys - approved_keys)
        missing = sorted(approved_keys - record_keys)
        raise _fail(
            "record/spec field sets differ: unapproved fields "
            f"{added}, missing approved fields {missing}"
        )
    for key in sorted(record_keys):
        if A.canonical_bytes({key: record[key]}) != A.canonical_bytes({key: approved[key]}):
            raise _fail(f"record field {key!r} differs from the owner-approved spec")


def _verify_expected_grant_provenance(
    spec_path: Path, *, repo: Path, revision: str, expected_sha256: str,
) -> None:
    """Tie the spec to the bound revision, byte for byte, and to a digest pin.

    Provenance is established only when ALL of these hold:
    1. the spec resolves INSIDE the bound worktree (otherwise it cannot be
       part of the reviewed revision);
    2. its bytes hash to the operator-supplied ``--expected-grant-sha256``;
    3. git tracks it at the bound revision;
    4. its on-disk bytes are identical to ``<revision>:<spec>`` (no drift).

    This is a repository-review anchor, not independent key custody: see the
    module docstring.  It refuses rather than guessing when it cannot tell.
    """
    if not _HEX64.fullmatch(str(expected_sha256 or "")):
        raise _fail(
            "missing or malformed --expected-grant-sha256 "
            "(64 lowercase hex required to pin the approved spec)"
        )
    if not spec_path.is_file():
        raise _fail(f"expected-grant spec not present: {spec_path}")
    resolved = spec_path.resolve()
    if not _within(resolved, repo):
        raise _fail(
            "expected-grant spec is outside the bound repository; "
            "provenance cannot be established for an untracked file"
        )
    actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if actual != str(expected_sha256).lower():
        raise _fail(
            "expected-grant spec digest mismatch: "
            f"file={actual}, pinned={str(expected_sha256).lower()}"
        )
    rel = resolved.relative_to(repo.resolve()).as_posix()
    rc, _out, _err = _git(repo, "ls-files", "--error-unmatch", "--", rel)
    if rc != 0:
        raise _fail(f"expected-grant spec is not tracked in the worktree: {rel}")
    rc, blob, _err = _git(repo, "show", f"{revision}:{rel}")
    if rc != 0:
        raise _fail(f"expected-grant spec is absent from revision {revision}: {rel}")
    if blob != resolved.read_bytes():
        raise _fail(
            "expected-grant spec differs from the bound revision content; "
            "commit the approved spec before signing"
        )


def _validate_record_structure(record: Mapping[str, Any]) -> None:
    """Validate record content with the loaders/rules the runtime gate uses.

    The expected-grant spec fixes WHAT is authorized (owner review); this fixes
    whether that content is signable at all, mirroring
    ``f2_authorization.verify_authorization`` for a ``f2-authorization/v2``
    probe/production record so the signer cannot produce a signature the gate
    would refuse.  Nothing here invents a limit that the gate does not enforce.
    """
    from qwen_train import f2_authz_budget as B

    stage = str(record.get("stage"))
    ops = record.get("operations")
    if not isinstance(ops, list) or not ops:
        raise _fail("record operations must be a non-empty list")
    allowed = A.STAGE_OPERATIONS.get(stage)
    if allowed is None:
        raise _fail(f"record declares unsupported stage: {stage!r}")
    outside = sorted(set(ops) - set(allowed))
    if outside:
        raise _fail(f"record scope escalation: {outside} outside stage {stage}")

    for name in ("candidate_manifest_digest", "registration_sha256"):
        value = record.get(name)
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise _fail(f"record field {name} must be 64 lowercase hex, got {value!r}")

    if record.get("revoked") is True or record.get("superseded_by"):
        raise _fail("record is revoked or superseded; it cannot be signed")

    try:
        budget = B.budget_of(record)  # same validator the gate/budget path uses
    except B.BudgetError as exc:
        raise _fail(f"record budget rejected: {exc}") from exc

    is_probe = "build_probe" in ops
    if is_probe:
        target_credited = record.get("target_credited")
        if isinstance(target_credited, bool) or target_credited != 0:
            raise _fail("PROBE_CREDITS_FORBIDDEN: target_credited must be 0 for a probe grant")
        if record.get("awards_credits") is not False:
            raise _fail("PROBE_CREDITS_FORBIDDEN: awards_credits must be false for a probe grant")
        for name in ("probe_pool_digest", "probe_selection_digest",
                     "registration_manifest_sha256"):
            value = record.get(name)
            if not isinstance(value, str) or not _HEX64.fullmatch(value):
                raise _fail(f"v2 probe record requires 64-hex {name}, got {value!r}")
        if record.get("ordering_policy") not in {
            "REGISTRATION_ORDER_UNBUILT",
            "REGISTRATION_ORDER_RAW",
            "ASCII_SORTED_UNBUILT",
        }:
            raise _fail(f"record declares invalid ordering_policy: {record.get('ordering_policy')!r}")
        if record.get("retention_policy") not in {
            "RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE",
            "DELETE_ALL_AFTER_CLASSIFICATION",
        }:
            raise _fail(f"record declares invalid retention_policy: {record.get('retention_policy')!r}")
    else:
        target_credited = record.get("target_credited")
        if isinstance(target_credited, bool) or not isinstance(target_credited, int) \
                or target_credited < 300:
            raise _fail("v2 production record requires integer target_credited >= 300")
        if record.get("awards_credits") is False:
            raise _fail("v2 production record must not declare awards_credits false")
        value = record.get("candidate_pool_digest")
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise _fail(f"v2 production record requires 64-hex candidate_pool_digest, got {value!r}")
        value = record.get("registration_manifest_sha256")
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise _fail(
                "v2 production record requires 64-hex registration_manifest_sha256, "
                f"got {value!r}"
            )

    raw_budget = record.get("budget")
    batch_limit = raw_budget.get("batch_limit")
    if batch_limit is not None:
        if isinstance(batch_limit, bool) or not isinstance(batch_limit, int):
            raise _fail("record budget.batch_limit must be an integer when declared")
        if not 1 <= batch_limit <= budget["max_total_tasks"]:
            raise _fail(
                f"record budget.batch_limit {batch_limit} outside "
                f"1..{budget['max_total_tasks']}"
            )

    target_compatible = record.get("target_compatible")
    if target_compatible is not None:
        if isinstance(target_compatible, bool) or not isinstance(target_compatible, int):
            raise _fail("record target_compatible must be an integer")
        if not 0 <= target_compatible <= budget["max_total_tasks"]:
            raise _fail(
                f"record target_compatible {target_compatible} outside "
                f"0..{budget['max_total_tasks']}"
            )

    if "selected_task_ids" in record:
        selected = record["selected_task_ids"]
        if not isinstance(selected, list) or not selected:
            raise _fail("record selected_task_ids must be a non-empty list")
        if any(not isinstance(t, str) or not t.strip() for t in selected):
            raise _fail("record selected_task_ids entries must be non-empty strings")
        if len(set(selected)) != len(selected):
            raise _fail("record selected_task_ids contains duplicates")
        if len(selected) > budget["max_total_tasks"]:
            raise _fail(
                f"record selects {len(selected)} tasks but max_total_tasks is "
                f"{budget['max_total_tasks']}"
            )
        if batch_limit is not None and len(selected) > batch_limit:
            raise _fail(
                f"record selects {len(selected)} tasks but batch_limit is {batch_limit}"
            )

    if "network_policy" in record and record["network_policy"] not in _NETWORK_POLICIES:
        raise _fail(f"record declares unsupported network_policy: {record['network_policy']!r}")

    if "allowed_identities" in record:
        identities = record["allowed_identities"]
        if not isinstance(identities, list) or not identities:
            raise _fail("record allowed_identities must be a non-empty list")
        if any(not isinstance(u, str) or not u.strip() for u in identities):
            raise _fail("record allowed_identities entries must be non-empty strings")
        if any(u != u.lower() for u in identities):
            raise _fail("record allowed_identities entries must be lowercase")
        if "*" in identities:
            raise _fail("record allowed_identities must not contain a wildcard")
        if len(set(identities)) != len(identities):
            raise _fail("record allowed_identities contains duplicates")

    nb, na = _parse_ts(record["not_before"]), _parse_ts(record["not_after"])
    if not nb < na:
        raise _fail(f"record validity window is empty: {nb.isoformat()} .. {na.isoformat()}")


def _canonical_summary(record: Mapping[str, Any]) -> str:
    """Human-readable canonical summary printed before the confirmation prompt."""
    budget = record.get("budget") or {}
    lines = [
        "=== SIGNING REQUEST - REVIEW BEFORE CONFIRMING ===",
        f"record id        : {record.get('id')}",
        f"stage/operations : {record.get('stage')} / {record.get('operations')}",
        f"approver (key id): {record.get('approver')}",
        f"validity (UTC)   : {record.get('not_before')} .. {record.get('not_after')}",
        f"repository rev   : {record.get('repository_revision')}",
        f"manifest digest  : {record.get('candidate_manifest_digest')}",
        f"registration     : {record.get('registration_sha256')}",
        f"pool digest      : {record.get('probe_pool_digest')}",
        f"selection digest : {record.get('probe_selection_digest')}",
        f"selected tasks   : {len(record.get('selected_task_ids') or [])} "
        f"{list(record.get('selected_task_ids') or [])}",
        f"budget           : max_total_tasks={budget.get('max_total_tasks')} "
        f"batch_limit={budget.get('batch_limit')} max_retries={budget.get('max_retries')} "
        f"max_executions={budget.get('max_executions')}",
        f"network policy   : {record.get('network_policy')}",
        f"identities       : {record.get('allowed_identities')}",
        f"record digest    : {A.record_digest(record)}",
    ]
    return "\n".join(lines)


def _confirm_digest(record: Mapping[str, Any]) -> str:
    """Require a TTY and an exactly typed digest prefix. Returns the digest."""
    digest = A.record_digest(record)
    if not sys.stdin.isatty():
        raise _fail(
            "signing requires an interactive terminal (stdin is not a TTY); "
            "this tool must never be an unattended signing oracle"
        )
    print(_canonical_summary(record))
    print(f"\nType the first 12 characters of the record digest to sign: {digest[:12]}")
    typed = (sys.stdin.readline() or "").strip()
    if typed != digest[:12]:
        raise _fail("confirmation did not match the record digest prefix; nothing signed")
    return digest


def _append_audit(path: Path | None, payload: Mapping[str, Any], *, repo: Path | None) -> Path:
    """Append one audit line to an explicitly supplied path outside the worktree."""
    if path is None:
        raise _fail("--audit-log is required when signing (no implicit audit destination)")
    target = Path(path)
    if not target.is_absolute():
        raise _fail("--audit-log must be an absolute path")
    resolved = target.resolve()
    if repo is not None and (resolved == repo.resolve() or _within(resolved, repo)):
        raise _fail(
            "--audit-log must be outside the signed worktree; a commit could "
            "otherwise rewrite or drop the audit trail"
        )
    if not resolved.parent.is_dir():
        raise _fail(f"--audit-log parent directory does not exist: {resolved.parent}")
    line = json.dumps(dict(payload), sort_keys=True) + "\n"
    with open(resolved, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return resolved


def preflight(
    record: Mapping[str, Any],
    *,
    stage: str,
    operation: str,
    trust_root: Path,
    trust_root_sha256: str | None,
    repo: Path | None,
    expect_revision: str,
    expect_manifest_sha256: str,
    expect_registration_sha256: str,
    now: _dt.datetime,
) -> dict[str, Any]:
    """Fail-closed preflight. Returns ``{"key_epoch": int, "key_public_b64": str}``."""
    for field in _REQUIRED:
        if field not in record:
            raise _fail(f"record missing required field: {field!r}")
    if record.get("schema") not in _SCHEMAS:
        raise _fail(f"unsupported schema: {record.get('schema')!r}")
    if record.get("stage") != stage:
        raise _fail(f"stage mismatch: record {record.get('stage')!r} != {stage!r}")
    ops = record.get("operations")
    if not isinstance(ops, list) or operation not in ops:
        raise _fail(f"operation {operation!r} not in record scope {ops!r}")

    nb, na = _parse_ts(record["not_before"]), _parse_ts(record["not_after"])
    if not (nb <= now <= na):
        raise _fail(f"record outside validity window [{nb.isoformat()}, {na.isoformat()}]")

    # --- authoritative binding expectations (operator supplied, never assumed) ---
    if not expect_revision:
        raise _fail("missing --expect-revision (authoritative execution revision)")
    if not expect_manifest_sha256:
        raise _fail("missing --expect-manifest-sha256 (authoritative manifest digest)")
    if not expect_registration_sha256:
        raise _fail("missing --expect-registration-sha256 (authoritative registration digest)")

    pairs = (
        ("repository_revision", expect_revision),
        ("candidate_manifest_digest", expect_manifest_sha256),
        ("registration_sha256", expect_registration_sha256),
    )
    for name, expected in pairs:
        bound = record.get(name)
        if not isinstance(bound, str) or not bound:
            raise _fail(f"record does not bind {name}")
        if bound != expected:
            raise _fail(f"{name} mismatch: record binds {bound!r}, expected {expected!r}")

    # --- live worktree must actually be at the bound revision and clean ---
    if repo is not None:
        ident = A.repository_identity(repo)
        if ident["revision"] != expect_revision:
            raise _fail(
                f"worktree revision mismatch: {ident['revision']!r} != expected {expect_revision!r}"
            )
        if record.get("repository_revision") != ident["revision"]:
            raise _fail("record revision != live worktree revision")
        if ident["dirty"]:
            raise _fail("execution worktree is dirty; code cannot be proven to equal the bound commit")

    # --- trust root + signer identity ---
    prev_pin = os.environ.get(A.TRUST_ROOT_DIGEST_ENV)
    if trust_root_sha256:
        os.environ[A.TRUST_ROOT_DIGEST_ENV] = trust_root_sha256
    try:
        signers, revoked_keys, key_epochs, revoked_records = A.load_trust_root(trust_root)
    except A.AuthorizationError as exc:
        raise _fail(f"trust root rejected: {exc}") from exc
    finally:
        # Never leak a pin into the rest of the process.
        if trust_root_sha256:
            if prev_pin is None:
                os.environ.pop(A.TRUST_ROOT_DIGEST_ENV, None)
            else:
                os.environ[A.TRUST_ROOT_DIGEST_ENV] = prev_pin

    approver = record.get("approver")
    if not isinstance(approver, str) or not approver:
        raise _fail("record has no approver")
    if approver not in signers:
        raise _fail(f"approver {approver!r} is not a trust-root signer")
    if approver in revoked_keys:
        raise _fail(f"approver key {approver!r} is revoked in the trust root")
    if record.get("id") in revoked_records:
        raise _fail(f"record {record.get('id')!r} is revoked via revoked_record_ids")

    epoch = key_epochs.get(approver, 1)
    return {"key_epoch": epoch, "key_public_b64": signers[approver]}


def _load_signing_key(key_path: Path, *, expected_public_b64: str):
    """Load the owner private key and confirm its PUBLIC half matches the root."""
    from cryptography.hazmat.primitives import serialization

    if not key_path.is_file():
        raise _fail(f"signing key not present at {key_path}")
    try:
        priv = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except Exception as exc:  # noqa: BLE001 - fail closed, never echo key bytes
        raise _fail(f"signing key could not be loaded ({type(exc).__name__})") from exc
    try:
        pub = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    except Exception as exc:  # noqa: BLE001
        raise _fail(f"signing key has no ed25519 public half ({type(exc).__name__})") from exc
    if base64.b64encode(pub).decode() != expected_public_b64:
        raise _fail(
            "signing key does not match the trust-root public key for the approver "
            "(wrong key for this trust root)"
        )
    return priv


def sign(
    record: Mapping[str, Any],
    *,
    out: Path,
    key_path: Path,
    expected_public_b64: str,
    key_epoch: int,
) -> dict[str, Any]:
    """Sign and write the envelope. Refuses to overwrite an existing output."""
    if out.exists():
        raise _fail(f"output already exists (refusing to overwrite): {out}")
    priv = _load_signing_key(key_path, expected_public_b64=expected_public_b64)
    approver = record["approver"]
    signature = base64.b64encode(priv.sign(A.canonical_bytes(record))).decode()
    envelope = {
        "record": dict(record),
        "signature": {
            "alg": "ed25519",
            "key_id": approver,
            "key_epoch": key_epoch,
            "signer": approver,
            "sig": signature,
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    # exclusive create: never clobber a signed authorization that appeared
    # between the preflight check and here.
    try:
        with open(out, "x", encoding="utf-8") as fh:
            fh.write(json.dumps(envelope, indent=2))
            fh.write("\n")
    except FileExistsError as exc:
        raise _fail(f"output appeared during signing (refusing to overwrite): {out}") from exc
    return envelope


def verify_only(
    signed_path: Path,
    *,
    stage: str,
    operation: str,
    trust_root: Path,
    trust_root_sha256: str | None,
    repo: Path | None,
    expect_revision: str,
    expect_manifest_sha256: str,
    expect_registration_sha256: str,
    bindings: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Verify a signed record with the real verifier, WITHOUT consuming it.

    ``bindings`` carries the ADDITIONAL expected values the operation requires
    (e.g. ``probe_pool_digest``, ``ordering_policy``, ``allowed_identities``).
    They must come from the caller's independent computation - this function
    never copies them out of the record, because an expected value derived from
    the thing being verified is not a check.
    """
    prev_pin = os.environ.get(A.TRUST_ROOT_DIGEST_ENV)
    if trust_root_sha256:
        os.environ[A.TRUST_ROOT_DIGEST_ENV] = trust_root_sha256
    try:
        merged: dict[str, Any] = dict(bindings or {})
        for key, value in (
            ("candidate_manifest_digest", expect_manifest_sha256),
            ("registration_sha256", expect_registration_sha256),
        ):
            if key in merged and merged[key] != value:
                raise _fail(
                    f"conflicting expected binding {key!r}: --expect-* says "
                    f"{value!r}, --expect-bindings says {merged[key]!r}"
                )
            merged[key] = value
        # verify_authorization is stateless/non-consuming (no ledger, no budget).
        return A.verify_authorization(
            signed_path,
            stage=stage,
            operation=operation,
            binding=merged,
            trust_root=trust_root,
            repository_root=repo,
        )
    finally:
        if trust_root_sha256:
            if prev_pin is None:
                os.environ.pop(A.TRUST_ROOT_DIGEST_ENV, None)
            else:
                os.environ[A.TRUST_ROOT_DIGEST_ENV] = prev_pin


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Owner-operated F2/S8 authorization signer (fail-closed).")
    ap.add_argument("--record", required=True, help="unsigned record JSON, or signed envelope with --verify-only")
    ap.add_argument("--out", help="signed output path (required unless --verify-only)")
    ap.add_argument("--key", help="owner Ed25519 PEM private key (required unless --verify-only)")
    ap.add_argument("--trust-root", required=True, help="trust_root.json")
    ap.add_argument("--trust-root-sha256", default=None, help="required SHA-256 pin of the trust root")
    ap.add_argument("--repo", default=None, help="execution worktree whose HEAD must equal the bound revision (required for signing/verify)")
    ap.add_argument("--expect-revision", required=True)
    ap.add_argument("--expect-manifest-sha256", required=True)
    ap.add_argument("--expect-registration-sha256", required=True)
    ap.add_argument("--stage", default="S8-001")
    ap.add_argument("--operation", default="base_gold_production")
    ap.add_argument("--now", default=None, help="PREFLIGHT-ONLY diagnostic clock override (ISO-8601); rejected when signing")
    ap.add_argument("--expected-grant", default=None, help="owner-approved expected-grant spec (required when signing)")
    ap.add_argument("--expected-grant-sha256", default=None, help="SHA-256 pin of the expected-grant spec (required when signing)")
    ap.add_argument("--expect-bindings", default=None, help="JSON object of additional expected binding values for --verify-only")
    ap.add_argument("--audit-log", default=None, help="absolute audit log path OUTSIDE the worktree (required when signing)")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument(
        "--preflight",
        action="store_true",
        help="run all fail-closed preflight checks and exit WITHOUT signing "
        "(optionally pass --key to also check key/public-key correspondence)",
    )
    args = ap.parse_args(argv)

    signing = not (args.preflight or args.verify_only)
    if args.now and not args.preflight:
        raise _fail(
            "--now is a preflight-only diagnostic clock; a signing or verify "
            "run must use real UTC"
        )
    now = _parse_ts(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    trust_root = Path(args.trust_root)
    repo = Path(args.repo) if args.repo else None
    rec_path = Path(args.record)

    if args.preflight:
        record = _record_from_doc(_load_record_strict(rec_path), signed=False)
        root = preflight(
            record, stage=args.stage, operation=args.operation,
            trust_root=trust_root, trust_root_sha256=args.trust_root_sha256,
            repo=repo, expect_revision=args.expect_revision,
            expect_manifest_sha256=args.expect_manifest_sha256,
            expect_registration_sha256=args.expect_registration_sha256, now=now,
        )
        key_checked = False
        if args.key:
            _load_signing_key(Path(args.key), expected_public_b64=root["key_public_b64"])
            key_checked = True
        print(json.dumps({
            "preflight": True,
            "binding": False,
            "clock_overridden": bool(args.now),
            "repository_checked": repo is not None,
            "record_id": record["id"],
            "approver": record["approver"],
            "key_epoch": root["key_epoch"],
            "key_checked": key_checked,
            "signed": False,
            "consumed": False,
        }, indent=2))
        return 0

    if args.verify_only:
        if repo is None:
            raise _fail("--repo is required for --verify-only (live revision binding must be checked)")
        if not args.trust_root_sha256:
            raise _fail("trust root rejected: --trust-root-sha256 is required for --verify-only")
        record = _record_from_doc(_load_record_strict(rec_path), signed=True)
        extra: dict[str, Any] = {}
        if args.expect_bindings:
            raw_bindings = _load_record_strict(Path(args.expect_bindings))
            if not isinstance(raw_bindings, dict):
                raise _fail("--expect-bindings must be a JSON object of expected values")
            extra = raw_bindings
        # Preflight the embedded record too (same bindings/expiry/identity).
        preflight(
            record, stage=args.stage, operation=args.operation,
            trust_root=trust_root, trust_root_sha256=args.trust_root_sha256,
            repo=repo, expect_revision=args.expect_revision,
            expect_manifest_sha256=args.expect_manifest_sha256,
            expect_registration_sha256=args.expect_registration_sha256, now=now,
        )
        verified = verify_only(
            rec_path, stage=args.stage, operation=args.operation,
            trust_root=trust_root, trust_root_sha256=args.trust_root_sha256,
            repo=repo, expect_revision=args.expect_revision,
            expect_manifest_sha256=args.expect_manifest_sha256,
            expect_registration_sha256=args.expect_registration_sha256,
            bindings=extra,
        )
        print(json.dumps({
            "verified": True,
            "record_id": verified.get("id"),
            "record_digest": A.record_digest(verified),
            "consumed": False,
        }, indent=2))
        return 0

    # ---- signing path: every guard below is mandatory ----
    assert signing
    if not args.out:
        raise _fail("--out is required when signing")
    if not args.key:
        raise _fail("--key is required when signing")
    if repo is None:
        raise _fail("--repo is required when signing (the revision must come from live git)")
    if not args.trust_root_sha256:
        raise _fail("trust root rejected: --trust-root-sha256 is required when signing, not optional")
    if not args.expected_grant:
        raise _fail("--expected-grant is required when signing (owner-approved spec)")
    if not args.expected_grant_sha256:
        raise _fail("--expected-grant-sha256 is required when signing (spec digest pin)")
    if not args.audit_log:
        raise _fail("--audit-log is required when signing (owner-controlled path outside the worktree)")

    record = _record_from_doc(_load_record_strict(rec_path), signed=False)
    _validate_record_structure(record)
    spec_record, spec_digest = _load_expected_grant(Path(args.expected_grant))
    _verify_expected_grant_provenance(
        Path(args.expected_grant), repo=repo,
        revision=str(args.expect_revision), expected_sha256=args.expected_grant_sha256,
    )
    _require_spec_match(record, spec_record)
    root = preflight(
        record, stage=args.stage, operation=args.operation,
        trust_root=trust_root, trust_root_sha256=args.trust_root_sha256,
        repo=repo, expect_revision=args.expect_revision,
        expect_manifest_sha256=args.expect_manifest_sha256,
        expect_registration_sha256=args.expect_registration_sha256, now=now,
    )


    # The key is only touched AFTER all non-key checks and the interactive
    # confirmation, so a refused request never loads private material.
    digest = _confirm_digest(record)
    signed_at = _dt.datetime.now(_dt.timezone.utc)
    sign(
        record, out=Path(args.out), key_path=Path(args.key),
        expected_public_b64=root["key_public_b64"], key_epoch=root["key_epoch"],
    )
    audit_path = _append_audit(Path(args.audit_log), {
        "event": "S8_GRANT_SIGNED",
        "record_id": record["id"],
        "record_digest": digest,
        "approver": record["approver"],
        "key_epoch": root["key_epoch"],
        "signed_at_utc": signed_at.isoformat(),
        "repository_revision": args.expect_revision,
        "expected_grant_sha256": spec_digest,
        "out": str(Path(args.out).resolve()),
        "pid": os.getpid(),
    }, repo=repo)
    print(json.dumps({
        "signed": args.out,
        "record_id": record["id"],
        "record_digest": digest,
        "approver": record["approver"],
        "key_id": record["approver"],
        "key_epoch": root["key_epoch"],
        "signed_at_utc": signed_at.isoformat(),
        "expected_grant_sha256": spec_digest,
        "audit_log": str(audit_path),
        "consumed": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
