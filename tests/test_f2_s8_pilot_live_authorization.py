"""The S8 live entry point must require a SIGNED S8-001 authorization.

`pilot_gates_phase2.json` (8 caller-supplied booleans) is a PRECONDITION, not
authority: the entry point must refuse before any guest work unless a valid
owner-signed record bound to THIS candidate manifest and THIS registration (and
to the executing repository revision) is presented.  These tests use the
ephemeral signing oracle in `tests/f2_authz_testkit.py`; no real trust root,
grant or guest is involved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train import f2_s8_pilot_live as live
from tests import f2_authz_testkit as kit

REG_SHA = "5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042"


def _gates(tmp_path: Path) -> Path:
    p = tmp_path / "gates.json"
    p.write_text(json.dumps({g: True for g in live.REQUIRED_GATES}), encoding="utf-8")
    return p


def _manifest(tmp_path: Path, name: str = "build_manifest.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"openai__openai-agents-python-1636": {"status": "built"}}),
                 encoding="utf-8")
    return p


def _argv(tmp_path: Path, manifest: Path, gates: Path) -> list[str]:
    return [
        "--manifest", str(manifest),
        "--inputs", str(tmp_path / "inputs"),
        "--registration-id", "evaluator_compat_python_confirmed_v1",
        "--registration-sha256", REG_SHA,
        "--ledger", str(tmp_path / "ledger.jsonl"),
        "--staging", str(tmp_path / "staging"),
        "--out", str(tmp_path / "out"),
        "--gates", str(gates),
    ]


def _provision_s8_grant(tmp_path, monkeypatch, *, manifest_sha: str) -> None:
    kit.apply_repo_identity(monkeypatch)
    kit.provision_record_env(
        tmp_path, monkeypatch, stage="S8-001", ops=("base_gold_production",),
        binding={"candidate_manifest_digest": manifest_sha,
                 "registration_sha256": REG_SHA},
    )


class TestS8LiveAuthorization:
    def test_refuses_without_any_signed_authorization(self, tmp_path, monkeypatch):
        monkeypatch.delenv("F2_AUTHORIZATION_RECORD", raising=False)
        monkeypatch.delenv("F2_AUTHORIZATION_TRUST_ROOT", raising=False)
        monkeypatch.delenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", raising=False)
        with pytest.raises(SystemExit, match="S8-001"):
            live.main(_argv(tmp_path, _manifest(tmp_path), _gates(tmp_path)))

    def test_refuses_a_grant_bound_to_a_different_manifest(self, tmp_path, monkeypatch):
        manifest = _manifest(tmp_path)
        _provision_s8_grant(tmp_path, monkeypatch, manifest_sha="0" * 64)  # wrong
        with pytest.raises(SystemExit, match="S8-001"):
            live.main(_argv(tmp_path, manifest, _gates(tmp_path)))

    def test_refuses_a_grant_bound_to_a_different_registration(self, tmp_path, monkeypatch):
        manifest = _manifest(tmp_path)
        sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        kit.apply_repo_identity(monkeypatch)
        kit.provision_record_env(
            tmp_path, monkeypatch, stage="S8-001", ops=("base_gold_production",),
            binding={"candidate_manifest_digest": sha,
                     "registration_sha256": "a" * 64},   # wrong registration
        )
        with pytest.raises(SystemExit, match="S8-001"):
            live.main(_argv(tmp_path, manifest, _gates(tmp_path)))

    def test_a_grant_cannot_be_replayed(self, tmp_path, monkeypatch):
        """S8-001 is SINGLE-USE: the grant is durably consumed before guest work,
        so a second invocation with the same grant is refused."""
        manifest = _manifest(tmp_path)
        sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        _provision_s8_grant(tmp_path, monkeypatch, manifest_sha=sha)
        for name in ("SWARM_S8_ENVIRONMENT_IDENTITY", "SWARM_S8_ARTIFACT_ROOT"):
            monkeypatch.delenv(name, raising=False)
        argv = _argv(tmp_path, manifest, _gates(tmp_path))
        # First run: authorization is consumed, then the governed env fails closed.
        with pytest.raises(Exception) as first:
            live.main(argv)
        assert "S8-001" not in str(first.value)      # got past authorization
        # Second run with the SAME grant: refused as already consumed.
        with pytest.raises(SystemExit, match="S8-001"):
            live.main(argv)

    def test_a_correctly_bound_grant_passes_authorization(self, tmp_path, monkeypatch):
        """With a correctly bound grant, the entry point gets PAST the S8-001 check
        and fails on the (absent) governed environment instead -- proving the gate
        booleans alone were not what allowed it."""
        manifest = _manifest(tmp_path)
        sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        _provision_s8_grant(tmp_path, monkeypatch, manifest_sha=sha)
        for name in (
            "SWARM_S8_ENVIRONMENT_IDENTITY", "SWARM_S8_BASE_STATE_DIGEST",
            "SWARM_S8_GOLD_STATE_DIGEST", "SWARM_S8_ARTIFACT_ROOT",
        ):
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(Exception) as exc:
            live.main(_argv(tmp_path, manifest, _gates(tmp_path)))
        assert "S8-001" not in str(exc.value)      # authorization SATISFIED
