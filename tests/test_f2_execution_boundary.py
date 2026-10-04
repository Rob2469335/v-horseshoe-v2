"""F2 execution-boundary regression tests.

Proves the ACTUAL production-reachable execution seams are fail-closed behind the
canonical TaskReadiness R1-R8 gate — not merely `run_f2_arm()`:

* `F2ExecutionAdapter.execute_arm_real()`  (worker CLI's only execution route)
* `start_real_p2_production_model()`        (the production model seam => Popen)

The spawn seams are substituted; no real P2, Qdrant, model or production service
is started. The key assertion is always: readiness failure occurs BEFORE the
spawn seam is attempted.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from qwen_train import f2_execution_adapter as ADAPTER
from qwen_train.f2_arm_worker import run_worker
from qwen_train.f2_execution_adapter import F2ExecutionAdapter, terminate_backend
from runtime_v2.services.f2_freeze import freeze_artifact, persist_manifest, verify_manifest
from runtime_v2.services.task_readiness import (
    READINESS_CONDITIONS,
    ReadinessEvidence,
    ReadinessGateError,
    TaskReadiness,
    TaskReadinessError,
    endpoint_measurable,
    evaluate_readiness,
    manifest_readiness_payload,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tr(task_id: str = "TASK-1", base_commit: str = "deadbeef") -> TaskReadiness:
    return TaskReadiness(
        task_id=task_id, base_commit=base_commit, relevant_file_set=("src/module.py",)
    )


def _all_true() -> ReadinessEvidence:
    return ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS})


def _payload(task_id: str = "TASK-1", *, tamper_hash: bool = False) -> dict:
    tr = _tr(task_id)
    ev = _all_true()
    verdict = evaluate_readiness(replace(ev, R8_endpoint_measurable=endpoint_measurable(tr)))
    payload = manifest_readiness_payload(tr, ev, verdict)
    if tamper_hash:
        payload["declaration"]["relevant_file_set_hash"] = "0" * 64
    return payload


def _frozen(tmp_path: Path, *, task_id: str = "TASK-1", with_readiness: bool = True,
            tamper_hash: bool = False) -> Path:
    art = freeze_artifact(
        rendered_artifact="active block",
        arm="T",
        task_id=task_id,
        task_readiness=_payload(task_id, tamper_hash=tamper_hash) if with_readiness else None,
    )
    verify_manifest(art)
    return persist_manifest(art, tmp_path)


@pytest.fixture(autouse=True)
def _replay_env(monkeypatch):
    from runtime_v2.services.f2_replay import clear_replay_state

    monkeypatch.setenv("SWARM_F2_REPLAY", "1")
    monkeypatch.setenv("SWARM_F2_ROLLOUT_ID", "ro-test")
    yield
    # The worker path INSTALLS process-local replay state and PUBLISHES an
    # absolute SWARM_F2_TRAJ_DIR into os.environ; neither may leak into other
    # suites running in this process.
    clear_replay_state()
    os.environ.pop("SWARM_F2_TRAJ_DIR", None)
    monkeypatch.delenv("SWARM_F2_REPLAY", raising=False)


class TestExecuteArmRealBoundary:
    """The adapter's production entry — the worker CLI's only execution route."""

    @staticmethod
    def _adapter(tmp_path: Path, manifest: Path) -> F2ExecutionAdapter:
        return F2ExecutionAdapter(
            arm="T", manifest_path=manifest, repo_root=REPO_ROOT,
            workspace_root=tmp_path, port=8211,
        )

    def test_without_readiness_fails_before_p2_spawn(self, tmp_path):
        manifest = _frozen(tmp_path, with_readiness=False)
        calls: list[str] = []
        real = ADAPTER.start_real_p2_production_model
        ADAPTER.start_real_p2_production_model = lambda **kw: calls.append("spawn")  # type: ignore[assignment]
        try:
            with pytest.raises(ReadinessGateError):
                self._adapter(tmp_path, manifest).execute_arm_real(
                    task_prompt="p", task_id="TASK-1", rollout_id="ro",
                    trajectory_run_id="tr", agent_id="coder", port=8211,
                )
        finally:
            ADAPTER.start_real_p2_production_model = real  # type: ignore[assignment]
        assert calls == []  # readiness failure occurred BEFORE the P2 spawn

    def test_with_tampered_hash_fails_before_p2_spawn(self, tmp_path):
        manifest = _frozen(tmp_path, with_readiness=True, tamper_hash=True)
        calls: list[str] = []
        real = ADAPTER.start_real_p2_production_model
        ADAPTER.start_real_p2_production_model = lambda **kw: calls.append("spawn")  # type: ignore[assignment]
        try:
            with pytest.raises((ReadinessGateError, TaskReadinessError)):
                self._adapter(tmp_path, manifest).execute_arm_real(
                    task_prompt="p", task_id="TASK-1", rollout_id="ro",
                    trajectory_run_id="tr", agent_id="coder", port=8211,
                )
        finally:
            ADAPTER.start_real_p2_production_model = real  # type: ignore[assignment]
        assert calls == []

    def test_with_valid_readiness_reaches_spawn_seam(self, tmp_path):
        manifest = _frozen(tmp_path, with_readiness=True)

        class _Reached(Exception):
            pass

        real = ADAPTER.start_real_p2_production_model

        def _fake(**_kw):
            raise _Reached()

        ADAPTER.start_real_p2_production_model = _fake  # type: ignore[assignment]
        try:
            with pytest.raises(_Reached):
                self._adapter(tmp_path, manifest).execute_arm_real(
                    task_prompt="p", task_id="TASK-1", rollout_id="ro",
                    trajectory_run_id="tr", agent_id="coder", port=8211,
                )
        finally:
            ADAPTER.start_real_p2_production_model = real  # type: ignore[assignment]

    def test_declaration_task_mismatch_fails_before_p2_spawn(self, tmp_path):
        """A declaration for a DIFFERENT task must not authorize this arm."""
        # Freeze the manifest for MANIFEST-TASK but freeze a declaration for a
        # DIFFERENT task inside it: the manifest-bound gate must refuse.
        art = freeze_artifact(
            rendered_artifact="active block", arm="T",
            task_id="MANIFEST-TASK", task_readiness=_payload("TASK-1"),
        )
        verify_manifest(art)
        manifest = persist_manifest(art, tmp_path)
        calls: list[str] = []
        real = ADAPTER.start_real_p2_production_model
        ADAPTER.start_real_p2_production_model = lambda **kw: calls.append("spawn")  # type: ignore[assignment]
        try:
            with pytest.raises(ReadinessGateError):
                self._adapter(tmp_path, manifest).execute_arm_real(
                    task_prompt="p", task_id="MANIFEST-TASK", rollout_id="ro",
                    trajectory_run_id="tr", agent_id="coder", port=8211,
                )
        finally:
            ADAPTER.start_real_p2_production_model = real  # type: ignore[assignment]
        assert calls == []

    def test_r_false_fails_before_p2_spawn(self, tmp_path):
        ev = ReadinessEvidence(**{**{c: True for c in READINESS_CONDITIONS}, "R3_no_regression": False})
        tr = _tr()
        verdict = evaluate_readiness(replace(ev, R8_endpoint_measurable=endpoint_measurable(tr)))
        payload = manifest_readiness_payload(tr, ev, verdict)
        art = freeze_artifact(rendered_artifact="b", arm="T", task_id="TASK-1", task_readiness=payload)
        verify_manifest(art)
        manifest2 = persist_manifest(art, tmp_path)
        calls: list[str] = []
        real = ADAPTER.start_real_p2_production_model
        ADAPTER.start_real_p2_production_model = lambda **kw: calls.append("spawn")  # type: ignore[assignment]
        try:
            with pytest.raises(ReadinessGateError):
                self._adapter(tmp_path, manifest2).execute_arm_real(
                    task_prompt="p", task_id="TASK-1", rollout_id="ro",
                    trajectory_run_id="tr", agent_id="coder", port=8211,
                )
        finally:
            ADAPTER.start_real_p2_production_model = real  # type: ignore[assignment]
        assert calls == []


class TestRunDryRunFalseBoundary:
    """`run(dry_run=False)` drives a real task, so it is also gated."""

    def test_without_readiness_fails_before_backend(self, tmp_path):
        art = freeze_artifact(rendered_artifact="b", arm="T", task_id="TASK-1")  # no readiness
        verify_manifest(art)
        manifest = persist_manifest(art, tmp_path)
        adapter = F2ExecutionAdapter(
            arm="T", manifest_path=manifest, repo_root=REPO_ROOT,
            workspace_root=tmp_path, port=8211,
        )
        calls: list[str] = []
        real = F2ExecutionAdapter._start_fresh_backend
        F2ExecutionAdapter._start_fresh_backend = lambda self: (calls.append("backend"), (None, None))[1]  # type: ignore[assignment]
        try:
            with pytest.raises(ReadinessGateError):
                adapter.run(
                    task_prompt="p", task_id="TASK-1", rollout_id="ro",
                    trajectory_run_id="tr", dry_run=False,
                )
        finally:
            F2ExecutionAdapter._start_fresh_backend = real  # type: ignore[assignment]
        assert calls == []  # failed before any backend spawn


class TestSpawnSeamBoundary:
    """The lowest trusted boundary before the actual Popen."""

    def test_without_readiness_fails_before_popen(self, tmp_path):
        manifest = _frozen(tmp_path, with_readiness=False)
        calls: list[str] = []
        real = ADAPTER._start_real_p2
        ADAPTER._start_real_p2 = lambda **kw: calls.append("popen")  # type: ignore[assignment]
        try:
            with pytest.raises(ReadinessGateError):
                ADAPTER.start_real_p2_production_model(
                    attempt_id="a", repo_root=REPO_ROOT, workspace_root=tmp_path,
                    port=8211, work_dir=tmp_path, manifest_path=manifest,
                    rollout_id="ro", trajectory_run_id="tr",
                )
        finally:
            ADAPTER._start_real_p2 = real  # type: ignore[assignment]
        assert calls == []  # NO Popen was attempted

    def test_with_valid_readiness_reaches_popen(self, tmp_path):
        manifest = _frozen(tmp_path, with_readiness=True)

        class _Reached(Exception):
            pass

        real = ADAPTER._start_real_p2
        ADAPTER._start_real_p2 = lambda **kw: (_ for _ in ()).throw(_Reached())  # type: ignore[assignment]
        try:
            with pytest.raises(_Reached):
                ADAPTER.start_real_p2_production_model(
                    attempt_id="a", repo_root=REPO_ROOT, workspace_root=tmp_path,
                    port=8211, work_dir=tmp_path, manifest_path=manifest,
                    rollout_id="ro", trajectory_run_id="tr",
                )
        finally:
            ADAPTER._start_real_p2 = real  # type: ignore[assignment]


class TestWorkerRoute:
    """The worker CLI has no execution route other than the gated adapter."""

    def test_execute_routes_through_the_gated_adapter(self, tmp_path, monkeypatch):
        calls = {"constructed": 0, "executed": 0}

        class _FakeAdapter:
            def __init__(self, *a, **k):
                calls["constructed"] += 1

            def execute_arm_real(self, **k):
                calls["executed"] += 1
                return {"mode": "fake", "p2_pid": None, "p2_launcher_pid": None,
                        "serving_pid_source": "none", "backend_healthy": False}

        real = ADAPTER.F2ExecutionAdapter
        ADAPTER.F2ExecutionAdapter = _FakeAdapter  # type: ignore[assignment]
        try:
            manifest = _frozen(tmp_path, with_readiness=True)
            monkeypatch.setenv("SWARM_F2_REPO_ROOT", str(REPO_ROOT))
            monkeypatch.setenv("SWARM_F2_TRAJECTORY_RUN_ID", "tr")
            run_worker(argv=[
                "--manifest", str(manifest), "--arm", "T", "--execute",
                "--task-id", "TASK-1",
                "--port", "8211",
            ])
            # ROUTING proof: the worker's ONLY execution route is the (gated)
            # adapter entry, which it did reach. The receipt afterwards enforces
            # the separate P2-delivery-evidence safeguard (D3).
            assert calls["constructed"] == 1 and calls["executed"] == 1
        finally:
            ADAPTER.F2ExecutionAdapter = real  # type: ignore[assignment]

    def test_without_execute_records_delegated_no_execution(self, tmp_path):
        calls = {"constructed": 0, "executed": 0}

        class _FakeAdapter:
            def __init__(self, *a, **k):
                calls["constructed"] += 1

            def execute_arm_real(self, **k):
                calls["executed"] += 1
                return {}

        real = ADAPTER.F2ExecutionAdapter
        ADAPTER.F2ExecutionAdapter = _FakeAdapter  # type: ignore[assignment]
        try:
            manifest = _frozen(tmp_path, with_readiness=True)
            rc = run_worker(argv=["--manifest", str(manifest), "--arm", "T"])
            assert rc == 0
            assert calls["constructed"] == 0 and calls["executed"] == 0
        finally:
            ADAPTER.F2ExecutionAdapter = real  # type: ignore[assignment]
        terminate_backend(None)
