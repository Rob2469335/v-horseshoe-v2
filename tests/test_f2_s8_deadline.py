"""Absolute task deadline propagation (D0-C).

One deadline is bound at the task boundary and read by every `run()` and stage
check. Tests use short local children only; no candidate build, no network.
"""

from __future__ import annotations

import sys
import time

import pytest

from qwen_train import f2_s8_build_executor as X
from qwen_train import f2_s8_pilot_build as PB
from qwen_train import f2_s8_winjob as W

TASK = "owner__widget-1"


def test_expired_deadline_refuses_before_spawn(monkeypatch):
    calls = []
    monkeypatch.setattr(W, "run_in_job", lambda *a, **k: calls.append(k))
    with PB.task_deadline(time.monotonic() - 1):
        with pytest.raises(PB.TaskDeadlineExceeded):
            PB.run([sys.executable, "-c", "print(1)"])
    assert calls == []


def test_effective_timeout_is_capped_by_remaining(monkeypatch):
    captured = {}

    def fake(cmd, *, cwd=None, timeout=None, env=None):
        captured["timeout"] = timeout
        from subprocess import CompletedProcess

        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(W, "run_in_job", fake)
    with PB.task_deadline(time.monotonic() + 10):
        PB.run([sys.executable, "-c", "pass"], timeout=3600)
    assert 0 < captured["timeout"] <= 10


def test_later_calls_do_not_reset_the_deadline(monkeypatch):
    seen = []

    def fake(cmd, *, cwd=None, timeout=None, env=None):
        seen.append(timeout)
        from subprocess import CompletedProcess

        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(W, "run_in_job", fake)
    with PB.task_deadline(time.monotonic() + 5):
        PB.run(["a"], timeout=3600)
        PB.run(["b"], timeout=3600)
    assert len(seen) == 2
    assert seen[1] <= seen[0] <= 5


def test_stage_check_refuses_after_expiry():
    with PB.task_deadline(time.monotonic() - 0.5):
        with pytest.raises(PB.TaskDeadlineExceeded, match="stage"):
            PB._check_deadline("stage")


def test_deadline_is_cleared_after_the_scope():
    with PB.task_deadline(time.monotonic() + 5):
        assert PB.deadline_remaining() is not None
    assert PB.deadline_remaining() is None


def test_per_call_timeout_remains_upper_bound(monkeypatch):
    captured = {}

    def fake(cmd, *, cwd=None, timeout=None, env=None):
        captured["timeout"] = timeout
        from subprocess import CompletedProcess

        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(W, "run_in_job", fake)
    with PB.task_deadline(time.monotonic() + 3600):
        PB.run(["a"], timeout=7)
    assert captured["timeout"] == 7


def _executor(tmp_path, *, collect_fn, build_fn, task_timeout=1):
    class _Auth:
        selected_task_ids = (TASK,)

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / f"{TASK}.json").write_text("{}", encoding="utf-8")
    return X.HostBuildExecutor(
        _Auth(),
        inputs=inputs,
        patches=tmp_path,
        workspace_root=tmp_path,
        build_fn=build_fn,
        classify_fn=lambda exc: "NOISE",
        resolve_url_fn=lambda task, i: "https://example.com/r.git",
        declared_minor_fn=lambda task, i: "3.10",
        collect_fn=collect_fn,
        task_timeout_seconds=task_timeout,
    )


def test_build_one_binds_and_propagates_the_deadline(tmp_path):
    observed = {}
    ws = tmp_path / TASK
    ws.mkdir()

    def build_fn(*a, **k):
        observed["build"] = PB.deadline_remaining()
        return {"task_id": TASK, "workspace": str(ws), "elapsed_s": 0.1}

    def collect_fn(*a, **k):
        observed["collect"] = PB.deadline_remaining()
        return True, "", "ok"

    ex = _executor(tmp_path, collect_fn=collect_fn, build_fn=build_fn, task_timeout=30)
    cell = ex.build_one(TASK)
    assert cell.classification == "OBSERVED_COMPATIBLE"
    assert observed["build"] is not None and observed["build"] <= 30
    assert observed["collect"] is not None and observed["collect"] <= 30
    assert PB.deadline_remaining() is None  # scope closed


def test_collection_refuses_when_deadline_already_expired(tmp_path):
    ws = tmp_path / "ws"
    (ws / "repo").mkdir(parents=True)
    with PB.task_deadline(time.monotonic() - 1):
        ok, code, detail = X.check_declared_nodes_collect(
            ws, ["t"], "pytest", python=sys.executable, timeout=10
        )
    assert ok is False
    assert code == "COLLECT_TIMEOUT"
    assert "deadline expired" in detail
