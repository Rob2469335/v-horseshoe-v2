"""Focused tests for the owner-operated signer (qwen_train.f2_s8_sign_grant).

All keys, trust roots and records here are ephemeral and live only inside the
test's tmp_path. No production key is read, no grant is consumed, no ledger or
budget state is touched.
"""

from __future__ import annotations

import base64
import json

import pytest

from qwen_train import f2_authorization as A
from qwen_train import f2_s8_sign_grant as S
from tests.f2_authz_testkit import (
    TEST_REPO_REVISION,
    apply_repo_identity,
    keypair,
    write_trust_root,
)

KID = "f2-s8-owner-v1"


def _record(**over):
    rec = {
        "id": "s8-test-b8",
        "schema": "f2-authorization/v1",
        "stage": "S8-001",
        "operations": ["base_gold_production"],
        "approver": KID,
        "not_before": "2026-01-01T00:00:00+00:00",
        "not_after": "2027-01-01T00:00:00+00:00",
        "protocol_digest": "f2_experiment_j_v1",
        "repository_revision": TEST_REPO_REVISION,
        "candidate_manifest_digest": "a" * 64,
        "registration_sha256": "b" * 64,
    }
    rec.update(over)
    return rec


def _write_pem(tmp_path, priv, name="key.pem"):
    from cryptography.hazmat.primitives import serialization

    p = tmp_path / name
    p.write_bytes(
        priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return p


def _write_record(tmp_path, record, name="unsigned.json"):
    p = tmp_path / name
    p.write_text(json.dumps(record), "utf-8")
    return p


def _argv(tmp_path, *, record_path, out=None, key=None, root, pin, verify_only=False, **over):
    rev = over.pop("expect_revision", TEST_REPO_REVISION)
    man = over.pop("expect_manifest_sha256", "a" * 64)
    reg = over.pop("expect_registration_sha256", "b" * 64)
    argv = [
        "--record", str(record_path),
        "--trust-root", str(root),
        "--trust-root-sha256", pin,
        "--repo", str(tmp_path),
        "--expect-revision", rev,
        "--expect-manifest-sha256", man,
        "--expect-registration-sha256", reg,
    ]
    if verify_only:
        argv.append("--verify-only")
    else:
        argv += ["--out", str(out), "--key", str(key)]
    return argv


def _sha(p):
    import hashlib

    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)  # pin repository_identity to TEST_REPO_REVISION
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {KID: (priv, pub)}, epochs={KID: 1})
    return {"tmp": tmp_path, "priv": priv, "pub": pub, "root": root, "pin": _sha(root)}


def test_sign_roundtrip_then_verify(setup):
    s = setup
    rec = _record()
    recp = _write_record(s["tmp"], rec)
    out = s["tmp"] / "signed.json"
    key = _write_pem(s["tmp"], s["priv"])

    assert S.main(_argv(s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"])) == 0
    assert out.is_file()
    env = json.loads(out.read_text("utf-8"))
    assert env["signature"]["alg"] == "ed25519"
    assert env["signature"]["key_id"] == KID
    assert env["signature"]["signer"] == KID
    assert env["signature"]["key_epoch"] == 1
    # signature is over canonical_bytes(record)
    s["priv"].public_key().verify(
        base64.b64decode(env["signature"]["sig"]), A.canonical_bytes(env["record"])
    )

    # verify-only accepts it and does NOT consume
    assert S.main(_argv(s["tmp"], record_path=out, root=s["root"], pin=s["pin"], verify_only=True)) == 0


def test_refuses_existing_output(setup):
    s = setup
    recp = _write_record(s["tmp"], _record())
    out = s["tmp"] / "signed.json"
    out.write_text("already here", "utf-8")
    key = _write_pem(s["tmp"], s["priv"])
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        S.main(_argv(s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"]))
    assert out.read_text("utf-8") == "already here"


def test_refuses_expired(setup):
    s = setup
    rec = _record(not_before="2020-01-01T00:00:00+00:00", not_after="2021-01-01T00:00:00+00:00")
    recp = _write_record(s["tmp"], rec)
    key = _write_pem(s["tmp"], s["priv"])
    out = s["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="outside validity window"):
        S.main(_argv(s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"]))


def test_refuses_mismatched_manifest_binding(setup):
    s = setup
    recp = _write_record(s["tmp"], _record())
    key = _write_pem(s["tmp"], s["priv"])
    out = s["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="candidate_manifest_digest mismatch"):
        S.main(_argv(
            s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"],
            expect_manifest_sha256="c" * 64,
        ))


def test_refuses_key_not_matching_trust_root(setup):
    s = setup
    recp = _write_record(s["tmp"], _record())
    other_priv, _other_pub = keypair()  # a DIFFERENT key than the trust root
    other_key = _write_pem(s["tmp"], other_priv, name="other.pem")
    out = s["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="does not match the trust-root public key"):
        S.main(_argv(s["tmp"], record_path=recp, out=out, key=other_key, root=s["root"], pin=s["pin"]))
    # fail-closed: the key-match guard fires BEFORE any output is written
    assert not out.exists(), "no signed output must be produced when the key does not match the trust root"


def test_refuses_revoked_approver(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {KID: (priv, pub)}, revoked=(KID,), epochs={KID: 1})
    recp = _write_record(tmp_path, _record())
    key = _write_pem(tmp_path, priv)
    out = tmp_path / "signed.json"
    with pytest.raises(SystemExit, match="revoked"):
        S.main(_argv(tmp_path, record_path=recp, out=out, key=key, root=root, pin=_sha(root)))


def test_verify_only_rejects_tampered_signature(setup):
    s = setup
    recp = _write_record(s["tmp"], _record())
    out = s["tmp"] / "signed.json"
    key = _write_pem(s["tmp"], s["priv"])
    assert S.main(_argv(s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"])) == 0
    env = json.loads(out.read_text("utf-8"))
    raw = bytearray(base64.b64decode(env["signature"]["sig"]))
    raw[0] ^= 0xFF
    env["signature"]["sig"] = base64.b64encode(bytes(raw)).decode()
    tampered = s["tmp"] / "tampered.json"
    tampered.write_text(json.dumps(env), "utf-8")
    with pytest.raises(A.AuthorizationError):
        S.main(_argv(s["tmp"], record_path=tampered, root=s["root"], pin=s["pin"], verify_only=True))


def test_missing_pin_fails_closed(tmp_path, monkeypatch):
    apply_repo_identity(monkeypatch)
    monkeypatch.delenv(A.TRUST_ROOT_DIGEST_ENV, raising=False)
    priv, pub = keypair()
    root = write_trust_root(tmp_path, {KID: (priv, pub)}, epochs={KID: 1})
    recp = _write_record(tmp_path, _record(not_before="2026-01-01T00:00:00+00:00"))
    key = _write_pem(tmp_path, priv)
    out = tmp_path / "signed.json"
    argv = [
        "--record", str(recp), "--out", str(out), "--key", str(key),
        "--trust-root", str(root), "--repo", str(tmp_path),
        "--expect-revision", TEST_REPO_REVISION,
        "--expect-manifest-sha256", "a" * 64,
        "--expect-registration-sha256", "b" * 64,
    ]
    with pytest.raises(SystemExit, match="trust root rejected"):
        S.main(argv)


def test_signing_and_verification_never_consume_or_reserve(setup, monkeypatch):
    """Signing and --verify-only must never consume a grant or reserve budget."""
    from qwen_train import f2_authz_budget as B
    from qwen_train import f2_authz_ledger as L

    def _boom(*_a, **_k):
        raise AssertionError("grant consumption/reservation must not occur during signing/verification")

    monkeypatch.setattr(L.AuthorizationLedger, "consume", _boom, raising=True)
    monkeypatch.setattr(B, "reserve", _boom, raising=True)

    s = setup
    recp = _write_record(s["tmp"], _record())
    out = s["tmp"] / "signed.json"
    key = _write_pem(s["tmp"], s["priv"])
    assert S.main(_argv(s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"])) == 0
    assert S.main(_argv(s["tmp"], record_path=out, root=s["root"], pin=s["pin"], verify_only=True)) == 0


def test_preflight_mode_signs_nothing(setup):
    """--preflight performs checks and exits 0 without creating any output."""
    s = setup
    recp = _write_record(s["tmp"], _record())
    key = _write_pem(s["tmp"], s["priv"])
    out = s["tmp"] / "signed.json"
    argv = _argv(s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"])
    # strip the signing-only args and use --preflight
    argv = [a for a in argv if a not in ("--out", str(out), "--key", str(key))]
    argv.append("--preflight")
    assert S.main(argv) == 0
    assert not out.exists(), "preflight must never create a signed output"


def test_preflight_mode_key_match_and_mismatch(setup):
    s = setup
    recp = _write_record(s["tmp"], _record())
    good = _write_pem(s["tmp"], s["priv"], name="good.pem")
    other_priv, _ = keypair()
    bad = _write_pem(s["tmp"], other_priv, name="bad.pem")
    base = _argv(s["tmp"], record_path=recp, out=s["tmp"] / "x.json", key=good, root=s["root"], pin=s["pin"])
    base = [a for a in base if a not in ("--out", str(s["tmp"] / "x.json"), "--key", str(good))]
    # matching key -> ok
    assert S.main(base + ["--preflight", "--key", str(good)]) == 0
    # mismatched key -> fail closed
    with pytest.raises(SystemExit, match="does not match the trust-root public key"):
        S.main(base + ["--preflight", "--key", str(bad)])
    assert not (s["tmp"] / "x.json").exists()


def test_refuses_wrong_stage(setup):
    s = setup
    recp = _write_record(s["tmp"], _record(stage="S8-001"))
    key = _write_pem(s["tmp"], s["priv"])
    out = s["tmp"] / "signed.json"
    with pytest.raises(SystemExit, match="stage mismatch"):
        S.main(_argv(s["tmp"], record_path=recp, out=out, key=key, root=s["root"], pin=s["pin"]) + ["--stage", "CONF-001"])
