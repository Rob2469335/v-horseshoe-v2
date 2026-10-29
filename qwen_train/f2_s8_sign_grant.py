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
* refuses outside the record's ``[not_before, not_after]`` window;
* refuses unless the trust root loads under its required SHA-256 pin;
* refuses unless the approver is a live, unrevoked trust-root signer;
* refuses unless the signing key's public half equals that signer's public key;
* refuses unless the record's revision/manifest/registration bindings equal the
  operator-supplied expected values (and, for the revision, the live worktree);
* never consumes a grant, reserves budget, or starts a pilot - it only signs and,
  with ``--verify-only``, re-verifies non-consumingly.

Usage
-----
Sign (owner)::

    python -m qwen_train.f2_s8_sign_grant \
        --record <unsigned.json> --out <signed.json> \
        --key <owner.pem> --trust-root <trust_root.json> \
        --repo <execution-worktree> \
        --expect-revision <rev> \
        --expect-manifest-sha256 <sha> \
        --expect-registration-sha256 <sha>

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
import json
import os
from pathlib import Path
from typing import Any, Mapping

from qwen_train import f2_authorization as A

_SCHEMAS = {A.SCHEMA, "f2-authorization/v2"}
_REQUIRED = (
    "id", "schema", "stage", "operations", "approver",
    "not_before", "not_after", "protocol_digest",
)


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
    if trust_root_sha256:
        os.environ[A.TRUST_ROOT_DIGEST_ENV] = trust_root_sha256
    try:
        signers, revoked_keys, key_epochs, revoked_records = A.load_trust_root(trust_root)
    except A.AuthorizationError as exc:
        raise _fail(f"trust root rejected: {exc}") from exc

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
) -> Mapping[str, Any]:
    """Verify a signed record with the real verifier, WITHOUT consuming it."""
    if trust_root_sha256:
        os.environ[A.TRUST_ROOT_DIGEST_ENV] = trust_root_sha256
    binding = {
        "candidate_manifest_digest": expect_manifest_sha256,
        "registration_sha256": expect_registration_sha256,
    }
    # verify_authorization is stateless/non-consuming (no ledger, no budget).
    return A.verify_authorization(
        signed_path,
        stage=stage,
        operation=operation,
        binding=binding,
        trust_root=trust_root,
        repository_root=repo,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Owner-operated F2/S8 authorization signer (fail-closed).")
    ap.add_argument("--record", required=True, help="unsigned record JSON, or signed envelope with --verify-only")
    ap.add_argument("--out", help="signed output path (required unless --verify-only)")
    ap.add_argument("--key", help="owner Ed25519 PEM private key (required unless --verify-only)")
    ap.add_argument("--trust-root", required=True, help="trust_root.json")
    ap.add_argument("--trust-root-sha256", default=None, help="required SHA-256 pin of the trust root")
    ap.add_argument("--repo", default=None, help="execution worktree whose HEAD must equal the bound revision")
    ap.add_argument("--expect-revision", required=True)
    ap.add_argument("--expect-manifest-sha256", required=True)
    ap.add_argument("--expect-registration-sha256", required=True)
    ap.add_argument("--stage", default="S8-001")
    ap.add_argument("--operation", default="base_gold_production")
    ap.add_argument("--now", default=None, help="override 'now' (ISO-8601) for deterministic checks")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument(
        "--preflight",
        action="store_true",
        help="run all fail-closed preflight checks and exit WITHOUT signing "
        "(optionally pass --key to also check key/public-key correspondence)",
    )
    args = ap.parse_args(argv)

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
            "record_id": record["id"],
            "approver": record["approver"],
            "key_epoch": root["key_epoch"],
            "key_checked": key_checked,
            "signed": False,
            "consumed": False,
        }, indent=2))
        return 0

    if args.verify_only:
        record = _record_from_doc(_load_record_strict(rec_path), signed=True)
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
        )
        print(json.dumps({
            "verified": True,
            "record_id": verified.get("id"),
            "record_digest": A.record_digest(verified),
            "consumed": False,
        }, indent=2))
        return 0

    if not args.out:
        raise _fail("--out is required when signing")
    if not args.key:
        raise _fail("--key is required when signing")

    record = _record_from_doc(_load_record_strict(rec_path), signed=False)
    root = preflight(
        record, stage=args.stage, operation=args.operation,
        trust_root=trust_root, trust_root_sha256=args.trust_root_sha256,
        repo=repo, expect_revision=args.expect_revision,
        expect_manifest_sha256=args.expect_manifest_sha256,
        expect_registration_sha256=args.expect_registration_sha256, now=now,
    )
    sign(
        record, out=Path(args.out), key_path=Path(args.key),
        expected_public_b64=root["key_public_b64"], key_epoch=root["key_epoch"],
    )
    print(json.dumps({
        "signed": args.out,
        "record_id": record["id"],
        "record_digest": A.record_digest(record),
        "approver": record["approver"],
        "key_id": record["approver"],
        "key_epoch": root["key_epoch"],
        "consumed": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
