"""Phase-ops tests: real revision, real manifest digest, both chains, built set."""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qwen_train.f2_s8_factory import AppendOnlyLedger  # noqa: E402
from qwen_train.f2_s8_phase_ops import (  # noqa: E402
    PhaseOpsError,
    build_phase,
    main,
)
from tests.conftest import _REAL_POPEN  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_real_git(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: pathlib.Path) -> dict:
    ws = tmp_path / "ws"
    for task in ("t1", "t2", "networkx__networkx-8207"):
        (ws / task).mkdir(parents=True, exist_ok=True)
    build = {
        "t1": {"status": "built", "workspace": str(ws / "t1")},
        "t2": {"status": "built", "workspace": str(ws / "t2")},
        "networkx__networkx-8207": {
            "status": "built", "workspace": str(ws / "networkx__networkx-8207")
        },
    }
    manifest = tmp_path / "build_manifest.json"
    manifest.write_text(json.dumps(build), encoding="utf-8")
    registration = tmp_path / "registered_tasks.json"
    registration.write_text(
        json.dumps(["t1", "stanfordnlp__dspy-8739", "networkx__networkx-8207", "t2"]),
        encoding="utf-8",
    )
    chain1 = tmp_path / "chain1.jsonl"
    chain2 = tmp_path / "chain2.jsonl"
    led1 = AppendOnlyLedger(chain1)
    led1.append({"task_id": "networkx__networkx-8207", "outcome": "gold_fail",
                 "outcome_class": "scientific", "admitted": False})
    led2 = AppendOnlyLedger(chain2)
    led2.append({"task_id": "t2", "outcome": "base_fail_gold_pass",
                 "outcome_class": "scientific", "admitted": True})
    return {
        "manifest": manifest, "registration": registration,
        "chain1": chain1, "chain2": chain2,
        "out": tmp_path / "phase.json",
    }


class TestBuildPhase:
    def test_revision_and_manifest_digest_are_real(self, tmp_path):
        fx = _fixture(tmp_path)
        phase = build_phase(
            repo_root=REPO,
            build_manifest_path=fx["manifest"],
            registration_path=fx["registration"],
            registration_id="reg-1",
            registration_sha256="a" * 64,
            ledger_paths=[fx["chain1"], fx["chain2"]],
            out_path=fx["out"],
        )
        expected_revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
        assert phase["repository_revision_at_generation"] == expected_revision
        assert len(phase["repository_revision_at_generation"]) == 40
        assert phase["candidate_manifest_digest"] == _sha(fx["manifest"])
        assert phase["candidate_manifest_digest"] != "__sha256 of THIS phase's build manifest__"
        assert phase["grant_binding_required"]["repository_revision"] == expected_revision
        assert phase["grant_binding_required"]["candidate_manifest_digest"] == _sha(fx["manifest"])

    def test_only_the_built_set_is_scheduled(self, tmp_path):
        fx = _fixture(tmp_path)
        phase = build_phase(
            repo_root=REPO, build_manifest_path=fx["manifest"],
            registration_path=fx["registration"], registration_id="reg-1",
            registration_sha256="a" * 64,
            ledger_paths=[fx["chain1"], fx["chain2"]], out_path=fx["out"],
        )
        selected = [t["task_id"] for t in phase["tasks"]]
        assert selected == ["t1"]
        assert "stanfordnlp__dspy-8739" not in selected

    def test_both_chains_are_consulted(self, tmp_path):
        fx = _fixture(tmp_path)
        phase = build_phase(
            repo_root=REPO, build_manifest_path=fx["manifest"],
            registration_path=fx["registration"], registration_id="reg-1",
            registration_sha256="a" * 64,
            ledger_paths=[fx["chain1"], fx["chain2"]], out_path=fx["out"],
        )
        # t2 is admitted only in the second chain; it must be skipped.
        assert "t2" not in [t["task_id"] for t in phase["tasks"]]

    def test_the_size_cap_is_ten(self, tmp_path):
        fx = _fixture(tmp_path)
        phase = build_phase(
            repo_root=REPO, build_manifest_path=fx["manifest"],
            registration_path=fx["registration"], registration_id="reg-1",
            registration_sha256="a" * 64,
            ledger_paths=[fx["chain1"], fx["chain2"]], out_path=fx["out"],
            size=99,
        )
        assert phase["selection"]["selected"] <= 10

    def test_an_empty_phase_is_refused(self, tmp_path):
        fx = _fixture(tmp_path)
        fx["registration"].write_text(
            json.dumps(["networkx__networkx-8207"]), encoding="utf-8")
        with pytest.raises(PhaseOpsError):
            build_phase(
                repo_root=REPO, build_manifest_path=fx["manifest"],
                registration_path=fx["registration"], registration_id="reg-1",
                registration_sha256="a" * 64,
                ledger_paths=[fx["chain1"]], out_path=fx["out"],
            )

    def test_no_ledger_is_mutated(self, tmp_path):
        fx = _fixture(tmp_path)
        before = {str(p): _sha(p) for p in (fx["chain1"], fx["chain2"])}
        build_phase(
            repo_root=REPO, build_manifest_path=fx["manifest"],
            registration_path=fx["registration"], registration_id="reg-1",
            registration_sha256="a" * 64,
            ledger_paths=[fx["chain1"], fx["chain2"]], out_path=fx["out"],
        )
        after = {str(p): _sha(p) for p in (fx["chain1"], fx["chain2"])}
        assert before == after


class TestCli:
    def test_pre_writes_a_phase_and_returns_zero(self, tmp_path):
        fx = _fixture(tmp_path)
        rc = main([
            "pre", "--repo-root", str(REPO),
            "--build-manifest", str(fx["manifest"]),
            "--registration", str(fx["registration"]),
            "--registration-id", "reg-1",
            "--registration-sha256", "a" * 64,
            "--ledger", str(fx["chain1"]), "--ledger", str(fx["chain2"]),
            "--out", str(fx["out"]),
        ])
        assert rc == 0
        assert fx["out"].is_file()

    def test_an_empty_phase_returns_two(self, tmp_path):
        fx = _fixture(tmp_path)
        fx["registration"].write_text(
            json.dumps(["networkx__networkx-8207"]), encoding="utf-8")
        rc = main([
            "pre", "--repo-root", str(REPO),
            "--build-manifest", str(fx["manifest"]),
            "--registration", str(fx["registration"]),
            "--registration-id", "reg-1",
            "--registration-sha256", "a" * 64,
            "--ledger", str(fx["chain1"]), "--out", str(fx["out"]),
        ])
        assert rc == 2
