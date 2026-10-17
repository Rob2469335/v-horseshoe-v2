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
    # A built entry must also carry a `workspace` (the runner refuses one that
    # cannot supply its task environment BEFORE consuming the grant), so the
    # manifest here must be otherwise well-formed for these authorization tests.
    p.write_text(json.dumps(
        {"openai__openai-agents-python-1636": {
            "status": "built", "workspace": str(tmp_path / "ws")}}),
        encoding="utf-8")
    return p


def _inputs(tmp_path: Path, task_id: str = "openai__openai-agents-python-1636") -> Path:
    """The per-task input record the build manifest names.

    The runner validates the manifest and resolves the runnable specs BEFORE it
    consumes the grant (F2-IMPL-AUTH-033: "a readiness failure never burns the
    grant"), so a request that never reaches the authorization gate cannot test
    it. These tests are about AUTHORIZATION refusal, so the request must be
    otherwise VALID: a built manifest whose inputs record exists.
    """
    d = tmp_path / "inputs"
    d.mkdir(exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps({
        "repo": "openai/openai-agents-python",
        "base_commit": "0" * 40,
        "test_cmds": ["pytest tests/test_agents.py"],
        "FAIL_TO_PASS": ["tests/test_agents.py::test_x"],
        "PASS_TO_PASS": [],
    }), encoding="utf-8")
    return d


def _provision_governance(monkeypatch, tmp_path: Path) -> None:
    """Synthetic governed-env readiness, so a request can reach the S8-001 gate.

    Validation and READINESS now precede consumption (F2-IMPL-AUTH-033: "a
    readiness failure never burns the grant"), so a test that is about the
    AUTHORIZATION gate must first be a ready request. These values are synthetic
    test fixtures -- no real trust root, grant, evaluator artifact or guest.
    """
    monkeypatch.setenv("SWARM_S8_ARTIFACT_ROOT", str(tmp_path / "store"))
    monkeypatch.setenv("SWARM_S8_ARTIFACT_RETENTION_DAYS", "30")
    monkeypatch.setenv("SWARM_S8_EVALUATOR_ID", "f2_pytest_junit_evaluator")
    monkeypatch.setenv("SWARM_S8_EVALUATOR_VERSION", "1.0.0")
    monkeypatch.setenv("SWARM_S8_EVALUATOR_IMPL", str(tmp_path / "impl.bin"))
    (tmp_path / "impl.bin").write_bytes(b"synthetic evaluator implementation\n")
    monkeypatch.setenv("SWARM_S8_EVALUATOR_PROCEDURE", "f2_pytest_junit_identity_v1")
    monkeypatch.setenv("SWARM_S8_ENVIRONMENT_IDENTITY", "test-environment")
    monkeypatch.setenv("SWARM_S8_BASE_STATE_DIGEST", "1" * 64)
    monkeypatch.setenv("SWARM_S8_GOLD_STATE_DIGEST", "2" * 64)


def _argv(tmp_path: Path, manifest: Path, gates: Path) -> list[str]:
    _inputs(tmp_path)
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
        _provision_governance(monkeypatch, tmp_path)   # readiness, so the gate is reached
        with pytest.raises(SystemExit, match="S8-001"):
            live.main(_argv(tmp_path, _manifest(tmp_path), _gates(tmp_path)))

    def test_refuses_a_grant_bound_to_a_different_manifest(self, tmp_path, monkeypatch):
        manifest = _manifest(tmp_path)
        _provision_governance(monkeypatch, tmp_path)
        _provision_s8_grant(tmp_path, monkeypatch, manifest_sha="0" * 64)  # wrong
        with pytest.raises(SystemExit, match="S8-001"):
            live.main(_argv(tmp_path, manifest, _gates(tmp_path)))

    def test_refuses_a_grant_bound_to_a_different_registration(self, tmp_path, monkeypatch):
        manifest = _manifest(tmp_path)
        sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        _provision_governance(monkeypatch, tmp_path)
        kit.apply_repo_identity(monkeypatch)
        kit.provision_record_env(
            tmp_path, monkeypatch, stage="S8-001", ops=("base_gold_production",),
            binding={"candidate_manifest_digest": sha,
                     "registration_sha256": "a" * 64},   # wrong registration
        )
        with pytest.raises(SystemExit, match="S8-001"):
            live.main(_argv(tmp_path, manifest, _gates(tmp_path)))

    def test_a_grant_cannot_be_replayed(self, tmp_path, monkeypatch):
        """S8-001 is SINGLE-USE, asserted at the CONSUMER boundary.

        Consumption is durable and atomic, so the second use of the same grant is
        refused. This is exercised directly on ``verify_and_consume`` (the
        authorized consumer) rather than through ``main``: the runner now reaches
        consumption only after readiness passes, and past consumption it proceeds
        to guest work, which a unit test must never trigger.
        """
        from qwen_train.f2_authorization import AuthorizationError, verify_and_consume

        manifest = _manifest(tmp_path)
        sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        _provision_s8_grant(tmp_path, monkeypatch, manifest_sha=sha)
        kit.use_test_ledger(monkeypatch, tmp_path)
        binding = {"candidate_manifest_digest": sha, "registration_sha256": REG_SHA}
        verify_and_consume(stage="S8-001", operation="base_gold_production",
                           binding=binding)
        with pytest.raises(AuthorizationError):
            verify_and_consume(stage="S8-001", operation="base_gold_production",
                               binding=binding)

    def test_a_readiness_failure_does_not_consume_the_grant(self, tmp_path, monkeypatch):
        """F2-IMPL-AUTH-033: "a readiness failure never burns the grant".

        With the governed environment absent the run is refused at READINESS,
        before consumption, so the single-use grant is NOT spent: a second
        identical invocation fails the same way rather than reporting an
        already-consumed authorization.
        """
        manifest = _manifest(tmp_path)
        sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        _provision_s8_grant(tmp_path, monkeypatch, manifest_sha=sha)
        for name in ("SWARM_S8_ENVIRONMENT_IDENTITY", "SWARM_S8_ARTIFACT_ROOT"):
            monkeypatch.delenv(name, raising=False)
        argv = _argv(tmp_path, manifest, _gates(tmp_path))
        with pytest.raises(Exception) as first:
            live.main(argv)
        assert "governance inputs are missing" in str(first.value)
        assert "S8-001" not in str(first.value)
        with pytest.raises(Exception) as second:
            live.main(argv)
        assert "governance inputs are missing" in str(second.value)
        assert "already" not in str(second.value).lower()

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
