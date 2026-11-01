"""Tests for `qwen_train.f2_lesson_evidence` and its preflight wiring.

The AUTHORIZATION dimension is exercised with an INDEPENDENT, ephemeral trust root
and key generated inside the test (in a temp dir, via `F2_AUTHORIZATION_TRUST_ROOT`).
The owner's real key is never read, used or required.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from qwen_train.f2_lesson_evidence import (
    ALE_HASH_MISMATCH,
    ALE_INSUFFICIENT_RUNS,
    ALE_INSUFFICIENT_TASKS,
    ALE_LESSON_NOT_IN_MANIFEST,
    ALE_MALFORMED,
    ALE_MANIFEST_INVALID,
    ALE_NOT_AUTHORIZED,
    ALE_NO_CAUSAL_RELEVANCE,
    ALE_POPULATION_MISMATCH,
    ALE_RECEIPT_UNVERIFIED,
    ALE_STALE,
    ALE_UNREADABLE,
    ALE_VERIFIED,
    AUTHORIZATION_KIND,
    AUTHORIZATION_SCHEMA,
    SCHEMA,
    canonical_bytes,
    receipt_verifier_default,
    verify_active_lesson_evidence,
)
from runtime_v2.services.f2_freeze import LessonEntry, freeze_artifact, manifest_to_dict

RULE = "Always inspect the failing test before editing the implementation."
RULE_HASH = __import__("hashlib").sha256(RULE.encode()).hexdigest()
OWNER = "f2-s8-owner-v1"
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
REG = "5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042"
OK_RECEIPT = lambda r, s: s == "deadbeef"  # noqa: E731
OK_MANIFEST = lambda a: None  # noqa: E731


# --------------------------------------------------------------------------- fixtures
@pytest.fixture()
def ephemeral_owner(monkeypatch, tmp_path):
    """A test-only trust root + ed25519 key. Never the owner's."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)).decode()
    root = tmp_path / "trust_root.json"
    root.write_text(json.dumps({
        "revoked_key_ids": [], "revoked_record_ids": [],
        "signers": {OWNER: {"epoch": 1, "public_key": pub_b64}},
    }), encoding="utf-8")
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(root))
    # load_trust_root() is fail-closed on a missing digest pin, so the test must
    # establish it exactly as an operator would.
    import hashlib
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256",
                       hashlib.sha256(root.read_bytes()).hexdigest())

    def sign(record):
        sig = priv.sign(canonical_bytes(record))
        return {"alg": "ed25519", "key_id": OWNER, "key_epoch": 1, "signer": OWNER,
                "sig": base64.b64encode(sig).decode()}
    return sign


def _artifact(lesson_id="L-1", lesson_hash=RULE_HASH):
    return freeze_artifact(
        rendered_artifact="[BEHAVIORAL LESSONS]\n1. " + RULE, arm="T",
        ordered_lessons=(LessonEntry(lesson_id=lesson_id, lesson_hash=lesson_hash,
                                     position=1, rule_text=RULE),),
        lesson_l_id=lesson_id, lesson_l_hash=lesson_hash, git_sha="0" * 40,
        model_name="robs4b", experiment_id="experiment_j",
        protocol_version="f2_experiment_j_v1", promotion_proof_ref="proof:L-1",
        freeze_timestamp=1_700_000_000.0)


def _event(**over):
    ev = {
        "behavioural_runs": ["r1", "r2", "r3"],
        "task_ids": ["t1", "t2", "t3"],
        "causal_relevance": "L bears on the first-edit behaviour the primary endpoint measures",
        "occurred_at": "2026-10-17T10:00:00+00:00",
        "population_binding": {"registration_sha256": REG, "admitted_task_ids": ["t1", "t2", "t3"]},
    }
    ev.update(over)
    return ev


def _doc(sign=None, lesson_id="L-1", lesson_hash=RULE_HASH, event=None,
         recorded_at="2026-10-17T11:00:00+00:00", with_auth=True, authorization=None,
         artifact_lesson_id=None):
    ev = event if event is not None else _event()
    doc = {
        "schema": SCHEMA, "lesson_l_id": lesson_id, "lesson_l_hash": lesson_hash,
        "frozen_t_manifest": manifest_to_dict(
            _artifact(artifact_lesson_id or lesson_id, RULE_HASH)),
        "promotion": {"receipt": {"candidate_id": "c1", "state_hash": "a" * 64},
                      "signature": "deadbeef", "candidate_id": "c1"},
        "learning_event": ev,
        "recorded_at": recorded_at,
    }
    if authorization is not None:
        doc["authorization"] = authorization
    elif with_auth:
        arec = {"schema": AUTHORIZATION_SCHEMA, "kind": AUTHORIZATION_KIND,
                "id": "q12-att-1", "lesson_l_id": lesson_id,
                "learning_event_digest": __import__("hashlib").sha256(canonical_bytes(ev)).hexdigest(),
                "population_registration_sha256": REG, "approver": OWNER}
        doc["authorization"] = {"record": arec,
                                "signature": sign(arec) if sign else {"alg": "ed25519"}}
    return doc


V = dict(receipt_verifier=OK_RECEIPT, manifest_verifier=OK_MANIFEST,
         expected_registration_sha256=REG, now=NOW)


# --------------------------------------------------------------------------- shape
class TestShapeAndFreshness:
    def test_wrong_schema(self):
        assert verify_active_lesson_evidence({"schema": "nope"}, **V).state == ALE_MALFORMED

    def test_not_an_object(self):
        assert verify_active_lesson_evidence(["x"], **V).state == ALE_MALFORMED

    def test_missing_path_is_unreadable_not_malformed(self, tmp_path):
        assert verify_active_lesson_evidence(tmp_path / "nope.json", **V).state == ALE_UNREADABLE

    def test_missing_lesson_identity(self):
        d = _doc(); d.pop("lesson_l_id")
        assert verify_active_lesson_evidence(d, **V).state == ALE_MALFORMED

    def test_missing_recorded_at_is_stale(self):
        assert verify_active_lesson_evidence(_doc(recorded_at=None), **V).state == ALE_STALE

    def test_old_record_is_stale(self):
        old = (NOW - timedelta(days=31)).isoformat()
        ev = _event(occurred_at=(NOW - timedelta(days=32)).isoformat())
        assert verify_active_lesson_evidence(_doc(event=ev, recorded_at=old), **V).state == ALE_STALE

    def test_future_event_is_stale(self):
        ev = _event(occurred_at=(NOW + timedelta(hours=2)).isoformat())
        assert verify_active_lesson_evidence(_doc(event=ev), **V).state == ALE_STALE

    def test_event_after_record_is_stale(self):
        ev = _event(occurred_at="2026-10-17T11:30:00+00:00")
        assert verify_active_lesson_evidence(_doc(event=ev), **V).state == ALE_STALE


# ---------------------------------------------------------------------- consistency
class TestConsistency:
    def test_invalid_manifest_fails_closed(self):
        def boom(_a): raise RuntimeError("tampered")
        v = verify_active_lesson_evidence(_doc(), receipt_verifier=OK_RECEIPT,
                                         manifest_verifier=boom,
                                         expected_registration_sha256=REG, now=NOW)
        assert v.state == ALE_MANIFEST_INVALID and not v.ok

    def test_lesson_not_in_manifest(self):
        # Declared lesson "L-ABSENT", but the frozen artifact holds only "L-1".
        assert verify_active_lesson_evidence(
            _doc(lesson_id="L-ABSENT", artifact_lesson_id="L-1"), **V
        ).state == ALE_LESSON_NOT_IN_MANIFEST

    def test_hash_mismatch(self):
        assert verify_active_lesson_evidence(_doc(lesson_hash="b" * 64), **V).state == ALE_HASH_MISMATCH

    def test_two_runs_rejected(self):
        assert verify_active_lesson_evidence(_doc(event=_event(behavioural_runs=["r1", "r2"])), **V).state == ALE_INSUFFICIENT_RUNS

    def test_duplicate_runs_do_not_count(self):
        d = _doc(event=_event(behavioural_runs=["r1", "r1", "r1"]))
        assert verify_active_lesson_evidence(d, **V).state == ALE_INSUFFICIENT_RUNS

    def test_two_tasks_rejected(self):
        assert verify_active_lesson_evidence(_doc(event=_event(task_ids=["t1", "t2"])), **V).state == ALE_INSUFFICIENT_TASKS

    def test_empty_causal_relevance_rejected(self):
        assert verify_active_lesson_evidence(_doc(event=_event(causal_relevance="  ")), **V).state == ALE_NO_CAUSAL_RELEVANCE

    def test_population_mismatch_rejected(self):
        ev = _event(population_binding={"registration_sha256": "0" * 64, "admitted_task_ids": []})
        assert verify_active_lesson_evidence(_doc(event=ev), **V).state == ALE_POPULATION_MISMATCH


# --------------------------------------------------------------------- authenticity
class TestAuthenticity:
    def test_bad_receipt_signature(self):
        v = verify_active_lesson_evidence(_doc(), receipt_verifier=lambda r, s: False,
                                         manifest_verifier=OK_MANIFEST,
                                         expected_registration_sha256=REG, now=NOW)
        assert v.state == ALE_RECEIPT_UNVERIFIED
        assert v.dimensions["consistency"] == "verified"

    def test_receipt_verifier_exception_is_fail_closed(self):
        def boom(_r, _s): raise RuntimeError("nope")
        v = verify_active_lesson_evidence(_doc(), receipt_verifier=boom,
                                         manifest_verifier=OK_MANIFEST,
                                         expected_registration_sha256=REG, now=NOW)
        assert v.state == ALE_RECEIPT_UNVERIFIED

    def test_default_receipt_verifier_refuses_without_a_key(self, monkeypatch):
        monkeypatch.delenv("SWARM_RECEIPT_KEY", raising=False)
        assert receipt_verifier_default({"a": 1}, "deadbeef") is False


# -------------------------------------------------------------------- authorization
class TestAuthorization:
    """The dimension a rehearsal cannot supply: an owner-signed attestation."""

    def test_consistent_and_authentic_but_unauthorized_is_blocking(self, ephemeral_owner):
        """The core anti-rehearsal property: structure + receipt are NOT enough."""
        v = verify_active_lesson_evidence(_doc(sign=None, with_auth=False), **V)
        assert v.state == ALE_NOT_AUTHORIZED and not v.ok
        assert v.dimensions == {"consistency": "verified", "authenticity": "verified",
                                "authorization": "not-established"}

    def test_wrong_schema_or_kind_rejected(self, ephemeral_owner):
        rec = {"schema": "nope", "kind": AUTHORIZATION_KIND, "approver": OWNER}
        v = verify_active_lesson_evidence(_doc(authorization={"record": rec, "signature": ephemeral_owner(rec)}), **V)
        assert v.state == ALE_NOT_AUTHORIZED

    def test_attestation_bound_to_a_different_lesson_rejected(self, ephemeral_owner):
        rec = {"schema": AUTHORIZATION_SCHEMA, "kind": AUTHORIZATION_KIND, "id": "x",
               "lesson_l_id": "OTHER", "learning_event_digest": "0" * 64,
               "population_registration_sha256": REG, "approver": OWNER}
        v = verify_active_lesson_evidence(_doc(authorization={"record": rec, "signature": ephemeral_owner(rec)}), **V)
        assert v.state == ALE_NOT_AUTHORIZED

    def test_attestation_bound_to_a_different_event_rejected(self, ephemeral_owner):
        rec = {"schema": AUTHORIZATION_SCHEMA, "kind": AUTHORIZATION_KIND, "id": "x",
               "lesson_l_id": "L-1", "learning_event_digest": "0" * 64,
               "population_registration_sha256": REG, "approver": OWNER}
        v = verify_active_lesson_evidence(_doc(authorization={"record": rec, "signature": ephemeral_owner(rec)}), **V)
        assert v.state == ALE_NOT_AUTHORIZED

    def test_tampered_attestation_rejected(self, ephemeral_owner):
        arec = {"schema": AUTHORIZATION_SCHEMA, "kind": AUTHORIZATION_KIND, "id": "x",
                "lesson_l_id": "L-1",
                "learning_event_digest": __import__("hashlib").sha256(canonical_bytes(_event())).hexdigest(),
                "population_registration_sha256": REG, "approver": OWNER}
        sig = ephemeral_owner(arec)
        arec = dict(arec); arec["id"] = "tampered"          # mutate AFTER signing
        v = verify_active_lesson_evidence(_doc(authorization={"record": arec, "signature": sig}), **V)
        assert v.state == ALE_NOT_AUTHORIZED

    def test_revoked_record_rejected(self, ephemeral_owner, tmp_path, monkeypatch):
        root = json.loads((tmp_path / "trust_root.json").read_text(encoding="utf-8"))
        root["revoked_record_ids"] = ["q12-att-1"]
        (tmp_path / "trust_root.json").write_text(json.dumps(root), encoding="utf-8")
        v = verify_active_lesson_evidence(_doc(sign=ephemeral_owner), **V)
        assert v.state == ALE_NOT_AUTHORIZED

    def test_wrong_approver_rejected(self, ephemeral_owner):
        arec = {"schema": AUTHORIZATION_SCHEMA, "kind": AUTHORIZATION_KIND, "id": "x",
                "lesson_l_id": "L-1",
                "learning_event_digest": __import__("hashlib").sha256(canonical_bytes(_event())).hexdigest(),
                "population_registration_sha256": REG, "approver": "someone-else"}
        v = verify_active_lesson_evidence(_doc(authorization={"record": arec, "signature": ephemeral_owner(arec)}), **V)
        assert v.state == ALE_NOT_AUTHORIZED

    def test_untrusted_key_rejected(self, ephemeral_owner, monkeypatch):
        monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", "C:/nonexistent/trust_root.json")
        v = verify_active_lesson_evidence(_doc(sign=ephemeral_owner), **V)
        assert v.state == ALE_NOT_AUTHORIZED

    def test_missing_digest_pin_denies(self, ephemeral_owner, monkeypatch):
        """The trust-root digest pin is fail-closed: an unpinned root must not verify."""
        monkeypatch.delenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", raising=False)
        v = verify_active_lesson_evidence(_doc(sign=ephemeral_owner), **V)
        assert v.state == ALE_NOT_AUTHORIZED
        assert "pin" in v.detail

    def test_verified_path_with_an_independent_trust_root(self, ephemeral_owner):
        v = verify_active_lesson_evidence(_doc(sign=ephemeral_owner), **V)
        assert v.ok and v.state == ALE_VERIFIED
        assert v.dimensions == {"consistency": "verified", "authenticity": "verified",
                                "authorization": "verified"}


# ------------------------------------------------------------------ preflight wiring
class TestPreflightWiring:
    def test_default_stays_absent_and_blocking(self):
        from qwen_train import f2_preflight as pf
        by = {f["id"]: f for f in pf.run_preflight()["findings"]}
        f = by["evidence.active_lesson"]
        assert f["status"] == "absent"
        assert f["category"] == pf.EXTERNAL_EVIDENCE

    def test_a_supplied_unverifiable_record_never_becomes_ready(self, tmp_path):
        from qwen_train import f2_preflight as pf
        rec = tmp_path / "al.json"
        rec.write_text(json.dumps(_doc(sign=None, with_auth=False)), encoding="utf-8")
        rep = pf.run_preflight(active_lesson=rec)
        f = {x["id"]: x for x in rep["findings"]}["evidence.active_lesson"]
        assert f["status"] != "present"
        assert f["category"] == pf.EXTERNAL_EVIDENCE
        assert rep["result"] == "BLOCKED"

    def test_missing_verifier_module_fails_closed(self, monkeypatch, tmp_path):
        from qwen_train import f2_preflight as pf
        import builtins
        real_import = builtins.__import__

        def fake(name, *a, **k):
            if name == "qwen_train.f2_lesson_evidence":
                raise ModuleNotFoundError(name)
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", fake)
        rec = tmp_path / "al.json"
        rec.write_text("{}", encoding="utf-8")
        f = pf._active_lesson_finding(rec)
        assert f.status == "unverifiable" and f.category == pf.EXTERNAL_EVIDENCE
