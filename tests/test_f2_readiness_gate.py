"""F2 pre-flight readiness gate: wiring + fail-closed behavior.

Proves the existing `evaluate_readiness`/`TaskReadiness` mechanism is now an
ACTUAL execution gate in `f2_arm_orchestrator.run_f2_arm`: a failed/absent
readiness blocks the arm before any render/freeze/spawn, and a pass records the
verdict alongside the arm manifest. No operator F2 values are used.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qwen_train import f2_arm_orchestrator as ORCH
from qwen_train.f2_arm_orchestrator import _enforce_readiness
from runtime_v2.services.task_readiness import (
    READINESS_CONDITIONS,
    ReadinessEvidence,
    ReadinessGateError,
    TaskReadiness,
    TaskReadinessError,
)


def _tr(task_id: str = "TASK-1", base_commit: str = "deadbeef") -> TaskReadiness:
    return TaskReadiness(
        task_id=task_id,
        base_commit=base_commit,
        relevant_file_set=("src/module.py",),
    )


def _all_true() -> ReadinessEvidence:
    return ReadinessEvidence(**{c: True for c in READINESS_CONDITIONS})


class TestEnforceReadiness:
    def test_pass(self):
        decl, verdict = _enforce_readiness(
            readiness=_tr(), evidence=_all_true(), task_id="TASK-1", base_commit="deadbeef"
        )
        assert decl.task_id == "TASK-1"
        assert verdict.ready is True

    def test_accepts_dict_declaration_and_evidence(self):
        decl, verdict = _enforce_readiness(
            readiness=_tr().to_dict(),
            evidence={c: True for c in READINESS_CONDITIONS},
            task_id="TASK-1",
            base_commit="",
        )
        assert verdict.ready is True

    def test_missing_declaration_fails_closed(self):
        with pytest.raises(ReadinessGateError):
            _enforce_readiness(readiness=None, evidence=_all_true(), task_id="TASK-1", base_commit="")

    def test_malformed_declaration_fails_closed(self):
        with pytest.raises(TaskReadinessError):
            _enforce_readiness(
                readiness={"schema_version": "wrong", "task_id": "TASK-1"},
                evidence=_all_true(), task_id="TASK-1", base_commit="",
            )

    def test_wrong_relevant_file_set_hash_fails_closed(self):
        d = _tr().to_dict()
        d["relevant_file_set_hash"] = "0" * 64
        with pytest.raises(TaskReadinessError):
            _enforce_readiness(readiness=d, evidence=_all_true(), task_id="TASK-1", base_commit="")

    def test_task_identity_mismatch_fails_closed(self):
        with pytest.raises(ReadinessGateError):
            _enforce_readiness(readiness=_tr("OTHER"), evidence=_all_true(), task_id="TASK-1", base_commit="")

    def test_base_commit_mismatch_fails_closed(self):
        with pytest.raises(ReadinessGateError):
            _enforce_readiness(
                readiness=_tr(base_commit="aaaa"), evidence=_all_true(),
                task_id="TASK-1", base_commit="bbbb",
            )

    def test_incomplete_evidence_fails_closed(self):
        ev = ReadinessEvidence(**{**{c: True for c in READINESS_CONDITIONS}, "R6_learning_signal_sufficient": None})
        with pytest.raises(ReadinessGateError):
            _enforce_readiness(readiness=_tr(), evidence=ev, task_id="TASK-1", base_commit="")

    def test_none_never_becomes_pass(self):
        with pytest.raises(ReadinessGateError):
            _enforce_readiness(readiness=_tr(), evidence=ReadinessEvidence(), task_id="TASK-1", base_commit="")

    def test_r8_is_derived_from_the_declaration(self):
        """A caller cannot assert R8; it is a property of a valid declaration."""
        ev = ReadinessEvidence(**{**{c: True for c in READINESS_CONDITIONS}, "R8_endpoint_measurable": False})
        _, verdict = _enforce_readiness(
            readiness=_tr(), evidence=ev, task_id="TASK-1", base_commit=""
        )
        assert verdict.checks["R8_endpoint_measurable"] is True
        assert verdict.ready is True


class TestOrchestratorGateBlocksExecution:
    @staticmethod
    def _no_spawn():
        called = {"popen": False}
        real = ORCH.subprocess.Popen

        class _P:
            def __init__(self, *a, **k):
                called["popen"] = True
                raise SystemExit("captured")

        ORCH.subprocess.Popen = _P
        return called, real

    def test_missing_readiness_blocks_before_spawn(self, tmp_path):
        called, real = self._no_spawn()
        try:
            with pytest.raises(ReadinessGateError):
                ORCH.run_f2_arm(
                    arm="C0", manifest_dir=tmp_path,
                    worker_script=Path("qwen_train/f2_arm_worker.py"), repo_root=Path("."),
                    render_t=lambda: (), lesson_l_id="", lesson_l_hash="", task_id="TASK-1",
                )
        finally:
            ORCH.subprocess.Popen = real
        assert called["popen"] is False
        # The gate runs before render/freeze: no arm artifacts were persisted.
        assert list(tmp_path.glob("*_readiness.json")) == []
        assert list(tmp_path.glob("*_f2_freeze.json")) == []

    def test_identity_mismatch_blocks_before_spawn(self, tmp_path):
        called, real = self._no_spawn()
        try:
            with pytest.raises(ReadinessGateError):
                ORCH.run_f2_arm(
                    arm="C0", manifest_dir=tmp_path,
                    worker_script=Path("qwen_train/f2_arm_worker.py"), repo_root=Path("."),
                    render_t=lambda: (), lesson_l_id="", lesson_l_hash="", task_id="TASK-1",
                    readiness=_tr("OTHER"), readiness_evidence=_all_true(),
                )
        finally:
            ORCH.subprocess.Popen = real
        assert called["popen"] is False

    def test_pass_records_readiness_and_reaches_spawn(self, tmp_path):
        called, real = self._no_spawn()
        try:
            try:
                ORCH.run_f2_arm(
                    arm="C0", manifest_dir=tmp_path,
                    worker_script=Path("qwen_train/f2_arm_worker.py"), repo_root=Path("."),
                    render_t=lambda: (), lesson_l_id="", lesson_l_hash="", task_id="TASK-1",
                    readiness=_tr(), readiness_evidence=_all_true(),
                )
            except SystemExit:
                pass
        finally:
            ORCH.subprocess.Popen = real
        # Readiness passed -> the arm reached the spawn seam.
        assert called["popen"] is True
        records = list(tmp_path.glob("*_readiness.json"))
        assert len(records) == 1
        rec = json.loads(records[0].read_text(encoding="utf-8"))
        assert rec["task_id"] == "TASK-1"
        assert rec["base_commit"] == "deadbeef"
        assert rec["arm"] == "C0"
        assert rec["relevant_file_set_hash"]
        assert rec["readiness"]["ready"] is True
        assert rec["readiness"]["unmet"] == []
