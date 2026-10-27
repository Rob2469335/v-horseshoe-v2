"""Phase 0 hardening tests for signed-record binding and budget-root safety.

These tests are deliberately isolated: ephemeral Ed25519 keys, a temporary
trust root, and temporary budget roots. They never touch the production ledger.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

import pytest

from qwen_train import f2_authorization as A
from tests.conftest import _REAL_POPEN
from tests.f2_authz_testkit import (
    TEST_REPO_REVISION,
    apply_repo_identity,
    keypair,
    signed_record,
    write_record,
    write_trust_root,
)

MANIFEST = "a" * 64
POOL = "b" * 64
REGISTRATION = "c" * 64
REGISTRATION_MANIFEST = "d" * 64


def _provision(tmp_path, monkeypatch):
    priv, pub = keypair()
    trust = write_trust_root(tmp_path, {"owner": (priv, pub)})
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(trust))
    monkeypatch.setenv(
        "F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(trust)
    )
    apply_repo_identity(monkeypatch)
    record = signed_record(
        stage="S8-001",
        ops=("base_gold_production",),
        schema="f2-authorization/v2",
        repository_revision=TEST_REPO_REVISION,
        candidate_manifest_digest=MANIFEST,
        registration_sha256=REGISTRATION,
        registration_manifest_sha256=REGISTRATION_MANIFEST,
        candidate_pool_digest=POOL,
    )
    return record, write_record(tmp_path, record, priv)


def _binding(**overrides):
    binding = {
        "candidate_manifest_digest": MANIFEST,
        "registration_sha256": REGISTRATION,
        "registration_manifest_sha256": REGISTRATION_MANIFEST,
        "candidate_pool_digest": POOL,
    }
    binding.update(overrides)
    return binding


def _reserve(tmp_path, record_path, *, manifest_digest=MANIFEST, binding=None):
    return A.verify_and_reserve(
        stage="S8-001",
        operation="base_gold_production",
        task_count=1,
        task_ids=["task-1"],
        manifest_digest=manifest_digest,
        binding=_binding() if binding is None else binding,
        budget_ledger_root=tmp_path / "budget",
        record_path=record_path,
    )


def test_missing_registration_manifest_binding_is_refused(tmp_path, monkeypatch):
    _, record_path = _provision(tmp_path, monkeypatch)
    with pytest.raises(A.AuthorizationError, match="registration_manifest_sha256"):
        A.verify_authorization(
            record_path,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_manifest_digest": MANIFEST,
                "registration_sha256": REGISTRATION,
            },
        )


def test_registration_manifest_binding_mismatch_is_refused(tmp_path, monkeypatch):
    _, record_path = _provision(tmp_path, monkeypatch)
    with pytest.raises(A.AuthorizationError, match="registration_manifest_sha256 mismatch"):
        A.verify_authorization(
            record_path,
            stage="S8-001",
            operation="base_gold_production",
            binding=_binding(registration_manifest_sha256="e" * 64),
        )


def test_manifest_digest_must_match_signed_candidate_manifest(tmp_path, monkeypatch):
    _, record_path = _provision(tmp_path, monkeypatch)
    with pytest.raises(A.AuthorizationError, match="manifest_digest mismatch"):
        _reserve(tmp_path, record_path, manifest_digest="evil-manifest")
    assert not (tmp_path / "budget").exists()


def test_matching_manifest_and_pool_are_stored_separately(tmp_path, monkeypatch):
    _, record_path = _provision(tmp_path, monkeypatch)
    _, entry = _reserve(tmp_path, record_path)
    assert entry["manifest_digest"] == MANIFEST
    assert entry["binding"]["candidate_manifest_digest"] == MANIFEST
    assert entry["binding"]["candidate_pool_digest"] == POOL


def test_canonical_bytes_rejects_nan_infinity_and_non_string_keys():
    for bad in ({"x": float("nan")}, {"x": float("inf")}, {1: "value"}):
        with pytest.raises(A.AuthorizationError):
            A.canonical_bytes(bad)
    assert A.canonical_bytes({"b": 1, "a": "x"}) == b'{"a":"x","b":1}'


def test_verify_and_reserve_refuses_symlink_budget_root(tmp_path, monkeypatch):
    _, record_path = _provision(tmp_path, monkeypatch)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted on this host")
    with pytest.raises(A.AuthorizationError, match="symlink or junction"):
        A.verify_and_reserve(
            stage="S8-001",
            operation="base_gold_production",
            task_count=1,
            task_ids=["task-1"],
            manifest_digest=MANIFEST,
            binding=_binding(),
            budget_ledger_root=link,
            record_path=record_path,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_verify_and_reserve_refuses_junction_budget_root(tmp_path, monkeypatch):
    _, record_path = _provision(tmp_path, monkeypatch)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    with patch("subprocess.Popen", new=_REAL_POPEN):
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
    if made.returncode != 0:
        pytest.skip(f"junction creation failed: {made.stderr or made.stdout}")
    try:
        with pytest.raises(A.AuthorizationError, match="symlink or junction"):
            A.verify_and_reserve(
                stage="S8-001",
                operation="base_gold_production",
                task_count=1,
                task_ids=["task-1"],
                manifest_digest=MANIFEST,
                binding=_binding(),
                budget_ledger_root=link,
                record_path=record_path,
            )
    finally:
        with patch("subprocess.Popen", new=_REAL_POPEN):
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
