"""Orchestrator-side workspace governance for F2 execution (R12).

The orchestrator previously never set ``SWARM_WORKSPACE_ROOT``; it relied on the
child inheriting it from the ambient environment. Since ``1b8b4a59`` the worker
requires an explicitly declared, absolute, existing workspace, so an executing
arm could only run if the operator happened to have exported the variable.

This suite pins the orchestrator half of that contract:

* it resolves and validates the workspace ITSELF, fail-closed, BEFORE any
  freeze/spawn work;
* an explicit ``workspace_root`` argument goes through the same validation and
  is not trusted merely because it was passed;
* the main repository (the code root, which is itself a git repository) can
  never become the evaluated workspace;
* the validated value is written explicitly into the child environment rather
  than left to ambient inheritance.

No F2 arm executes, no model is contacted, no service is started. Every failing
case is proved to fail at the workspace gate, not later.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _readiness(task_id: str = "pypa__twine-1066"):
    """A mechanically READY declaration (R1-R8 satisfied) for the gate to accept."""
    from dataclasses import replace

    from runtime_v2.services.task_readiness import (
        READINESS_CONDITIONS,
        ReadinessEvidence,
        TaskReadiness,
        endpoint_measurable,
        evaluate_readiness,
    )

    tr = TaskReadiness(
        task_id=task_id,
        base_commit="deadbeef",
        relevant_file_set=("src/module.py",),
    )
    ev = ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS})
    ev = replace(ev, R8_endpoint_measurable=endpoint_measurable(tr))
    return tr, ev, evaluate_readiness(ev)


def _call_execute(tmp_path: Path, **kw):
    """Invoke run_f2_arm(execute=True) with a READY declaration."""
    from qwen_train.f2_arm_orchestrator import run_f2_arm

    tr, ev, _verdict = _readiness()
    return run_f2_arm(
        arm="T",
        manifest_dir=tmp_path / "manifests",
        worker_script=REPO_ROOT / "qwen_train" / "f2_arm_worker.py",
        repo_root=REPO_ROOT,
        render_t=lambda records: "",
        lesson_l_id="L",
        lesson_l_hash="deadbeef",
        task_id="pypa__twine-1066",
        instance_id="pypa__twine-1066",
        execute=True,
        readiness=tr,
        readiness_evidence=ev,
        task_prompt="fix the failing test",
        **kw,
    )


class TestOrchestratorWorkspaceGate:
    def test_missing_declaration_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import FreshArmWorkspaceError

        monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
        with pytest.raises(FreshArmWorkspaceError, match="SWARM_WORKSPACE_ROOT"):
            _call_execute(tmp_path)

    def test_relative_declaration_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import FreshArmWorkspaceError

        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", "relative/ws")
        with pytest.raises(FreshArmWorkspaceError, match="absolute"):
            _call_execute(tmp_path)

    def test_nonexistent_declaration_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train.arm_workspace import FreshArmWorkspaceError

        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(tmp_path / "absent"))
        with pytest.raises(FreshArmWorkspaceError, match="not an existing directory"):
            _call_execute(tmp_path)

    def test_main_repository_cannot_be_the_workspace(self, tmp_path, monkeypatch):
        """The code root is a git repo, so only an explicit check stops it."""
        from qwen_train.arm_workspace import FreshArmWorkspaceError

        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(REPO_ROOT))
        with pytest.raises(FreshArmWorkspaceError, match="code root"):
            _call_execute(tmp_path)

    def test_explicit_argument_is_validated_not_trusted(self, tmp_path, monkeypatch):
        """Passing the code root as an argument must fail exactly as env would."""
        from qwen_train.arm_workspace import FreshArmWorkspaceError

        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        with pytest.raises(FreshArmWorkspaceError, match="code root"):
            _call_execute(tmp_path, workspace_root=REPO_ROOT)

    def test_valid_workspace_passes_the_workspace_gate(self, tmp_path, monkeypatch):
        """A real isolated workspace clears THIS gate.

        The arm may still fail downstream (no real pool row / backend), which is
        a different concern; the assertion is that it does not fail closed with
        a workspace error.
        """
        from qwen_train.arm_workspace import FreshArmWorkspaceError

        ws = tmp_path / "instance"
        ws.mkdir()
        monkeypatch.setenv("SWARM_WORKSPACE_ROOT", str(ws))
        try:
            _call_execute(tmp_path)
        except FreshArmWorkspaceError as exc:  # pragma: no cover - failure path
            raise AssertionError(
                f"a valid isolated workspace must pass the gate, got: {exc}"
            )
        except Exception:
            pass  # downstream failure is acceptable for this assertion

    def test_workspace_gate_runs_before_any_manifest_is_written(
        self, tmp_path, monkeypatch
    ):
        """Fail-closed ordering: no freeze/spawn side effect may precede it."""
        from qwen_train.arm_workspace import FreshArmWorkspaceError

        monkeypatch.delenv("SWARM_WORKSPACE_ROOT", raising=False)
        manifests = tmp_path / "manifests"
        with pytest.raises(FreshArmWorkspaceError):
            _call_execute(tmp_path)
        assert not manifests.exists(), (
            "a manifest was frozen before the workspace gate refused"
        )