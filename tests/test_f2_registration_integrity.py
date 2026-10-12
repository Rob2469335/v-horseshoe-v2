"""Registration integrity: TOCTOU-free authorization and acquired-input
binding on the PRODUCTION path (``build_manifest`` itself, not a helper).

The owner-signing fixture is a LAZY oracle over the real verifier: it signs
whatever evidence the guard demands (the owner signs the artifacts it
approves); signature, scope, dates, binding equality, repository revision,
and revocation are all the REAL checks.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train import f2_authorization as A
from qwen_train import f2_registration as REG
from tests.f2_authz_testkit import apply_repo_identity

_S8 = "S8_evidence_provenance"


def _row(iid: str) -> dict:
    return {
        "instance_id": iid,
        "created_at": "2025-06-01T00:00:00",
        "repo": "o/a",
        "problem_statement": "x",
        "patch": "diff",
        "test_cmd": "pytest -q",
        "FAIL_TO_PASS": ["tests/test_x.py::test_a"],
        "PASS_TO_PASS": ["tests/test_x.py::test_b"],
    }


def _write_inputs(tmp: Path) -> tuple[Path, Path]:
    """Write a valid union report + one acquired source (jsonl + provenance)."""
    src = tmp / "acq_live"
    src.mkdir()
    jsonl = src / "acquired.jsonl"
    jsonl.write_text("\n".join(json.dumps(_row(i)) for i in ("a__ok-1", "b__yml-1")) + "\n",
                     encoding="utf-8")
    (src / "PROVENANCE.json").write_text(json.dumps({
        "source_key": "swe-bench-live",
        "normalised_sha256": hashlib.sha256(jsonl.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    report = tmp / "union_report.json"
    report.write_text(json.dumps({
        "total_raw_rows": 2, "exclusions": 0, "total_accepted_after_union": 2,
        "screen_rejections": [
            {"instance_id": "a__ok-1", "source": "swe-bench-live",
             "failing_rules": [_S8]},
            {"instance_id": "b__yml-1", "source": "swe-bench-live",
             "failing_rules": [_S8]},
        ],
    }), encoding="utf-8")
    return report, jsonl


@pytest.fixture
def reg_env(tmp_path, monkeypatch):
    """Ephemeral trust root + pin + record + owner-signing oracle over the
    REAL verifier; captures every binding the guard demands."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    import datetime as dt

    apply_repo_identity(monkeypatch)
    priv = Ed25519PrivateKey.generate()
    pub_b64 = __import__("base64").b64encode(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    root = tmp_path / "trust_root.json"
    root.write_text(json.dumps({"signers": {"owner": pub_b64}}), "utf-8")
    now = dt.datetime.now(dt.timezone.utc)
    template = {
        "id": "TEST-REG-INTEG", "schema": A.SCHEMA, "stage": "POP-001",
        "operations": ["register"], "approver": "owner",
        "not_before": (now - dt.timedelta(minutes=1)).isoformat(),
        "not_after": (now + dt.timedelta(days=1)).isoformat(),
        "protocol_digest": "d",
        "repository_revision": "e" * 40,
    }
    recp = tmp_path / "auth.json"
    demanded: list[dict] = []
    on_verify = {"fn": None}

    def _sign_then_verify(record_path, **kw):
        binding = dict(kw.get("binding") or {})
        demanded.append(binding)
        rec = dict(template)
        rec.update(binding)
        sig = priv.sign(A.canonical_bytes(rec))
        Path(str(record_path)).write_text(json.dumps({"record": rec, "signature": {
            "alg": "ed25519", "key_id": "owner", "signer": "owner",
            "sig": __import__("base64").b64encode(sig).decode()}}), "utf-8")
        if on_verify["fn"] is not None:
            fn, on_verify["fn"] = on_verify["fn"], None
            fn()  # simulated concurrent attacker running DURING verification
        return real_verify(record_path, **kw)

    real_verify = A.verify_authorization
    recp.write_text(json.dumps({"record": template, "signature": {
        "alg": "ed25519", "key_id": "owner", "signer": "owner",
        "sig": __import__("base64").b64encode(
            priv.sign(A.canonical_bytes(template))).decode()}}), "utf-8")
    monkeypatch.setattr(A, "verify_authorization", _sign_then_verify)
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(root))
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(root))
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(recp))

    class _Env:
        pass

    env = _Env()
    env.tmp = tmp_path
    env.demanded = demanded
    env.on_verify = on_verify
    env.priv = priv
    env.real_verify = real_verify  # the UNWRAPPED verifier (bypasses the oracle)
    return env


def test_authorized_digest_equals_parsed_bytes_digest(reg_env):
    report, jsonl = _write_inputs(reg_env.tmp)
    manifest, ordered, excluded = REG.build_manifest(
        union_report=report, acquired_paths=[jsonl], generated_at="T")
    file_digest = hashlib.sha256(report.read_bytes()).hexdigest()
    # The digest the OWNER signed is exactly the digest of the file bytes and
    # exactly what the manifest records.
    signed = reg_env.demanded[-1]
    assert signed["population_manifest_digest"] == file_digest
    assert manifest["inputs"]["union_report_sha256"] == file_digest
    assert {r["instance_id"] for r in ordered} == {"a__ok-1", "b__yml-1"}


def test_union_report_swap_after_read_cannot_substitute_parsed_content(reg_env):
    """Simulate a concurrent attacker replacing the union report AFTER it was
    read (the swap fires inside verification, i.e., after the single read):
    the parse must still reflect the ORIGINAL authorized bytes."""
    report, jsonl = _write_inputs(reg_env.tmp)
    original = report.read_text(encoding="utf-8")

    def _attack():
        # Different eligibility universe: b__yml-1 becomes metadata-rejected.
        report.write_text(json.dumps({
            "total_raw_rows": 2, "exclusions": 0, "total_accepted_after_union": 2,
            "screen_rejections": [
                {"instance_id": "a__ok-1", "source": "swe-bench-live",
                 "failing_rules": [_S8]},
            ],
        }), encoding="utf-8")

    reg_env.on_verify["fn"] = _attack
    manifest, ordered, excluded = REG.build_manifest(
        union_report=report, acquired_paths=[jsonl], generated_at="T")
    assert manifest["counts"]["metadata_eligible"] == 2  # ORIGINAL bytes parsed
    assert {r["instance_id"] for r in ordered} == {"a__ok-1", "b__yml-1"}
    # And the file on disk is now DIFFERENT from what was consumed: a reread
    # would have produced different content. The manifest digest still names
    # the ORIGINAL bytes.
    assert report.read_text(encoding="utf-8") != original
    assert manifest["inputs"]["union_report_sha256"] == hashlib.sha256(
        original.encode("utf-8")).hexdigest()


def test_acquired_swap_after_read_cannot_substitute_parsed_declarations(reg_env):
    report, jsonl = _write_inputs(reg_env.tmp)

    def _attack():
        # Replace the declarations with an empty file: a reread would drop
        # every FAIL_TO_PASS/PASS_TO_PASS and register nothing.
        jsonl.write_text("\n", encoding="utf-8")

    reg_env.on_verify["fn"] = _attack
    manifest, ordered, excluded = REG.build_manifest(
        union_report=report, acquired_paths=[jsonl], generated_at="T")
    assert {r["instance_id"] for r in ordered} == {"a__ok-1", "b__yml-1"}
    assert manifest["counts"]["registered"] == 2


def test_missing_provenance_fails_closed_before_authorization(reg_env):
    report, jsonl = _write_inputs(reg_env.tmp)
    (jsonl.parent / "PROVENANCE.json").unlink()
    with pytest.raises(REG.RegistrationError, match="provenance"):
        REG.build_manifest(union_report=report, acquired_paths=[jsonl])
    assert reg_env.demanded == []  # denied BEFORE the guard was even reached


def test_provenance_digest_mismatch_fails_closed(reg_env):
    report, jsonl = _write_inputs(reg_env.tmp)
    jsonl.write_text(jsonl.read_text("utf-8") + "\n", encoding="utf-8")
    with pytest.raises(REG.RegistrationError, match="digest mismatch"):
        REG.build_manifest(union_report=report, acquired_paths=[jsonl])
    assert reg_env.demanded == []


def test_swapped_file_and_provenance_together_still_denied_by_signature(reg_env):
    """Rewriting BOTH the acquired file and its colocated provenance keeps the
    consistency check green - the OWNER-SIGNED acquired_inputs_digest is the
    only anchor that notices. The mechanism: the post-attack set's digest
    differs from the pre-attack digest the owner signed, so a record bound to
    the pre-attack set cannot authorize the post-attack set."""
    report, jsonl = _write_inputs(reg_env.tmp)
    pre_digest = REG._acquired_inputs_digest({
        "acq_live": hashlib.sha256(jsonl.read_bytes()).hexdigest()})
    # Attacker rewrites the jsonl AND regenerates a consistent provenance.
    jsonl.write_text("\n".join(json.dumps(_row(i)) for i in ("EVIL__ok-9",)) + "\n",
                     encoding="utf-8")
    (jsonl.parent / "PROVENANCE.json").write_text(json.dumps({
        "source_key": "swe-bench-live",
        "normalised_sha256": hashlib.sha256(jsonl.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    post_digest = REG._acquired_inputs_digest({
        "acq_live": hashlib.sha256(jsonl.read_bytes()).hexdigest()})
    assert post_digest != pre_digest
    # A record the owner signed for the PRE-attack set denies the POST-attack
    # set at the guard (binding mismatch), even though provenance is consistent.
    from tests.f2_authz_testkit import signed_record, write_record

    rec = signed_record("POP-001", ("register",),
                        population_manifest_digest="u" * 64,
                        acquired_inputs_digest=pre_digest)
    p = write_record(reg_env.tmp, rec, reg_env.priv, name="auth_pre.json")
    with pytest.raises(A.AuthorizationError, match="acquired_inputs_digest"):
        reg_env.real_verify(
            p, stage="POP-001", operation="register",
            binding={"population_manifest_digest": "u" * 64,
                     "acquired_inputs_digest": post_digest})


def test_record_missing_acquired_inputs_digest_denied(reg_env):
    """A compliant signature over a record that lacks the acquired-inputs
    binding cannot authorize registration (required binding missing)."""
    from tests.f2_authz_testkit import signed_record, write_record

    full = signed_record("POP-001", ("register",),
                         population_manifest_digest="u" * 64,
                         acquired_inputs_digest="a" * 64)
    del full["acquired_inputs_digest"]
    p = write_record(reg_env.tmp, full, reg_env.priv, name="auth_noacq.json")
    with pytest.raises(A.AuthorizationError, match="acquired_inputs_digest"):
        reg_env.real_verify(
            p, stage="POP-001", operation="register",
            binding={"population_manifest_digest": "u" * 64,
                     "acquired_inputs_digest": "a" * 64})


def test_duplicate_acquired_path_rejected(reg_env):
    report, jsonl = _write_inputs(reg_env.tmp)
    with pytest.raises(REG.RegistrationError, match="duplicate acquired input"):
        REG.build_manifest(union_report=report, acquired_paths=[jsonl, jsonl])


def test_duplicate_source_dir_name_rejected(reg_env):
    report, jsonl = _write_inputs(reg_env.tmp)
    other = reg_env.tmp / "other" / "acq_live"  # SAME parent name
    other.mkdir(parents=True)
    jsonl2 = other / "acquired.jsonl"
    jsonl2.write_text(jsonl.read_text("utf-8"), encoding="utf-8")
    (other / "PROVENANCE.json").write_text(
        (jsonl.parent / "PROVENANCE.json").read_text("utf-8"), encoding="utf-8")
    with pytest.raises(REG.RegistrationError, match="ambiguous"):
        REG.build_manifest(union_report=report, acquired_paths=[jsonl, jsonl2])


def test_missing_acquired_file_fails_closed(reg_env):
    report, jsonl = _write_inputs(reg_env.tmp)
    jsonl.unlink()
    with pytest.raises(REG.RegistrationError, match="unreadable"):
        REG.build_manifest(union_report=report, acquired_paths=[jsonl])
    assert reg_env.demanded == []


def test_no_authorization_means_no_registration_work(reg_env, monkeypatch):
    """Without a record the guard denies before any parse; nothing downstream
    (metadata load, declarations, ordering) may run."""
    report, jsonl = _write_inputs(reg_env.tmp)
    monkeypatch.delenv("F2_AUTHORIZATION_RECORD", raising=False)
    called = {"meta": False}
    real_meta = REG.load_eligible_created_at

    def _spy(**kw):
        called["meta"] = True
        return real_meta(**kw)

    monkeypatch.setattr(REG, "load_eligible_created_at", _spy)
    with pytest.raises(A.AuthorizationError):
        REG.build_manifest(union_report=report, acquired_paths=[jsonl])
    assert called["meta"] is False
