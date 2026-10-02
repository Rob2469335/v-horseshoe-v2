"""F2 Slice 4 — REAL fresh-P2 backend architecture-D integration tests.

Slice 3 proved replay propagation with a real `python -c` replay-probe
subprocess. THIS slice replaces that probe with the ACTUAL backend process:

    P1 (worker/test)  -- starts -->  REAL uvicorn backend P2
                                  (swarm_os.app.main + main.py lifespan
                                   installs verified replay INSIDE P2)
        |   F2 env: SWARM_F2_REPLAY=1, manifest, repo root, rollout, trajectory
        v
    P2 serves the existing task/API path  POST /agents/{agent_id}/step/stream
        |
        v
    P3/replay-gated delivery seam reached (agent_service_v2.py ~2695)
        |
        v
    fake/instrumented model boundary reached in P2 (evidence file)

Required proofs (authorization §14 / worker-execution authorization):
  - P1 exists, P2 is a genuinely separate backend process, P2 PID captured
  - P2 receives F2 environment and INDEPENDENTLY installs verified replay
  - F2_REQUIRED=True and REPLAY_ACTIVE=True established inside P2
  - environment clearing after install does not lose F2_REQUIRED
  - actual API/task execution occurs in P2 and reaches the real delivery seam
  - LIVE render_active_lessons() is NOT called
  - fake model boundary is reached in P2; NO real model/provider is ever called
  - invalid replay => P2 aborts; P3/delivery/fake-model never reached
  - C0 (empty artifact) remains valid replay-active delivery

NO real model, NO Experiment J, NO N=2, NO scientific evaluation.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from qwen_train.f2_arm_primitives import lesson_hash_of
from qwen_train.f2_execution_adapter import (
    F2ExecutionAdapter,
    FAKE_MODEL_EVIDENCE_FILE,
    start_real_p2_with_fake_model,
    wait_for_backend_health,
)
from runtime_v2.services.f2_freeze import (
    FreezeVerificationError,
    LessonEntry,
    freeze_artifact,
    persist_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
_F2_ENV_VARS = (
    "SWARM_F2_REPLAY",
    "SWARM_F2_MANIFEST_PATH",
    "SWARM_F2_REPO_ROOT",
    "SWARM_F2_ROLLOUT_ID",
    "SWARM_F2_TRAJECTORY_RUN_ID",
)

# Ports chosen far from the live backend (8000) to avoid collision.
P2_PORT = 8271


@pytest.fixture(scope="module", autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock so these tests
    can spawn a REAL backend process. (Same pattern as the other F2 tests.)"""
    yield


@pytest.fixture(autouse=True)
def _isolate_child_prompt_repairer_store(tmp_path):
    """Keep the REAL child backend's prompt-repairer state out of production `data/`.

    These tests spawn a genuine uvicorn P2 in a separate process. That child has
    no pytest fixtures, so `isolate_prompt_repairer_store` cannot protect the
    production persistent files for it - P2 was appending ~21 KB per test to the
    real `data/prompt_repairer_audit.jsonl`.

    `F2ExecutionAdapter` builds the child env with `os.environ.copy()`, so
    pointing `SWARM_PROMPT_REPAIRER_DATA_DIR` at a per-test temp directory
    redirects EVERY prompt-repairer persistent file the child touches
    (candidates, snapshots, audit log, journal). Production behaviour is
    unchanged when the variable is unset.
    """
    store = tmp_path / "child_prompt_repairer_store"
    store.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("SWARM_PROMPT_REPAIRER_DATA_DIR")
    os.environ["SWARM_PROMPT_REPAIRER_DATA_DIR"] = str(store)
    try:
        yield store
    finally:
        if previous is None:
            os.environ.pop("SWARM_PROMPT_REPAIRER_DATA_DIR", None)
        else:
            os.environ["SWARM_PROMPT_REPAIRER_DATA_DIR"] = previous


@pytest.fixture(autouse=True)
def _clear_f2_env():
    saved = {k: os.environ.pop(k, None) for k in _F2_ENV_VARS}
    yield
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def _make_manifest(tmp_path: Path, *, arm: str = "T", with_lesson: bool = True):
    lessons = (
        (LessonEntry("A", lesson_hash_of("use pathlib"), 1, "use pathlib"),)
        if with_lesson
        else ()
    )
    if arm == "C0":
        l_id, l_hash = None, None
    else:
        l_id, l_hash = "A", lesson_hash_of("use pathlib")
    art = freeze_artifact(
        rendered_artifact=("use pathlib" if (with_lesson and arm == "T") else ""),
        arm=arm,
        ordered_lessons=lessons,
        lesson_l_id=l_id,
        lesson_l_hash=l_hash,
        task_id="task-1",
        model_name="test-model",
        git_sha="deadbeef",
        freeze_timestamp=1_700_000_000.0,
    )
    return persist_manifest(art, tmp_path), art


def _set_f2_env(manifest: Path, rollout: str, traj: str):
    os.environ["SWARM_F2_REPLAY"] = "1"
    os.environ["SWARM_F2_MANIFEST_PATH"] = str(manifest)
    os.environ["SWARM_F2_REPO_ROOT"] = str(REPO_ROOT)
    os.environ["SWARM_F2_ROLLOUT_ID"] = rollout
    os.environ["SWARM_F2_TRAJECTORY_RUN_ID"] = traj


def _new_adapter(arm: str, manifest: Path, work_dir: Path, port: int = P2_PORT):
    return F2ExecutionAdapter(
        arm=arm,
        manifest_path=manifest,
        repo_root=REPO_ROOT,
        workspace_root=REPO_ROOT,
        port=port,
        backend_timeout=60,
    )


class TestRealFreshP2BackendPositive:
    def test_real_backend_installs_replay_and_reaches_delivery_seam(self, tmp_path):
        """Full positive Architecture-D proof with a REAL separate uvicorn P2."""
        manifest, art = _make_manifest(tmp_path, arm="T", with_lesson=True)
        rollout = "rollout-slice4"
        traj = "traj-slice4"
        _set_f2_env(manifest, rollout, traj)
        work_dir = tmp_path / "run1"
        work_dir.mkdir()

        adapter = _new_adapter("T", manifest, work_dir)
        p1_pid = os.getpid()
        ev = adapter.prove_real_p2_delivery(
            task_prompt="prove the frozen artifact arrives",
            task_id="task-1",
            rollout_id=rollout,
            trajectory_run_id=traj,
            agent_id="f2probe",
            port=P2_PORT,
            work_dir=work_dir,
            startup_timeout=90,
            http_timeout=90,
        )

        # --- P1 exists / P2 is separate + fresh backend (criteria 1-3) ---
        assert ev["p1_pid"] == p1_pid
        p2_pid = ev["p2_pid"]
        assert p2_pid is not None and p2_pid != p1_pid
        assert ev["p2_expected_pid"] == ev["p2_launcher_pid"]
        # The backend was started by THIS invocation (its parent is P1), not a
        # pre-existing process. On this machine the launcher may re-execute
        # itself once; both are fresh descendants of P1.
        assert ev["p2_launcher_parent_pid"] == p1_pid

        # --- P2 received F2 environment and became healthy (criteria 4, 9) ---
        assert ev["backend_healthy"] is True

        # --- actual HTTP/task path served by P2 (criteria 9) ---
        http = ev["http"]
        assert http.get("status") == 200, f"http failed: {http}"
        assert http.get("chunks"), "no streamed chunks from P2"

        # --- replay installed INSIDE P2 + F2_REQUIRED/REPLAY_ACTIVE (5-7) ---
        fe = ev["fake_model_evidence"]
        assert fe, f"fake-model evidence missing: {ev['process_identity']}"
        # The fake model ran in the actual process serving the port (P2).
        assert fe.get("pid") == p2_pid
        assert fe.get("replay_required") is True
        assert fe.get("replay_active") is True

        # --- env clearing inside P2 does not lose the requirement (criteria 8) ---
        assert fe.get("f2_replay_env_after_install", "") == ""
        assert fe.get("replay_required") is True

        # --- delivery seam requested the frozen artifact (criteria 10) ---
        assert fe.get("delivery_artifact") == art.rendered_artifact

        # --- LIVE renderer NOT used; fake model reached (criteria 11-12) ---
        assert fe.get("model_called") is True
        assert fe.get("model")  # resolved model name recorded (never contacted)
        assert not fe.get("render_active_lessons_called"), (
            "LIVE render_active_lessons() was called during an F2 replay-active run"
        )

        # --- identity recorded (criteria 16) ---
        assert ev["manifest_content_address"]
        assert ev["rollout_id"] == rollout
        assert ev["trajectory_run_id"] == traj
        assert fe.get("rollout_id") == rollout
        assert fe.get("trajectory_run_id") == traj

        # backend must be terminated by the adapter cleanup
        assert not _any_live_p2(P2_PORT)

    def test_c0_empty_artifact_remains_valid_replay(self, tmp_path):
        """C0 (empty artifact) is valid replay-active delivery; NOT LIVE."""
        manifest, art = _make_manifest(tmp_path, arm="C0", with_lesson=False)
        assert art.rendered_artifact == ""
        rollout = "rollout-c0"
        traj = "traj-c0"
        _set_f2_env(manifest, rollout, traj)
        work_dir = tmp_path / "run_c0"
        work_dir.mkdir()

        adapter = _new_adapter("C0", manifest, work_dir)
        ev = adapter.prove_real_p2_delivery(
            task_prompt="c0 task",
            task_id="task-1",
            rollout_id=rollout,
            trajectory_run_id=traj,
            agent_id="f2probe",
            port=P2_PORT,
            work_dir=work_dir,
            startup_timeout=90,
            http_timeout=90,
        )

        assert ev["backend_healthy"] is True
        assert ev["http"].get("status") == 200
        fe = ev["fake_model_evidence"]
        assert fe, "evidence missing"
        assert fe.get("replay_required") is True
        assert fe.get("replay_active") is True
        # C0 empty artifact is delivered as the frozen artifact; never LIVE.
        assert fe.get("delivery_artifact") == ""
        assert fe.get("model_called") is True
        assert not fe.get("render_active_lessons_called")
        assert fe.get("f2_replay_env_after_install") == ""


class TestRealBackendFailClosed:
    def test_invalid_manifest_aborts_backend_no_delivery(self, tmp_path):
        """Invalid replay authorization => the REAL P2 aborts at startup. P3 is
        never reached, the delivery seam is never reached, the LIVE renderer is
        never used, and the fake model is never called."""
        bad = tmp_path / "bad_manifest.json"
        bad.write_text("{not json", encoding="utf-8")
        work_dir = tmp_path / "neg"
        work_dir.mkdir()
        evidence_path = work_dir / FAKE_MODEL_EVIDENCE_FILE
        trajectory = "traj-neg"

        record = start_real_p2_with_fake_model(
            attempt_id="f2arm_T_neg",
            repo_root=REPO_ROOT,
            workspace_root=REPO_ROOT,
            port=P2_PORT,
            evidence_path=evidence_path,
            work_dir=work_dir,
            manifest_path=bad,
            rollout_id="rollout-neg",
            trajectory_run_id=trajectory,
            startup_timeout=60,
        )
        try:
            # The lifespan fails closed (FreezeVerificationError -> raise), so
            # uvicorn startup fails and the process exits without serving.
            deadline = time.monotonic() + 60
            ret = None
            while time.monotonic() < deadline:
                ret = record["proc"].poll()
                if ret is not None:
                    break
                time.sleep(1.0)

            assert ret is not None and ret != 0, (
                "P2 with invalid manifest must exit nonzero (fail closed), "
                f"got returncode={ret}. Backend stderr tail:\n"
                f"{_tail(record['stderr_path'])}"
            )
            assert not wait_for_backend_health(P2_PORT, timeout=5), (
                "invalid-manifest backend must not serve the task/API path"
            )
            assert not evidence_path.exists(), (
                "fake model must never be reached when replay install fails"
            )
        finally:
            from qwen_train.f2_execution_adapter import terminate_backend

            terminate_backend(record)

    def test_missing_replay_flag_rejected_by_adapter_before_backend(self, tmp_path):
        """No SWARM_F2_REPLAY => the adapter refuses to establish the boundary;
        no real backend is started (fail-closed at P1)."""
        manifest, art = _make_manifest(tmp_path, arm="T")
        work_dir = tmp_path / "noenv"
        adapter = _new_adapter("T", manifest, work_dir)

        with pytest.raises(FreezeVerificationError):
            adapter.prove_real_p2_delivery(
                task_prompt="p",
                task_id="task-1",
                rollout_id="r",
                trajectory_run_id="t",
                agent_id="f2probe",
                port=P2_PORT,
                work_dir=work_dir,
            )


def _tail(path: str, n: int = 20) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:  # noqa: BLE001
        return "<unreadable stderr>"


def _any_live_p2(port: int) -> bool:
    """True if something is still listening on the port."""
    import urllib.error

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2.0):
            return True
    except urllib.error.URLError:
        return False
    except Exception:  # noqa: BLE001
        return False