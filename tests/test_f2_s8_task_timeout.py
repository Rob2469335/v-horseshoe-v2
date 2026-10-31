"""Per-task timeout plumbing (Argon finding E).

There is no signed grant field for a task wall clock. The driver's documented
``ProbeLimits.per_task_time_seconds`` was previously never used, and the
collection subprocess defaulted to 900 s regardless. These tests pin the
plumbing that now carries one budget into both layers.
"""

from __future__ import annotations

import pytest

from qwen_train import f2_s8_build_executor as X
from qwen_train import f2_s8_probe_driver as D

TASK = "owner__widget-1"


class _Clock:
    def __init__(self, step: float):
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def _cell(**kwargs):
    return D.BuildCell(TASK, **kwargs)


def test_over_budget_task_is_reported_as_a_timeout():
    builder = D.LiveBuilder(
        lambda task_id: _cell(classification="OBSERVED_COMPATIBLE"),
        workspace_root="C:/nonexistent-root-for-test",
        task_timeout_seconds=5,
        clock=_Clock(step=6.0),
    )
    out = builder([TASK])
    assert out[TASK].classification == "BUILD_FAILURE"
    assert out[TASK].reason_code == "TASK_TIMEOUT"
    assert "exceeded" in out[TASK].failure


def test_under_budget_task_is_unchanged():
    builder = D.LiveBuilder(
        lambda task_id: _cell(classification="UNKNOWN"),
        workspace_root="C:/nonexistent-root-for-test",
        task_timeout_seconds=60,
        clock=_Clock(step=1.0),
    )
    out = builder([TASK])
    assert out[TASK].classification == "UNKNOWN"


def test_non_positive_timeout_is_refused():
    for bad in (0, -1, True):
        with pytest.raises(D.BuilderContractError):
            D.LiveBuilder(lambda t: _cell(), workspace_root="x", task_timeout_seconds=bad)


def test_no_timeout_keeps_historical_behaviour():
    builder = D.LiveBuilder(
        lambda task_id: _cell(classification="UNKNOWN"),
        workspace_root="C:/nonexistent-root-for-test",
        clock=_Clock(step=10000.0),
    )
    assert builder([TASK])[TASK].classification == "UNKNOWN"


def _executor(**kwargs):
    return X.HostBuildExecutor(
        object(),
        inputs="C:/nonexistent-inputs",
        patches="C:/nonexistent-patches",
        build_fn=lambda *a, **k: {},
        classify_fn=lambda exc: "NOISE",
        resolve_url_fn=lambda task, inputs: "",
        declared_minor_fn=lambda task, inputs: None,
        **kwargs,
    )


def test_collect_timeout_is_capped_by_the_task_budget(monkeypatch):
    recorded = {}

    def _fake_collect(ws, f2p, cmd, *, timeout):
        recorded["timeout"] = timeout
        return True, "", "ok"

    monkeypatch.setattr(X, "check_declared_nodes_collect", _fake_collect)
    executor = _executor(task_timeout_seconds=600)
    executor._collect("ws", ("n",), None)
    assert recorded["timeout"] == 600


def test_collect_timeout_never_exceeds_the_historical_default(monkeypatch):
    recorded = {}

    def _fake_collect(ws, f2p, cmd, *, timeout):
        recorded["timeout"] = timeout
        return True, "", "ok"

    monkeypatch.setattr(X, "check_declared_nodes_collect", _fake_collect)
    executor = _executor(task_timeout_seconds=5000)
    executor._collect("ws", ("n",), None)
    assert recorded["timeout"] == 900


def test_collect_timeout_defaults_to_900(monkeypatch):
    recorded = {}

    def _fake_collect(ws, f2p, cmd, *, timeout):
        recorded["timeout"] = timeout
        return True, "", "ok"

    monkeypatch.setattr(X, "check_declared_nodes_collect", _fake_collect)
    executor = _executor()
    executor._collect("ws", ("n",), None)
    assert recorded["timeout"] == 900


def test_make_probe_live_builder_plumbs_the_timeout(monkeypatch):
    captured = {}

    class _FakeExecutor:
        def __init__(self, *a, **k):
            captured["executor"] = k
            self.build_one = lambda task_id: _cell()

    class _FakeLive:
        def __init__(self, build_one, *, workspace_root, task_timeout_seconds=None):
            captured["live"] = task_timeout_seconds

    monkeypatch.setattr(X, "HostBuildExecutor", _FakeExecutor)
    monkeypatch.setattr(X, "LiveBuilder", _FakeLive)
    X.make_probe_live_builder(
        object(),
        inputs="i",
        patches="p",
        workspace_root="w",
        task_timeout_seconds=600,
        verify_inputs=False,
    )
    assert captured["executor"]["task_timeout_seconds"] == 600
    assert captured["live"] == 600


def test_invalid_timeout_is_refused_by_the_executor():
    with pytest.raises(ValueError):
        _executor(task_timeout_seconds=0)
