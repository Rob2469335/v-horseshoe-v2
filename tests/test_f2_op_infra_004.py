"""F2-OP-INFRA-004 regression tests: D2, D3, W2, W4 and production wiring.

Engineering-only. No model execution, no F2 scientific observation, no lesson or
candidate mutation. These tests exercise the authorized implementation directly
and statically verify the dangerous old patterns are gone.

Identity (F0 §7) is the isolation concern: every test that touches the F2
environment uses the ``f2_env`` fixture so ``SWARM_F2_TRAJ_DIR`` cannot leak into
unrelated tests (that leak was a real regression during implementation).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from qwen_train import f2_arm_orchestrator as ORCH  # noqa: E402
from qwen_train import f2_execution_adapter as ADAPTER  # noqa: E402
from qwen_train import f2_arm_worker as WORKER  # noqa: E402
from runtime_v2.services.f2_freeze import FreezeVerificationError  # noqa: E402

POOL_INSTANCE = "pypa__twine-1066"


@pytest.fixture(autouse=True)
def real_subprocess():
    """Restore the genuine ``subprocess.Popen`` for this module.

    ``tests/conftest.py::global_subprocess_mock`` is autouse and patches
    ``subprocess.Popen`` for every test under ``tests/``. That mock makes the
    task-environment probe in ``_ensure_interpreter`` see an empty result, so the
    authorized W4 binding could not be exercised at all. This is the same
    documented opt-out that ``tests/test_f2_execution_adapter.py`` already uses.
    """
    import subprocess as _sp
    from tests.conftest import _REAL_POPEN
    saved = _sp.Popen
    _sp.Popen = _REAL_POPEN
    yield
    _sp.Popen = saved


@pytest.fixture
def f2_env(monkeypatch):
    """Isolate every F2 env var so nothing leaks between tests."""
    for var in (
        "SWARM_F2_TRAJ_DIR", "SWARM_F2_REPLAY", "SWARM_F2_MANIFEST_PATH",
        "SWARM_F2_REPO_ROOT", "SWARM_F2_ROLLOUT_ID", "SWARM_F2_TRAJECTORY_RUN_ID",
        "SWARM_WORKSPACE_ROOT", "F2_ARM_EXEC_CMD",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# --------------------------------------------------------------------------- D2

class TestD2EvidenceDirectoryConvergence:
    """D2: the P2 writer and the F2 worker reader must resolve to ONE directory."""

    def test_env_var_name_agrees_between_adapter_and_worker(self):
        assert ADAPTER.F2_TRAJ_DIR_ENV == WORKER.F2_TRAJ_DIR_ENV == "SWARM_F2_TRAJ_DIR"

    def test_adapter_resolver_is_absolute_and_inside_workspace(self):
        ws = Path(tempfile.mkdtemp(prefix="d2ws_"))
        resolved = ADAPTER.resolve_f2_traj_dir(ws)
        assert resolved.is_absolute()
        assert str(resolved).startswith(str(ws.resolve()))

    def test_adapter_resolver_is_deterministic(self):
        ws = Path(tempfile.mkdtemp(prefix="d2ws_"))
        assert ADAPTER.resolve_f2_traj_dir(ws) == ADAPTER.resolve_f2_traj_dir(ws)

    def test_writer_and_reader_converge(self, f2_env):
        ws = Path(tempfile.mkdtemp(prefix="d2ws_"))
        resolved = ADAPTER.resolve_f2_traj_dir(ws)
        f2_env.setenv("SWARM_F2_TRAJ_DIR", str(resolved))
        assert WORKER._resolve_traj_dir(ws) == resolved

    def test_reader_rejects_relative_override(self, f2_env):
        ws = Path(tempfile.mkdtemp(prefix="d2ws_"))
        f2_env.setenv("SWARM_F2_TRAJ_DIR", "relative/data/trajectories")
        with pytest.raises(FreezeVerificationError, match="must be an absolute path"):
            WORKER._resolve_traj_dir(ws)

    def test_non_f2_default_preserved_when_unset(self, f2_env):
        """Non-F2 behaviour must be byte-identical to today's derivation."""
        ws = Path(tempfile.mkdtemp(prefix="d2ws_"))
        assert WORKER._resolve_traj_dir(ws) == ws / "data" / "trajectories"

    def test_agent_service_tra_dir_reads_the_f2_override(self, f2_env):
        """The P2 writer honours the explicit path (real import in a fresh process)."""
        assert self._writer_traj_dir("/tmp/f2_absolute_dir") == Path("/tmp/f2_absolute_dir")

    def test_agent_service_tra_dir_default_unchanged(self, f2_env):
        """With no override the P2 writer keeps the historical relative default."""
        assert self._writer_traj_dir(None) == Path("data/trajectories")

    @staticmethod
    def _writer_traj_dir(override):
        """Import the real writer in a fresh process with/without the override."""
        env = dict(os.environ)
        env.pop("SWARM_F2_TRAJ_DIR", None)
        if override is not None:
            env["SWARM_F2_TRAJ_DIR"] = override
        code = (
            "import sys;sys.path.insert(0, r'%s');"
            "from runtime_v2.api.agent_service_v2 import AgentServiceV2;"
            "print(AgentServiceV2._TRAJ_DIR)" % REPO_ROOT
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, timeout=180)
        assert out.returncode == 0, out.stderr[-500:]
        return Path(out.stdout.strip())

    def test_env_var_is_propagated_into_p2(self):
        assert "SWARM_F2_TRAJ_DIR" in ADAPTER.F2_ENV_VARS

    def test_no_cwd_dependence_remains(self):
        """The reader must never fall back to a cwd-relative join."""
        src = (REPO_ROOT / "qwen_train" / "f2_arm_worker.py").read_text("utf-8")
        assert "workspace_root / \"data\" / \"trajectories\"" not in src


def importlib_reload_trajectory_dir(module):
    """Retained for callers that need to re-evaluate ``_TRAJ_DIR`` in-process."""
    import os
    override = os.environ.get("SWARM_F2_TRAJ_DIR") or "data/trajectories"
    module.AgentServiceV2._TRAJ_DIR = Path(override)


# --------------------------------------------------------------------------- D3

class TestD3EvidenceWriteOutcome:
    """D3: a failed evidence write must be distinguishable from absent evidence."""

    @staticmethod
    def _outcome(**kw):
        base = {
            "type": "f2_evidence_write_outcome", "run_id": "r", "rollout_id": "ro",
            "outcome": "written", "error_type": "", "error": "",
        }
        base.update(kw)
        return base

    def test_outcome_constants_exist(self):
        from runtime_v2.api import agent_service_v2 as AS
        assert AS._F2_EVIDENCE_WRITE_OK == "written"
        assert AS._F2_EVIDENCE_WRITE_FAILED == "write_failed"

    def test_silent_swallow_removed_from_write_seam(self):
        """The old `except Exception: pass` must not wrap evidence persistence."""
        src = (REPO_ROOT / "runtime_v2" / "api" / "agent_service_v2.py").read_text("utf-8")
        i = src.find('"record_type": "delivery_evidence"')
        assert i > 0, "delivery-evidence write not found"
        window = src[i - 700: i + 3200]
        # The failure reason is captured, not swallowed.
        assert "except Exception as _e:" in window, "failure reason not captured"
        assert "_F2_EVIDENCE_WRITE_FAILED" in window, "no explicit failure state"
        assert "_F2_EVIDENCE_WRITE_OK" in window, "no explicit success state"
        # The outcome is emitted so P1 can classify it even if the file write failed.
        assert "f2_evidence_write_outcome" in window
        assert "error_type" in window and "error" in window
        # The old silent swallow must be gone.
        assert "except Exception:  # noqa: BLE001\n                    pass" not in window

    def test_outcomes_parsed_from_p2_stdout(self):
        td = Path(tempfile.mkdtemp())
        p = td / "p2.out"
        p.write_text(
            "noise\n"
            + json.dumps(self._outcome()) + "\n"
            + json.dumps(self._outcome(outcome="write_failed",
                                       error_type="PermissionError",
                                       error="denied")) + "\n",
            encoding="utf-8",
        )
        outs = ADAPTER.read_evidence_write_outcomes(p)
        assert [o["outcome"] for o in outs] == ["written", "write_failed"]

    def test_failure_preserves_reason(self):
        td = Path(tempfile.mkdtemp())
        p = td / "p2.out"
        p.write_text(json.dumps(self._outcome(outcome="write_failed",
                                              error_type="OSError",
                                              error="disk full")) + "\n",
                     encoding="utf-8")
        first = WORKER._read_evidence_write_outcome(ADAPTER.read_evidence_write_outcomes(p))
        assert first["error_type"] == "OSError"
        assert first["error"] == "disk full"

    def test_write_failure_detected(self):
        outs = [self._outcome(outcome="write_failed")]
        assert WORKER._read_evidence_write_outcome(outs) is not None

    def test_absent_evidence_not_mistaken_for_write_failure(self):
        outs = [self._outcome(outcome="written")]
        assert WORKER._read_evidence_write_outcome(outs) is None

    def test_no_outcomes_yields_none(self):
        assert WORKER._read_evidence_write_outcome([]) is None
        assert WORKER._read_evidence_write_outcome(None) is None

    def test_missing_stdout_file_is_tolerated(self):
        assert ADAPTER.read_evidence_write_outcomes(None) == []
        assert ADAPTER.read_evidence_write_outcomes("/nonexistent/p2.out") == []

    def test_worker_distinguishes_both_failure_kinds(self):
        src = (REPO_ROOT / "qwen_train" / "f2_arm_worker.py").read_text("utf-8")
        assert "EVIDENCE_WRITE_FAILED" in src
        assert "EVIDENCE_NOT_FOUND" in src

    def test_stream_is_not_killed_by_write_failure(self):
        """The preserved intent: an evidence-write failure never kills the stream."""
        src = (REPO_ROOT / "runtime_v2" / "api" / "agent_service_v2.py").read_text("utf-8")
        i = src.find('"record_type": "delivery_evidence"')
        window = src[i: i + 3200]
        assert "raise" not in window.split("except Exception as _e:")[0][-200:]


# --------------------------------------------------------------------------- W2

class TestW2ProductionModelPath:
    """W2: the production path must NOT replace the real model call."""

    LAUNCH_KW = {
        "repo_root": str(REPO_ROOT), "workspace_root": r"C:\tmp\ws",
        "port": 8211, "evidence_path": r"C:\tmp\ev.json",
    }

    def test_production_launcher_does_not_patch_the_model(self):
        code = ADAPTER.build_real_p2_launcher(**self.LAUNCH_KW, fake_model=False)
        assert "complete_for_tool_decision = _fake_complete" not in code

    def test_production_launcher_does_not_override_mcp(self):
        code = ADAPTER.build_real_p2_launcher(**self.LAUNCH_KW, fake_model=False)
        assert "get_mcp_manager = _no_mcp_manager" not in code

    def test_production_launcher_still_serves_the_real_app(self):
        code = ADAPTER.build_real_p2_launcher(**self.LAUNCH_KW, fake_model=False)
        assert "uvicorn.run(app" in code
        assert 'host="127.0.0.1"' in code

    def test_production_launcher_keeps_live_render_spy(self):
        """Proving LIVE was never reached is a required fail-closed property."""
        code = ADAPTER.build_real_p2_launcher(**self.LAUNCH_KW, fake_model=False)
        assert "render_active_lessons_called" in code

    def test_fake_launcher_still_patches_for_existing_tests(self):
        code = ADAPTER.build_real_p2_launcher(**self.LAUNCH_KW, fake_model=True)
        assert "complete_for_tool_decision = _fake_complete" in code

    def test_production_entry_points_exist(self):
        assert callable(ADAPTER.start_real_p2_production_model)
        assert hasattr(ADAPTER.F2ExecutionAdapter, "execute_arm_real")

    def test_execute_arm_real_uses_the_production_entry_point(self):
        src = (REPO_ROOT / "qwen_train" / "f2_execution_adapter.py").read_text("utf-8")
        i = src.find("def execute_arm_real")
        assert i > 0
        body = src[i: i + 4000]
        assert "start_real_p2_production_model" in body
        assert "start_real_p2_with_fake_model" not in body

    def test_execute_arm_real_keeps_identity_fail_closed(self):
        src = (REPO_ROOT / "qwen_train" / "f2_execution_adapter.py").read_text("utf-8")
        i = src.find("def execute_arm_real")
        body = src[i: i + 4000]
        assert "_resolve_serving_identity" in body
        assert " or launcher_pid" not in body

    def test_execute_arm_real_keeps_readiness_gate(self):
        src = (REPO_ROOT / "qwen_train" / "f2_execution_adapter.py").read_text("utf-8")
        i = src.find("def execute_arm_real")
        assert "wait_for_backend_health" in src[i: i + 4000]

    def test_execute_arm_real_uses_existing_loopback_task_surface(self):
        src = (REPO_ROOT / "qwen_train" / "f2_execution_adapter.py").read_text("utf-8")
        i = src.find("def execute_arm_real")
        assert "post_task_stream" in src[i: i + 4000]


# --------------------------------------------------------------------------- W4

class TestW4TaskWorkspaceBinding:
    """W4: bind to the EXISTING task machinery, never a second task system."""

    @staticmethod
    def _bind(ws=None, **kw):
        return ADAPTER.bind_task_environment(
            instance_id=kw.pop("instance_id", POOL_INSTANCE),
            task_id=kw.pop("task_id", "ENGINEERING-REHEARSAL"),
            workspace_root=ws or Path(tempfile.mkdtemp(prefix="w4ws_")),
            repo_root=str(REPO_ROOT),
            **kw,
        )

    def test_identity_comes_from_the_curriculum_pool(self):
        b = self._bind()
        assert b["instance_id"] == POOL_INSTANCE
        assert b["repo"] == "pypa/twine"
        assert len(b["base_commit"]) == 40

    def test_declared_interpreter_is_not_the_project_interpreter(self):
        b = self._bind()
        assert str(REPO_ROOT / ".venv") not in b["interpreter_str"]
        assert b["task_python"]

    def test_install_and_test_provenance_present(self):
        b = self._bind()
        assert b["install_steps"], "install requirements must be declared"
        assert b["test_cmd"].startswith("pytest")
        assert b["test_argv"][0] == b["task_python"]

    def test_f1_authorized_harness_base_commit_recorded(self):
        assert len(self._bind()["harness_base_commit"]) == 40

    def test_main_repository_can_never_be_the_workspace(self):
        with pytest.raises(FreezeVerificationError, match="inside the main repository"):
            ADAPTER.bind_task_environment(
                instance_id=POOL_INSTANCE, task_id="X",
                workspace_root=REPO_ROOT / "qwen_train", repo_root=str(REPO_ROOT),
            )

    def test_unknown_instance_fails_closed(self):
        with pytest.raises(FreezeVerificationError, match="no curriculum pool row"):
            ADAPTER.bind_task_environment(
                instance_id="does__not-exist-9999", task_id="X",
                workspace_root=Path(tempfile.mkdtemp()), repo_root=str(REPO_ROOT),
            )

    def test_evaluator_separation_is_reported(self):
        b = self._bind()
        assert b["evaluator_separation_ok"] is True

    def test_pool_loader_is_read_only(self):
        row = ADAPTER.load_pool_row(POOL_INSTANCE)
        assert row["instance_id"] == POOL_INSTANCE
        pool = REPO_ROOT / "qwen_train" / "curriculum" / "swe_pool.jsonl"
        assert "instance_id" in pool.read_text("utf-8")

    def test_no_second_task_environment_was_invented(self):
        """W4 reuse, not reinvention: the real machinery is what we call."""
        src = (REPO_ROOT / "qwen_train" / "f2_execution_adapter.py").read_text("utf-8")
        i = src.find("def bind_task_environment")
        body = src[i: i + 5000]
        for helper in ("_pool_env_meta", "resolve_task_python", "task_exec_plan",
                       "task_test_argv", "_task_python_path",
                       "_preflight_evaluator_separation",
                       "F1_AUTHORIZED_BASE_COMMIT"):
            assert helper in body, f"existing helper not reused: {helper}"


# ------------------------------------------------------------- production wiring

class TestProductionWiring:
    """The dead F2_ARM_EXEC_CMD seam must be gone from the production path."""

    @staticmethod
    def _captured_argv(**kw):
        captured = {}

        class _Popen:
            def __init__(self, cmd, **kw2):
                captured["cmd"] = list(cmd)
                raise SystemExit("captured")

        real = subprocess.Popen
        ORCH.subprocess.Popen = _Popen
        # R12: an EXECUTING arm must be told, explicitly and fail-closed, which
        # filesystem is the isolated task workspace (qwen_train/arm_workspace.py:
        # :288-295). Supply a throwaway absolute directory so the guard passes on
        # its own terms; nothing executes, because Popen above is stubbed. The
        # orchestrator writes the value into os.environ at :202, so it is restored
        # afterwards and cannot leak into another test.
        prior_workspace_root = os.environ.get("SWARM_WORKSPACE_ROOT")
        throwaway_workspace = Path(tempfile.mkdtemp()).resolve()
        # The F2 pre-flight readiness gate requires a declaration + evidence;
        # supply TEST values (not operator F2 values) so the arm reaches Popen.
        from runtime_v2.services.task_readiness import (
            READINESS_CONDITIONS,
            ReadinessEvidence,
            TaskReadiness,
        )

        task_id = kw.pop("task_id", "ENGINEERING-REHEARSAL")
        kw.setdefault(
            "readiness",
            TaskReadiness(
                task_id=task_id,
                base_commit="engineering-rehearsal",
                relevant_file_set=("src/engineering_rehearsal.py",),
            ),
        )
        kw.setdefault(
            "readiness_evidence",
            ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS}),
        )
        try:
            ORCH.run_f2_arm(
                arm="C0",
                manifest_dir=Path(tempfile.mkdtemp()),
                worker_script=Path("qwen_train/f2_arm_worker.py"),
                repo_root=Path("."),
                render_t=lambda: (_ for _ in ()).throw(AssertionError("unused")),
                lesson_l_id="", lesson_l_hash="",
                task_id=task_id,
                workspace_root=throwaway_workspace,
                **kw,
            )
        except SystemExit:
            pass
        finally:
            ORCH.subprocess.Popen = real
            if prior_workspace_root is None:
                os.environ.pop("SWARM_WORKSPACE_ROOT", None)
            else:
                os.environ["SWARM_WORKSPACE_ROOT"] = prior_workspace_root
        return [str(c) for c in captured["cmd"]]

    def test_orchestrator_passes_the_structured_execute_flag(self):
        argv = self._captured_argv(execute=True, instance_id=POOL_INSTANCE,
                                   task_id="ENGINEERING-REHEARSAL")
        assert "--execute" in argv
        assert "--instance-id" in argv and POOL_INSTANCE in argv
        assert "--task-id" in argv and "ENGINEERING-REHEARSAL" in argv
        assert "--agent-id" in argv and "coder" in argv

    def test_production_argv_never_uses_the_dead_env_seam(self):
        argv = self._captured_argv(execute=True, instance_id=POOL_INSTANCE)
        assert "F2_ARM_EXEC_CMD" not in " ".join(argv)

    def test_no_execute_means_no_execute_flag(self):
        argv = self._captured_argv()
        assert "--execute" not in argv

    def test_worker_exposes_structured_execution_arguments(self):
        src = (REPO_ROOT / "qwen_train" / "f2_arm_worker.py").read_text("utf-8")
        for flag in ('"--execute"', '"--task-prompt"', '"--task-id"',
                     '"--instance-id"', '"--agent-id"', '"--port"'):
            assert flag in src, f"missing structured argument {flag}"

    def test_worker_invokes_the_adapter_not_a_shell_string(self):
        src = (REPO_ROOT / "qwen_train" / "f2_arm_worker.py").read_text("utf-8")
        assert "adapter.execute_arm_real" in src
        i = src.find("if structured_execute:")
        assert i > 0
        assert "F2ExecutionAdapter" in src[i: i + 3000]

    def test_manifest_and_arm_verification_preserved(self):
        src = (REPO_ROOT / "qwen_train" / "f2_arm_worker.py").read_text("utf-8")
        assert "load_manifest(manifest_path)" in src
        assert "verify_manifest(artifact)" in src
        assert "arm mismatch" in src

    def test_d4_launcher_substitution_absent(self):
        src = (REPO_ROOT / "qwen_train" / "f2_execution_adapter.py").read_text("utf-8")
        assert "resolve_serving_pid(port) or launcher_pid" not in src

    def test_d1_agent_identity_not_used_as_arm(self):
        src = (REPO_ROOT / "qwen_train" / "f2_arm_worker.py").read_text("utf-8")
        i = src.find("def _verify_p2_evidence_binding")
        body = src[i: src.find("def run_worker")]
        assert "manifest_content_address" in body
        assert 'not in (None, artifact.arm)' not in body
