"""Adversarial tests for the fail-closed F2 authorization verifier.

No protected scientific operation is executed. Signing uses an ephemeral test
key + a temp trust root in a temporary directory — this is an isolated test
fixture, NOT a production trust root, and cannot authorise real execution.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from qwen_train import f2_authorization as A
from tests.f2_authz_testkit import (
    apply_repo_identity,
    keypair as _keypair,
    signed_record as _signed_record,
    write_record as _write_record,
    write_trust_root as _write_trust,
)


def _now():
    return dt.datetime.now(dt.timezone.utc)


@pytest.fixture(autouse=True)
def _pinned_repo_identity(monkeypatch):
    """All tests in this module pin repository_identity; the REAL git
    behaviour is covered in test_f2_authz_binding_and_replay.py."""
    apply_repo_identity(monkeypatch)


def _write(tmp: Path, record: dict, priv, key_id="owner") -> Path:
    return _write_record(tmp, record, priv, key_id=key_id)


def _trust(tmp: Path, key_id="owner", pub="") -> Path:
    return _write_trust(tmp, {key_id: (None, pub)})


def _record(stage="POP-001", ops=("acquire",), **over):
    return _signed_record(stage, ops, **over)


def _expect_deny(fn):
    with pytest.raises(A.AuthorizationError):
        fn()


# ---- happy path (test-only key + temp trust root) ---------------------------

def test_valid_signed_record_allows(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    rec = _record()
    p = _write(tmp_path, rec, priv)
    got = A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root
    )
    assert got["id"] == "AUTH-TEST-1"


# ---- fail-closed: no trust root ---------------------------------------------

def test_no_trust_root_denies_every_stage(tmp_path):
    priv, _ = _keypair()
    rec = _record()
    p = _write(tmp_path, rec, priv)
    for stage, op in (("POP-001", "acquire"), ("EXPL-001", "exploratory_run"),
                      ("CONF-001", "confirmatory_run")):
        _expect_deny(lambda s=stage, o=op: A.verify_authorization(
            p, stage=s, operation=o, trust_root=tmp_path / "absent.json"))


# ---- malformed / schema -----------------------------------------------------

def test_missing_record(tmp_path):
    _expect_deny(lambda: A.verify_authorization(
        tmp_path / "nope.json", stage="POP-001", operation="acquire"))


def test_malformed_record(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json", "utf-8")
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire"))


def test_unsupported_schema(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(schema="f2-authorization/v0"), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


# ---- stage / scope ----------------------------------------------------------

def test_wrong_stage(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(stage="POP-001", ops=("acquire",)), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="CONF-001", operation="confirmatory_run", trust_root=root))


def test_scope_escalation_rejected(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    # POP-001 record claiming confirmatory scope must be rejected.
    p = _write(tmp_path, _record(stage="POP-001", ops=("confirmatory_run",)), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="confirmatory_run", trust_root=root))


def test_operation_out_of_scope(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(stage="EXPL-001", ops=("exploratory_run",)), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="EXPL-001", operation="confirmatory_run", trust_root=root))


# ---- digest binding ---------------------------------------------------------

def test_protocol_digest_mismatch(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire",
        protocol_digest="different", trust_root=root))


def test_population_digest_mismatch(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(population_digest="pop-sha"), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire",
        population_digest="other", trust_root=root))


# ---- time bounds ------------------------------------------------------------

def test_expired(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    old = _now() - dt.timedelta(days=10)
    p = _write(tmp_path, _record(
        not_before=(old - dt.timedelta(days=1)).isoformat(),
        not_after=(old).isoformat()), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


def test_not_yet_valid(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    fut = _now() + dt.timedelta(days=1)
    p = _write(tmp_path, _record(
        not_before=fut.isoformat(),
        not_after=(fut + dt.timedelta(days=1)).isoformat()), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


# ---- revocation / supersession ----------------------------------------------

def test_revoked_flag(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(revoked=True), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


def test_revoked_list(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(id="REV-9"), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire",
        revoked_ids=("REV-9",), trust_root=root))


def test_superseded(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(superseded_by="AUTH-TEST-2"), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


# ---- signature / trust ------------------------------------------------------

def test_invalid_signature(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    rec = _record()
    p = _write(tmp_path, rec, priv)
    # tamper the signed record after signing
    doc = json.loads(p.read_text("utf-8"))
    doc["record"]["operations"] = ["acquire", "register"]
    p.write_text(json.dumps(doc), "utf-8")
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


def test_untrusted_signer(tmp_path):
    priv, _ = _keypair()
    _, other_pub = _keypair()
    root = _trust(tmp_path, pub=other_pub)  # root trusts a different key
    p = _write(tmp_path, _record(), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


def test_approver_signer_mismatch(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    rec = _record()
    p = _write(tmp_path, rec, priv)
    doc = json.loads(p.read_text("utf-8"))
    doc["signature"]["signer"] = "someone-else"
    p.write_text(json.dumps(doc), "utf-8")
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire", trust_root=root))


# ---- self-declared status is not authorization ------------------------------

def test_status_field_is_not_authorization(tmp_path):
    rec = _record(status="APPROVED")
    p = tmp_path / "unsigned.json"
    p.write_text(json.dumps({"record": rec}), "utf-8")  # no signature block
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire",
        trust_root=tmp_path / "absent.json"))


# ---- central guard ----------------------------------------------------------

def test_require_authorized_denies_without_record(tmp_path, monkeypatch):
    monkeypatch.delenv("F2_AUTHORIZATION_RECORD", raising=False)
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(tmp_path / "absent.json"))
    _expect_deny(lambda: A.require_authorized(stage="CONF-001", operation="confirmatory_run"))


# ---- production entry points enforce the guard (fail-closed) ----------------

def test_acquire_entrypoint_denied_without_authorization(tmp_path, monkeypatch):
    """Direct call to the acquisition entry point is denied (no download happens)."""
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(tmp_path / "absent.json"))
    monkeypatch.delenv("F2_AUTHORIZATION_RECORD", raising=False)
    from qwen_train import f2_population_acquire as acq

    _expect_deny(lambda: acq.acquire("swe_rebench_v2", tmp_path / "out"))


def test_build_manifest_entrypoint_denied_without_authorization(tmp_path, monkeypatch):
    """Direct call to the registration entry point is denied (no admission happens)."""
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(tmp_path / "absent.json"))
    monkeypatch.delenv("F2_AUTHORIZATION_RECORD", raising=False)
    from qwen_train import f2_registration as reg

    union = tmp_path / "x.json"
    union.write_text("{}", "utf-8")  # digest is recomputed, then the guard denies
    _expect_deny(lambda: reg.build_manifest(union_report=union, acquired_paths=[]))


def test_pop_record_cannot_authorize_confirmatory(tmp_path):
    """A POP-001 record cannot authorise a CONF-001 operation (scope separation)."""
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(stage="POP-001", ops=("acquire",)), priv)
    _expect_deny(lambda: A.verify_authorization(
        p, stage="CONF-001", operation="confirmatory_run", trust_root=root))


def test_duplicate_json_keys_rejected(tmp_path):
    """Duplicate critical keys in a record are rejected (ambiguity)."""
    raw = ('{"record":{"id":"x","schema":"' + A.SCHEMA + '","stage":"POP-001",'
           '"operations":["acquire"],"approver":"owner","not_before":"2020-01-01T00:00:00+00:00",'
           '"not_after":"2099-01-01T00:00:00+00:00","protocol_digest":"d","id":"y"},'
           '"signature":{"alg":"ed25519","key_id":"owner","signer":"owner","sig":"AA=="}}')
    p = tmp_path / "dup.json"
    p.write_text(raw, "utf-8")
    _expect_deny(lambda: A.verify_authorization(
        p, stage="POP-001", operation="acquire",
        trust_root=tmp_path / "absent.json"))



# ---- TOCTOU: immutable verified snapshot ------------------------------------

def test_verify_and_bind_returns_immutable_snapshot(tmp_path):
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(), priv)
    v = A.verify_and_bind(stage="POP-001", operation="acquire",
                          record_path=p, trust_root=root)
    assert v.record["id"] == "AUTH-TEST-1"
    with pytest.raises(TypeError):
        v.record["id"] = "tampered"          # MappingProxyType is read-only
    doc = json.loads(p.read_text("utf-8"))   # mutate the file AFTER verification
    doc["record"]["id"] = "changed"
    p.write_text(json.dumps(doc), "utf-8")
    assert v.record["id"] == "AUTH-TEST-1"   # snapshot unaffected
    assert v.digest == A.record_digest(dict(v.record))


# ---- EXPL/CONF entry-point guards -------------------------------------------

def test_run_f2_arm_denied_without_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(tmp_path / "absent.json"))
    monkeypatch.delenv("F2_AUTHORIZATION_RECORD", raising=False)
    from qwen_train import f2_arm_orchestrator as orch

    _expect_deny(lambda: orch.run_f2_arm(
        arm="T", manifest_dir=tmp_path, worker_script=tmp_path / "w.py",
        repo_root=tmp_path, render_t=None, lesson_l_id="l", lesson_l_hash="h",
        task_id="t", execute=True))


def test_start_real_p2_production_model_denied_without_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(tmp_path / "absent.json"))
    monkeypatch.delenv("F2_AUTHORIZATION_RECORD", raising=False)
    from qwen_train import f2_execution_adapter as ad

    # The lowest boundary loads the manifest first (read-only), then enforces
    # authorization; stub the manifest seam so the auth guard is what denies.
    artifact = type("Artifact", (), {"task_readiness": {}, "task_id": "task",
                                     "experiment_id": "experiment_j",
                                     "protocol_version": "f2_v1", "arm": "X"})()
    monkeypatch.setattr(ad, "load_manifest", lambda _path: artifact)
    monkeypatch.setattr(ad, "verify_manifest", lambda _a: None)
    monkeypatch.setattr(ad, "enforce_readiness_from_manifest_payload", lambda *a, **k: None)

    _expect_deny(lambda: ad.start_real_p2_production_model(
        attempt_id="a", repo_root=tmp_path, workspace_root=tmp_path, port=8211,
        work_dir=tmp_path, manifest_path=tmp_path / "m.json",
        rollout_id="r", trajectory_run_id="t", stage="CONF-001",
        operation="confirmatory_run"))


@pytest.mark.parametrize(
    ("stage", "operation"),
    (("EXPL-001", "exploratory_run"), ("CONF-001", "confirmatory_run")),
)
def test_execution_stage_operation_pairs_are_explicit(stage, operation):
    A.validate_execution_stage_operation(stage, operation)


@pytest.mark.parametrize(
    ("stage", "operation"),
    (("EXPL-001", "confirmatory_run"), ("CONF-001", "exploratory_run"),
     ("POP-001", "acquire"), ("unknown", "confirmatory_run")),
)
def test_execution_stage_operation_mismatch_is_rejected(stage, operation):
    _expect_deny(lambda: A.validate_execution_stage_operation(stage, operation))


def test_adapter_rejects_exploratory_at_confirmatory_only_seam(tmp_path, monkeypatch):
    """The lowest model-launch seam preserves the caller's explicit EXPL pair."""
    from qwen_train import f2_execution_adapter as ad

    seen = {}
    monkeypatch.setattr(A, "require_authorized", lambda **kw: seen.update(kw))
    monkeypatch.setattr(ad, "load_manifest", lambda _path: object())
    monkeypatch.setattr(ad, "verify_manifest", lambda _artifact: None)
    monkeypatch.setattr(
        ad, "enforce_readiness_from_manifest_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(ad, "_start_real_p2", lambda **kw: {"spawn_args": kw})
    artifact = type("Artifact", (), {"task_readiness": {}, "task_id": "task"})()
    monkeypatch.setattr(ad, "load_manifest", lambda _path: artifact)

    _expect_deny(lambda: ad.start_real_p2_production_model(
        attempt_id="a", repo_root=tmp_path, workspace_root=tmp_path, port=8211,
        work_dir=tmp_path, manifest_path=tmp_path / "m.json", rollout_id="r",
        trajectory_run_id="t", stage="EXPL-001", operation="exploratory_run",
    ))
    assert seen == {}  # exploratory never reaches the confirmatory-only seam


def test_adapter_rejects_mismatch_before_manifest_or_spawn(tmp_path, monkeypatch):
    from qwen_train import f2_execution_adapter as ad

    touched = []
    monkeypatch.setattr(ad, "load_manifest", lambda _path: touched.append("manifest"))
    monkeypatch.setattr(ad, "_start_real_p2", lambda **_kw: touched.append("spawn"))
    _expect_deny(lambda: ad.start_real_p2_production_model(
        attempt_id="a", repo_root=tmp_path, workspace_root=tmp_path, port=8211,
        work_dir=tmp_path, manifest_path=tmp_path / "m.json", rollout_id="r",
        trajectory_run_id="t", stage="EXPL-001", operation="confirmatory_run",
    ))
    assert touched == []


# ---- empty / whitespace / directory authorization paths ---------------------

def test_require_authorized_empty_record_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", "")
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(tmp_path / "x.json"))
    _expect_deny(lambda: A.require_authorized(stage="CONF-001", operation="confirmatory_run"))


def test_require_authorized_whitespace_record_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", "   ")
    _expect_deny(lambda: A.require_authorized(stage="CONF-001", operation="confirmatory_run"))


def test_verify_authorization_directory_path_denied(tmp_path):
    _expect_deny(lambda: A.verify_authorization(tmp_path, stage="CONF-001", operation="confirmatory_run"))


# ---- production P2 boundary is confirmatory-only ----------------------------

def _p2_call(tmp_path, stage="CONF-001", operation="confirmatory_run"):
    from qwen_train import f2_execution_adapter as ad
    return ad.start_real_p2_production_model(
        attempt_id="a", repo_root=tmp_path, workspace_root=tmp_path, port=8211,
        work_dir=tmp_path, manifest_path=tmp_path / "m.json",
        rollout_id="r", trajectory_run_id="t", stage=stage, operation=operation)


def test_production_p2_rejects_expl_authorization(tmp_path, monkeypatch):
    """An EXPL-001 record must NOT satisfy the confirmatory-only adapter."""
    priv, pub = _keypair()
    root = _trust(tmp_path, pub=pub)
    p = _write(tmp_path, _record(stage="EXPL-001", ops=("exploratory_run",)), priv)
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(root))
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(p))
    _expect_deny(lambda: _p2_call(tmp_path, "EXPL-001", "exploratory_run"))


def test_production_p2_confirmatory_authorization_passes_guard_no_spawn(tmp_path, monkeypatch):
    """A CONF-001 record + a durable parent consumption passes the FULL lowest
    boundary; the next step (stubbed spawn seam) raises its own sentinel -
    proving auth AND consumption passed and no protected side effect occurred."""
    from tests.f2_authz_testkit import provision_parent_receipt

    ctx = provision_parent_receipt(tmp_path, monkeypatch, rollout_id="r")
    from qwen_train import f2_execution_adapter as ad

    artifact = type("Artifact", (), {"task_readiness": {}, "task_id": "task",
                                     "experiment_id": ctx["experiment_id"],
                                     "protocol_version": ctx["protocol_version"],
                                     "arm": ctx["arm"]})()
    monkeypatch.setattr(ad, "load_manifest", lambda _path: artifact)
    monkeypatch.setattr(ad, "verify_manifest", lambda _a: None)
    monkeypatch.setattr(ad, "enforce_readiness_from_manifest_payload", lambda *a, **k: None)

    class _Sentinel(Exception):
        pass

    def _boom(**_kw):
        raise _Sentinel("spawn-seam-reached")

    monkeypatch.setattr(ad, "_start_real_p2", _boom)
    with pytest.raises(_Sentinel):
        _p2_call(tmp_path)


def test_production_p2_requires_a_durable_consumption_receipt(tmp_path, monkeypatch):
    """A valid record WITHOUT the parent consumption receipt is still denied
    at the lowest boundary (single-use corroboration is mandatory)."""
    from tests.f2_authz_testkit import provision_record_env

    apply_repo_identity(monkeypatch)
    ctx = provision_record_env(
        tmp_path, monkeypatch,
        stage="CONF-001", ops=("confirmatory_run",),
        binding={"experiment_id": "experiment_j", "protocol_version": "f2_v1",
                 "arm": "X", "rollout_id": "r"},
    )
    from qwen_train import f2_execution_adapter as ad

    artifact = type("Artifact", (), {"task_readiness": {}, "task_id": "task",
                                     "experiment_id": "experiment_j",
                                     "protocol_version": "f2_v1", "arm": "X"})()
    monkeypatch.setattr(ad, "load_manifest", lambda _path: artifact)
    monkeypatch.setattr(ad, "verify_manifest", lambda _a: None)
    monkeypatch.setattr(ad, "enforce_readiness_from_manifest_payload", lambda *a, **k: None)
    monkeypatch.delenv("SWARM_F2_AUTHZ_CONSUMPTION_ID", raising=False)
    _expect_deny(lambda: _p2_call(tmp_path, "CONF-001", "confirmatory_run"))
    assert ctx["record_path"].exists()
