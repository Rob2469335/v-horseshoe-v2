"""Host non-execution tests for the S8 factory modules.

Property: the factory is repository-side machinery only.  Importing it, planning
with it, and recording results must never start a process, touch Hyper-V, touch
the network, read a credential, or run the pinned evaluator against a real task.

Method: a process-launch recorder patches every launcher this repository can
reach (plus the Hyper-V cmdlets and the evaluator entry point) and raises if any
of them is called while the factory is exercised with synthetic data.
"""
from __future__ import annotations

import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from qwen_train import f2_s8_factory as factory
from qwen_train.f2_s8_factory import (
    ADMITTED_OUTCOME,
    AppendOnlyLedger,
    ExecutionResult,
    RetryPolicy,
    counts,
    plan_run,
    run_once,
)

REG_SHA = "a" * 64
EVAL = {
    "evaluator_id": "f2_pytest_junit_evaluator",
    "version": "1.0.0",
    "implementation_digest": "b" * 64,
    "procedure_id": "f2_pytest_junit_identity_v1",
    "protocol_version": "f2_experiment_j_v1",
}

_SUBPROCESS = ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput")
_OS = ("system", "popen", "spawnv", "spawnve", "spawnlp", "execl", "execv")
_HYPERV_CMDLETS = (
    "New-VM", "Start-VM", "Stop-VM", "Set-VM", "New-VMSwitch", "New-NetNat",
    "Add-VMNetworkAdapterExtendedAcl", "Mount-VHD", "New-VHD", "Checkpoint-VM",
    "New-VMConnect", "Restart-VM", "Set-VMNetworkAdapter",
)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def make(self, name: str):
        def _intercept(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"factory path attempted execution via {name}: {args!r}")

        return _intercept


@contextmanager
def no_side_effects():
    """Fail loudly if the factory reaches any launcher, Hyper-V cmdlet or evaluator."""
    rec = _Recorder()
    with ExitStack() as stack:
        for name in _SUBPROCESS:
            stack.enter_context(patch.object(subprocess, name, rec.make(f"subprocess.{name}")))
        import os

        for name in _OS:
            if hasattr(os, name):
                stack.enter_context(patch.object(os, name, rec.make(f"os.{name}")))
        import asyncio

        for name in ("create_subprocess_exec", "create_subprocess_shell"):
            stack.enter_context(patch.object(asyncio, name, rec.make(f"asyncio.{name}")))
        # The evaluator producer must never be reached by the factory itself.
        import qwen_train.f2_evaluator as evaluator

        stack.enter_context(patch.object(evaluator, "produce_report", rec.make("f2_evaluator.produce_report")))
        stack.enter_context(patch.object(evaluator, "evaluate_execution", rec.make("f2_evaluator.evaluate_execution")))
        yield rec


def _ok(task_id, attempt, **over):
    kw = dict(
        task_id=task_id,
        outcome=ADMITTED_OUTCOME,
        attempt=attempt,
        attempt_id=f"{task_id}#{attempt}",
        disk_id=f"{task_id}-disk",
        evidence_name=f"{task_id}.junit.xml",
        evidence_sha256="c" * 64,
        evidence_size=10,
        evaluator=EVAL,
    )
    kw.update(over)
    return ExecutionResult(**kw)


def test_detector_is_live():
    """Self-test: the recorder must intercept a real launch attempt."""
    with no_side_effects() as rec:
        with pytest.raises(AssertionError, match="attempted execution"):
            subprocess.run([sys.executable, "-c", "raise SystemExit(0)"])
    assert rec.calls == ["subprocess.run"]


def test_factory_module_import_is_inert():
    """The module exposes no launcher surface and importing it runs nothing.

    (No ``importlib.reload``: reloading rebinds module-level classes and would
    break ``isinstance`` identity for the rest of the suite.)
    """
    with no_side_effects() as rec:
        assert factory.SCHEMA_VERSION == "f2_s8_factory_v1"
        assert len(factory.ALL_OUTCOMES) == 7
    assert rec.calls == []
    for name in ("subprocess", "os", "asyncio", "socket", "winreg", "ctypes"):
        assert not hasattr(factory, name), f"factory exposes {name!r}"


def test_planning_and_recording_never_launch_anything(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
    with no_side_effects() as rec:
        plan = plan_run(
            ordered_task_ids=["t-1", "t-2"], ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            policy=RetryPolicy(max_tasks_per_run=2, max_attempts=1),
        )
        assert plan.runnable == ("t-1", "t-2")
        run_once(
            task_id="t-1", ledger=ledger,
            runner=lambda tid, att: _ok(tid, att),
            registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL, ts="T",
        )
        assert counts(ledger, ["t-1", "t-2"])["admitted"] == 1
        assert ledger.verify()[0] is True
    assert rec.calls == []


def test_rejected_paths_also_perform_no_execution(tmp_path):
    ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
    with no_side_effects() as rec:
        with pytest.raises(factory.RegistrationMismatch):
            plan_run(ordered_task_ids=["t-1"], ledger=ledger, registration_id="reg",
                     registration_sha256="z" * 64, expected_registration_sha256=REG_SHA)
        with pytest.raises(factory.FactoryError):
            run_once(task_id="t-1", ledger=ledger,
                     runner=lambda tid, att: _ok(tid, att, evidence_name="../escape.xml"),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
    assert rec.calls == []
    assert ledger.records() == []


def test_the_module_exposes_no_launcher_and_no_hyperv_surface():
    """No import, call or cmdlet that could execute or touch infrastructure."""
    source = Path(factory.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import subprocess",
        "import asyncio",
        "import socket",
        "import winreg",
        "os.system(",
        "os.popen(",
        "produce_report(",
        "evaluate_execution(",
        "New-VM",
        "New-NetNat",
        "Mount-VHD",
        "Get-VM",
    ):
        assert forbidden not in source, f"factory module references {forbidden!r}"
