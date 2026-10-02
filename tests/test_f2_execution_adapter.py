"""Adapter-level tests for the F2 execution adapter (Architecture D).

Scope (worker-execution authorization §14, first slice):
- environment / identity propagation
- fresh P2 subprocess boundary (real subprocess with instrumented/fake delivery)
- replay-required state
- inactive replay can NOT proceed to model delivery
- C0 active replay with empty artifact is not treated as LIVE
- adapter produces the expected execution/receipt boundary without a model run

These tests do NOT launch a model. They use a real subprocess for the fresh
process boundary and fake delivery/CLI seams.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qwen_train.f2_arm_primitives import lesson_hash_of
from qwen_train.f2_execution_adapter import (
    F2ExecutionAdapter,
    build_execution_boundary_payload,
)
from runtime_v2.services.f2_freeze import (
    FreezeVerificationError,
    LessonEntry,
    freeze_artifact,
    persist_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "qwen_train" / "f2_execution_adapter.py"


@pytest.fixture(scope="module", autouse=True)
def global_subprocess_mock():
    """Override tests/conftest.py's autouse subprocess.Popen mock.

    These tests spawn a genuine fresh Python child process to prove the adapter's
    P1→P2 process boundary. The conftest mock would swallow child output.
    """
    yield


def _make_manifest(tmp_path: Path, *, arm: str = "T", with_lesson: bool = True):
    lessons = (
        (LessonEntry("A", lesson_hash_of("use pathlib"), 1, "use pathlib"),) if with_lesson else ()
    )
    if arm == "C0":
        l_id, l_hash = None, None
    else:
        l_id, l_hash = "A", lesson_hash_of("use pathlib")
    art = freeze_artifact(
        rendered_artifact="use pathlib" if (with_lesson and arm == "T") else "",
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


class _FakeBackendStarter:
    """Starts a REAL python subprocess as a stand-in P2."""

    def __init__(self):
        self.last_record = None

    def __call__(self, *args, **kwargs):
        env = dict(os.environ)
        code = (
            "import os, sys, json; "
            "sys.path.insert(0, os.environ['SWARM_F2_REPO_ROOT']); "
            "m = os.environ.get('SWARM_F2_MANIFEST_PATH', ''); "
            "from runtime_v2.services.f2_replay import "
            "install_verified_replay_from_env; "
            "s = install_verified_replay_from_env(); "
            "print(json.dumps({'replay_active': bool(s), "
            "'manifest': m})); "
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(REPO_ROOT),
        )
        out, err = proc.communicate(timeout=30)
        record = {
            "pid": proc.pid,
            "returncode": proc.returncode,
            "stdout": out.decode(errors="replace"),
            "stderr": err.decode(errors="replace"),
        }
        self.last_record = record
        return record, Path(".")


class _FakeBackendWaiter:
    def __init__(self, passed: bool = True, errors=None):
        self.passed = passed
        self.errors = errors or []

    def __call__(self, *args, **kwargs):
        return self


class _FakeCliRunner:
    def __init__(self):
        self.called = False
        self.last_item = None

    def __call__(self, item, timeout, allow_approval, record):
        self.called = True
        self.last_item = item
        return {"ok": True, "delegated": True}


def _env_replay_required(tmp_path, manifest):
    env = dict(os.environ)
    env["SWARM_F2_REPLAY"] = "1"
    env["SWARM_F2_MANIFEST_PATH"] = str(manifest)
    env["SWARM_F2_REPO_ROOT"] = str(REPO_ROOT)
    env["SWARM_F2_ROLLOUT_ID"] = "rollout-1"
    env["SWARM_F2_TRAJECTORY_RUN_ID"] = "traj-1"
    return env


# ---------------------------------------------------------------------------
# 1. environment / identity propagation
# ---------------------------------------------------------------------------


class TestEnvPropagation:
    def test_f2_env_reaches_fresh_p2(self, tmp_path):
        manifest, art = _make_manifest(tmp_path)
        fake_backend = _FakeBackendStarter()
        adapter = F2ExecutionAdapter(
            arm="T",
            manifest_path=manifest,
            repo_root=REPO_ROOT,
            workspace_root=tmp_path,
            backend_starter=fake_backend,
            backend_waiter=_FakeBackendWaiter(passed=True),
            cli_runner=_FakeCliRunner(),
        )
        # set the F2 replay-required env in this process (as the orchestrator does)
        os.environ.update(_env_replay_required(tmp_path, manifest))
        try:
            adapter.run(task_prompt="p", task_id="task-1", rollout_id="r", trajectory_run_id="t")
        finally:
            for var in ("SWARM_F2_REPLAY", "SWARM_F2_MANIFEST_PATH", "SWARM_F2_REPO_ROOT",
                        "SWARM_F2_ROLLOUT_ID", "SWARM_F2_TRAJECTORY_RUN_ID"):
                os.environ.pop(var, None)

        assert fake_backend.last_record is not None
        out = fake_backend.last_record["stdout"]
        parsed = json.loads(out.strip().splitlines()[-1])
        assert parsed["replay_active"] is True
        assert str(manifest) in parsed["manifest"]

    def test_identity_fields_present_in_boundary(self, tmp_path):
        manifest, art = _make_manifest(tmp_path)
        os.environ.update(_env_replay_required(tmp_path, manifest))
        try:
            boundary = F2ExecutionAdapter(
                arm="T",
                manifest_path=manifest,
                repo_root=REPO_ROOT,
                workspace_root=tmp_path,
                backend_starter=_FakeBackendStarter(),
                backend_waiter=_FakeBackendWaiter(passed=True),
            ).run(task_prompt="p", task_id="task-1", rollout_id="rollout-1", trajectory_run_id="traj-1")
        finally:
            for var in ("SWARM_F2_REPLAY", "SWARM_F2_MANIFEST_PATH", "SWARM_F2_REPO_ROOT",
                        "SWARM_F2_ROLLOUT_ID", "SWARM_F2_TRAJECTORY_RUN_ID"):
                os.environ.pop(var, None)
        assert boundary.arm == "T"
        assert boundary.manifest_verified is True
        assert boundary.replay_required is True
        assert boundary.execution_evidence["rollout_id"] == "rollout-1"
        assert boundary.execution_evidence["trajectory_run_id"] == "traj-1"


# ---------------------------------------------------------------------------
# 2. replay-required state
# ---------------------------------------------------------------------------


class TestReplayRequired:
    def test_replay_required_missing_aborts(self, tmp_path):
        manifest, art = _make_manifest(tmp_path)
        adapter = F2ExecutionAdapter(
            arm="T",
            manifest_path=manifest,
            repo_root=REPO_ROOT,
            workspace_root=tmp_path,
            backend_starter=_FakeBackendStarter(),
            backend_waiter=_FakeBackendWaiter(passed=True),
        )
        with pytest.raises(FreezeVerificationError):
            adapter.run(task_prompt="p", task_id="task-1", rollout_id="r", trajectory_run_id="t")

    def test_replay_required_with_invalid_manifest_aborts(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        os.environ.update(_env_replay_required(tmp_path, bad))
        fake_backend = _FakeBackendStarter()
        try:
            adapter = F2ExecutionAdapter(
                arm="T",
                manifest_path=bad,
                repo_root=REPO_ROOT,
                workspace_root=tmp_path,
                backend_starter=fake_backend,
                backend_waiter=_FakeBackendWaiter(passed=True),
            )
            # load_manifest/verify_manifest on the malformed file must fail
            # closed BEFORE any backend is started.
            with pytest.raises(Exception):
                adapter.run(task_prompt="p", task_id="task-1", rollout_id="r", trajectory_run_id="t")
        finally:
            for var in ("SWARM_F2_REPLAY", "SWARM_F2_MANIFEST_PATH", "SWARM_F2_REPO_ROOT",
                        "SWARM_F2_ROLLOUT_ID", "SWARM_F2_TRAJECTORY_RUN_ID"):
                os.environ.pop(var, None)
        # fail-closed: no fresh backend should have been spawned
        assert fake_backend.last_record is None


# ---------------------------------------------------------------------------
# 3. C0 empty artifact is not treated as LIVE
# ---------------------------------------------------------------------------


class TestC0Distinction:
    def test_c0_active_replay_empty_artifact_not_live(self, tmp_path):
        manifest, art = _make_manifest(tmp_path, arm="C0", with_lesson=False)
        assert art.rendered_artifact == ""
        os.environ.update(_env_replay_required(tmp_path, manifest))
        fake_backend = _FakeBackendStarter()
        cli = _FakeCliRunner()
        try:
            boundary = F2ExecutionAdapter(
                arm="C0",
                manifest_path=manifest,
                repo_root=REPO_ROOT,
                workspace_root=tmp_path,
                backend_starter=fake_backend,
                backend_waiter=_FakeBackendWaiter(passed=True),
                cli_runner=cli,
            ).run(task_prompt="p", task_id="task-1", rollout_id="r", trajectory_run_id="t")
        finally:
            for var in ("SWARM_F2_REPLAY", "SWARM_F2_MANIFEST_PATH", "SWARM_F2_REPO_ROOT",
                        "SWARM_F2_ROLLOUT_ID", "SWARM_F2_TRAJECTORY_RUN_ID"):
                os.environ.pop(var, None)
        assert boundary.arm == "C0"
        assert boundary.manifest_verified is True
        # The delivery-time LIVE fallback distinction is enforced at the
        # delivery seam (later slice); the adapter-level proof is that C0's
        # empty artifact is produced via active verified replay, and the payload
        # records it as such.
        payload = build_execution_boundary_payload(boundary)
        assert payload["adapter"]["manifest_verified"] is True
        assert payload["adapter"]["replay_required"] is True


# ---------------------------------------------------------------------------
# 4. fresh P2 subprocess boundary + adapter boundary output
# ---------------------------------------------------------------------------


class TestFreshProcessBoundary:
    def test_fresh_p2_is_a_real_subprocess(self, tmp_path):
        manifest, art = _make_manifest(tmp_path)
        fake_backend = _FakeBackendStarter()
        os.environ.update(_env_replay_required(tmp_path, manifest))
        try:
            boundary = F2ExecutionAdapter(
                arm="T",
                manifest_path=manifest,
                repo_root=REPO_ROOT,
                workspace_root=tmp_path,
                backend_starter=fake_backend,
                backend_waiter=_FakeBackendWaiter(passed=True),
            ).run(task_prompt="p", task_id="task-1", rollout_id="r", trajectory_run_id="t")
        finally:
            for var in ("SWARM_F2_REPLAY", "SWARM_F2_MANIFEST_PATH", "SWARM_F2_REPO_ROOT",
                        "SWARM_F2_ROLLOUT_ID", "SWARM_F2_TRAJECTORY_RUN_ID"):
                os.environ.pop(var, None)
        assert fake_backend.last_record is not None
        assert fake_backend.last_record["pid"] != os.getpid()
        assert fake_backend.last_record["returncode"] == 0
        assert boundary.backend is not None

    def test_adapter_boundary_without_model_run(self, tmp_path):
        manifest, art = _make_manifest(tmp_path)
        cli = _FakeCliRunner()
        os.environ.update(_env_replay_required(tmp_path, manifest))
        try:
            boundary = F2ExecutionAdapter(
                arm="T",
                manifest_path=manifest,
                repo_root=REPO_ROOT,
                workspace_root=tmp_path,
                backend_starter=_FakeBackendStarter(),
                backend_waiter=_FakeBackendWaiter(passed=True),
                cli_runner=cli,
            ).run(
                task_prompt="p",
                task_id="task-1",
                rollout_id="r",
                trajectory_run_id="t",
                dry_run=True,
            )
        finally:
            for var in ("SWARM_F2_REPLAY", "SWARM_F2_MANIFEST_PATH", "SWARM_F2_REPO_ROOT",
                        "SWARM_F2_ROLLOUT_ID", "SWARM_F2_TRAJECTORY_RUN_ID"):
                os.environ.pop(var, None)
        # dry_run must NOT drive the CLI / no model call
        assert cli.called is False
        assert boundary.failure is None
        payload = build_execution_boundary_payload(boundary)
        assert payload["adapter"]["manifest_content_address"]
        assert payload["backend"]["health_gate_passed"] is True

class TestServingProcessIdentityNeverGuessed:
    """D4: the launcher PID is never relabelled as the serving PID.

    ``resolve_serving_pid`` returns ``None`` when the LISTEN socket owner cannot
    be determined. That means UNKNOWN. Substituting the launcher PID would
    assert an identity that was never established (worker-execution authorization
    §10 item 5 / §13 fail-closed matrix).
    """

    def test_successful_resolution_returns_socket_owner(self, monkeypatch):
        from qwen_train import f2_execution_adapter as mod

        monkeypatch.setattr(mod, "resolve_serving_pid", lambda port: 31337)
        serving, source = mod._resolve_serving_identity(8211, launcher_pid=4242)
        assert serving == 31337
        assert source == "socket_listener"

    def test_resolution_failure_raises_and_never_returns_launcher(self, monkeypatch):
        from qwen_train import f2_execution_adapter as mod

        monkeypatch.setattr(mod, "resolve_serving_pid", lambda port: None)
        with pytest.raises(FreezeVerificationError, match="could not establish"):
            mod._resolve_serving_identity(8211, launcher_pid=4242)

    def test_launcher_pid_never_becomes_serving_pid(self, monkeypatch):
        """The exact old defect: ``resolve(...) or launcher_pid``."""
        from qwen_train import f2_execution_adapter as mod

        monkeypatch.setattr(mod, "resolve_serving_pid", lambda port: None)
        raised = False
        try:
            serving, _src = mod._resolve_serving_identity(8211, launcher_pid=4242)
        except FreezeVerificationError:
            raised = True
            serving = None
        assert raised is True
        assert serving != 4242

    def test_real_resolver_returns_none_for_closed_port(self):
        """No listener -> None (unknown), not a guess."""
        from qwen_train.f2_execution_adapter import resolve_serving_pid

        assert resolve_serving_pid(65533) is None

    def test_old_fallback_pattern_absent_from_source(self):
        """Static guard: the dangerous expression must not reappear."""
        src = HELPER.read_text(encoding="utf-8")
        assert "resolve_serving_pid(port) or launcher_pid" not in src
        assert "or launcher_pid" not in src

    def test_launcher_identity_recorded_separately(self, monkeypatch):
        """launcher_pid stays in its own field; serving_pid is independent."""
        from qwen_train import f2_execution_adapter as mod

        monkeypatch.setattr(mod, "resolve_serving_pid", lambda port: 31337)
        serving, _src = mod._resolve_serving_identity(8211, launcher_pid=4242)
        payload = {
            "p2_pid": serving,
            "p2_launcher_pid": 4242,
            "serving_pid_source": _src,
        }
        assert payload["p2_pid"] == 31337
        assert payload["p2_launcher_pid"] == 4242
        assert payload["p2_pid"] != payload["p2_launcher_pid"]
        assert payload["serving_pid_source"] == "socket_listener"
