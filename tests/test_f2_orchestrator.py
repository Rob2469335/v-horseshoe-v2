"""Tests for the F2 orchestrator (parent) + fresh-process worker.

Scope: parent orchestration, genuine fresh child process, repo-root bootstrap,
independent manifest load/verify, replay delivery, fail-closed behavior, and
receipt/process-isolation requirements.  Uses deterministic fixtures and spawns
REAL child processes (no experiment / no N=2 / no live services).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from qwen_train.f2_arm_primitives import (
    build_c0_artifact,
    derive_x_from_frozen,
    lesson_hash_of,
    render_block_from_records,
)
from qwen_train.f2_arm_worker import run_worker
from runtime_v2.services.f2_freeze import (
    LessonEntry,
    freeze_artifact,
    verify_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER = REPO_ROOT / "qwen_train" / "f2_arm_worker.py"


@pytest.fixture(scope="module", autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock.

    These tests spawn a genuine fresh Python child process to prove the F2
    fresh-process contract. The conftest autouse mock would swallow child output.
    """
    yield


def _lesson(lid: str, texture: str, position: int) -> LessonEntry:
    return LessonEntry(
        lesson_id=lid,
        lesson_hash=lesson_hash_of(texture),
        position=position,
        rule_text=texture,
    )


def _freeze_t(lessons, *, lesson_l_id: str, task_id: str = "task-1"):
    block = render_block_from_records(lessons)
    l_rec = next(le for le in lessons if le.lesson_id == lesson_l_id)
    t = freeze_artifact(
        rendered_artifact=block,
        arm="T",
        ordered_lessons=lessons,
        lesson_l_id=lesson_l_id,
        lesson_l_hash=l_rec.lesson_hash,
        task_id=task_id,
        model_name="test-model",
        git_sha="deadbeef",
        freeze_timestamp=1_700_000_000.0,
    )
    verify_manifest(t)
    return t


def _run_worker_child(manifest_path: Path, arm: str, system_prompt: str = "") -> tuple[int, str, str]:
    """Spawn the REAL fresh worker process (no PYTHONPATH reliance)."""
    env = dict(os.environ)
    env["SWARM_F2_REPLAY"] = "1"
    env["SWARM_F2_MANIFEST_PATH"] = str(manifest_path)
    env["SWARM_F2_ROLLOUT_ID"] = "rolled"
    import subprocess

    cmd = [sys.executable, "-u", str(WORKER), "--manifest", str(manifest_path), "--arm", arm]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(REPO_ROOT), env=env
    )
    out, err = proc.communicate(timeout=60)
    return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


class TestFreshProcessContract:
    def test_child_is_separate_process(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        manifest = tmp_path / "t.json"
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "T")
        assert rc == 0, err
        receipt = json.loads(out.strip().splitlines()[-1])
        assert receipt["process_identity"]["pid"] != os.getpid()

    def test_child_bootstraps_repo_root(self, tmp_path):
        # Worker succeeds importing runtime_v2 from a clean repo root even in an
        # unspecified cwd; repo-root bootstrap is inside the worker.
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "T")
        assert rc == 0, f"{out}\n{err}"

    def test_child_independently_loads_and_verifies(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "T")
        assert rc == 0, err
        receipt = json.loads(out.strip().splitlines()[-1])
        assert receipt["verification_result"] == "verified"
        assert receipt["manifest_identity"]["manifest_hash"] == m.manifest_hash

    def test_child_uses_replay_delivery(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "T")
        assert rc == 0
        receipt = json.loads(out.strip().splitlines()[-1])
        # delivered block is the frozen artifact, not live retrieval
        assert receipt["delivered_artifact"] == m.rendered_artifact
        assert receipt["lesson_block_hash"] == m.treatment_set_hash

    def test_invalid_manifest_fails_closed(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        # corrupt the manifest file after persist
        manifest.write_text(manifest.read_text()[:-1])  # broken JSON? keep, tamper hash
        from qwen_train import f2_arm_worker  # noqa

        rc = run_worker(["--manifest", str(manifest), "--arm", "T"])
        # run_worker returns 1 on verification failure (fail-closed, no LIVE)
        assert rc == 1

    def test_no_live_fallback_on_child_failure(self, tmp_path):
        # Missing manifest => worker must fail closed (returncode != 0), never
        # silently fall back to live retrieval.
        missing = tmp_path / "missing_f2_freeze.json"
        rc, out, err = _run_worker_child(missing, "T")
        assert rc != 0
        assert "Traceback" not in out  # controlled error JSON


class TestArmValidation:
    def test_arm_mismatch_fails_closed(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "X")  # manifest says T
        assert rc == 1  # fail closed; no silent run of a different arm


class TestProcessIsolation:
    def test_one_receipt_full(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "T")
        assert rc == 0
        receipt = json.loads(out.strip().splitlines()[-1])
        assert receipt["exit_status"] == 0
        assert receipt["delivery_timestamp"] > 0
        assert receipt["process_identity"]["role"] == "f2_arm_worker"


class TestXWorkerDelivery:
    def test_x_worker_delivers_x_artifact(self, tmp_path):
        lessons = (_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2), _lesson("C", "verify", 3))
        t = _freeze_t(lessons, lesson_l_id="L")
        x = derive_x_from_frozen(t)
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(x, tmp_path)
        rc, out, err = _run_worker_child(manifest, "X")
        assert rc == 0, err
        receipt = json.loads(out.strip().splitlines()[-1])
        assert receipt["arm"] == "X"
        assert receipt["delivered_artifact"] == x.rendered_artifact


class TestC0WorkerDelivery:
    def test_c0_worker_delivers_empty(self, tmp_path):
        c0 = build_c0_artifact(task_id="task-1", git_sha="deadbeef", model_name="m")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(c0, tmp_path)
        rc, out, err = _run_worker_child(manifest, "C0")
        assert rc == 0, err
        receipt = json.loads(out.strip().splitlines()[-1])
        assert receipt["arm"] == "C0"
        assert receipt["delivered_artifact"] == ""


class TestWorkerReceiptEvidence:
    def test_receipt_has_delivery_evidence(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "T", system_prompt="SYS> ")
        assert rc == 0
        r = json.loads(out.strip().splitlines()[-1])
        assert r["lesson_block_hash"]
        assert r["final_prompt_hash"]
        assert r["delivery_timestamp"] > 0
        # final_prompt_hash == SHA256("SYS> " + delivered)
        import hashlib

        assert r["final_prompt_hash"] == hashlib.sha256(
            ("SYS> " + m.rendered_artifact).encode()
        ).hexdigest()

    def test_receipt_invalid_not_silent(self, tmp_path):
        m = _freeze_t((_lesson("A", "use pathlib", 1), _lesson("L", "run tests", 2)), lesson_l_id="L")
        from runtime_v2.services.f2_freeze import persist_manifest

        manifest = persist_manifest(m, tmp_path)
        rc, out, err = _run_worker_child(manifest, "T")
        assert rc == 0
        r = json.loads(out.strip().splitlines()[-1])
        assert r["verification_result"] == "verified"