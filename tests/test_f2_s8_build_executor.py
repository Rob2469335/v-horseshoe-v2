"""Adapter tests: pilot_build.build -> BuildCell, with fakes (no real build)."""

from __future__ import annotations

import subprocess
import types

import pytest

from qwen_train.f2_s8_build_executor import (
    DEFAULT_DETERMINISTIC_INCOMPATIBILITY_CODES,
    HostBuildExecutor,
    make_probe_live_builder,
)
from qwen_train.f2_s8_probe_driver import (
    BuildCell,
    BuilderContractError,
    LiveBuilder,
    ProbeLimits,
    run_probe,
)

TASK = "acme__widget-1"


def _auth(selected=(TASK,)):
    return types.SimpleNamespace(selected_task_ids=tuple(selected))


def _input(tmp_path, task=TASK):
    d = tmp_path / "inputs"
    d.mkdir(exist_ok=True)
    (d / f"{task}.json").write_text("{}", "utf-8")
    return d


def _executor(tmp_path, *, build_fn, classify_fn=lambda e: "BUILD_FAILURE_UNCLASSIFIED",
              resolve_url_fn=lambda t, i: "https://example.com/acme/widget.git",
              declared_minor_fn=lambda t, i: "3.11", deterministic=()):
    inputs = _input(tmp_path)
    patches = tmp_path / "patches"
    patches.mkdir(exist_ok=True)
    return HostBuildExecutor(
        _auth(),
        inputs=inputs,
        patches=patches,
        build_fn=build_fn,
        classify_fn=classify_fn,
        resolve_url_fn=resolve_url_fn,
        declared_minor_fn=declared_minor_fn,
        deterministic_incompatibility_codes=deterministic,
    )


def test_success_returns_compatible_with_validated_workspace(tmp_path):
    ws = tmp_path / TASK
    ws.mkdir()

    def build_fn(task_id, url, **kw):
        assert url.endswith("widget.git")
        assert kw["authorized_run"] is not None
        return {"task_id": task_id, "workspace": str(ws), "elapsed_s": 1.5}

    cell = _executor(tmp_path, build_fn=build_fn).build_one(TASK)
    assert isinstance(cell, BuildCell)
    assert cell.classification == "OBSERVED_COMPATIBLE"
    assert cell.workspace == str(ws)
    assert cell.duration_seconds == 1.5


def test_missing_input_is_prerequisite_failure_not_success(tmp_path):
    called = []

    def build_fn(*a, **k):
        called.append(1)
        return {}

    ex = _executor(tmp_path, build_fn=build_fn)
    (ex._inputs / f"{TASK}.json").unlink()  # simulate no acquisition record
    cell = ex.build_one(TASK)
    assert cell.classification == "BUILD_FAILURE"
    assert cell.reason_code == "TASK_INPUT_MISSING"
    assert called == []


def test_no_url_is_source_acquisition_failure(tmp_path):
    called = []

    def build_fn(*a, **k):
        called.append(1)
        return {}

    ex = _executor(tmp_path, build_fn=build_fn, resolve_url_fn=lambda t, i: "")
    cell = ex.build_one(TASK)
    assert cell.classification == "BUILD_FAILURE"
    assert cell.reason_code == "SOURCE_ACQUISITION_FAILURE"
    assert called == []


def test_build_exception_maps_to_reason_code(tmp_path):
    def build_fn(*a, **k):
        raise RuntimeError("Could not find a version that satisfies the requirement foo")

    ex = _executor(
        tmp_path, build_fn=build_fn,
        classify_fn=lambda e: "DEPENDENCY_RESOLUTION_FAILURE",
    )
    cell = ex.build_one(TASK)
    assert cell.classification == "BUILD_FAILURE"
    assert cell.reason_code == "DEPENDENCY_RESOLUTION_FAILURE"
    assert cell.failure  # counts as an infrastructure signal


def test_deterministic_code_becomes_observed_incompatible(tmp_path):
    def build_fn(*a, **k):
        raise RuntimeError("os.uname not available")

    ex = _executor(
        tmp_path, build_fn=build_fn,
        classify_fn=lambda e: "PLATFORM_INCOMPATIBLE",
        deterministic={"PLATFORM_INCOMPATIBLE"},
    )
    cell = ex.build_one(TASK)
    assert cell.classification == "OBSERVED_INCOMPATIBLE"
    assert cell.failure == ""  # not an infrastructure failure


def test_default_deterministic_set_is_empty():
    assert DEFAULT_DETERMINISTIC_INCOMPATIBILITY_CODES == frozenset()


def test_build_not_authorized_is_reraised(tmp_path):
    def build_fn(*a, **k):
        raise RuntimeError("BUILD_NOT_AUTHORIZED: task not in selection")

    ex = _executor(tmp_path, build_fn=build_fn, classify_fn=lambda e: "BUILD_NOT_AUTHORIZED")
    with pytest.raises(RuntimeError, match="BUILD_NOT_AUTHORIZED"):
        ex.build_one(TASK)


def test_timeout_maps_to_build_timeout(tmp_path):
    def build_fn(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pip", timeout=3600)

    ex = _executor(tmp_path, build_fn=build_fn)
    cell = ex.build_one(TASK)
    assert cell.reason_code == "BUILD_TIMEOUT"


@pytest.mark.parametrize("bad_result", [
    None,
    {"task_id": "someone-else", "workspace": "x"},
    {"task_id": TASK, "workspace": ""},
])
def test_invalid_result_fails_closed(tmp_path, bad_result):
    ws = tmp_path / TASK
    ws.mkdir()

    def build_fn(*a, **k):
        return bad_result

    ex = _executor(tmp_path, build_fn=build_fn)
    with pytest.raises(BuilderContractError):
        ex.build_one(TASK)


def test_foreign_task_refused(tmp_path):
    ex = HostBuildExecutor(
        _auth(selected=("other-task",)),
        inputs=_input(tmp_path),
        patches=tmp_path,
        build_fn=lambda *a, **k: {},
        classify_fn=lambda e: "X",
        resolve_url_fn=lambda t, i: "u",
        declared_minor_fn=lambda t, i: "3.11",
    )
    with pytest.raises(BuilderContractError, match="not in the authorized probe selection"):
        ex.build_one(TASK)


def test_integration_with_live_builder_and_run_probe(tmp_path):
    ws = tmp_path / TASK
    ws.mkdir()

    def build_fn(task_id, url, **kw):
        return {"task_id": task_id, "workspace": str(ws), "elapsed_s": 0.2}

    ex = _executor(tmp_path, build_fn=build_fn)
    builder = make_probe_live_builder(
        _auth(),
        inputs=ex._inputs,
        patches=ex._patches,
        workspace_root=tmp_path,
        build_fn=build_fn,
        classify_fn=lambda e: "BUILD_FAILURE_UNCLASSIFIED",
        resolve_url_fn=lambda t, i: "https://example.com/acme/widget.git",
        declared_minor_fn=lambda t, i: "3.11",
    )
    assert isinstance(builder, LiveBuilder)

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("sentinel\n", "utf-8")
    result = run_probe(
        unknown_task_ids=[TASK],
        limits=ProbeLimits(max_tasks=1, disk_floor_bytes=1, per_task_disk_bytes=1),
        authorize=lambda: None,
        builder=builder,
        free_bytes=lambda: 10**18,
        verify_ledgers=lambda: None,
        cleanup_after_task=None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_COMPLETE"
    assert result.compatible == 1
    assert result.built == 1
    # No credit / protected-state mutation: the admission ledger is untouched.
    assert ledger.read_text("utf-8") == "sentinel\n"


def test_build_failure_does_not_write_credit(tmp_path):
    def build_fn(*a, **k):
        raise RuntimeError("boom")

    ex = _executor(tmp_path, build_fn=build_fn, classify_fn=lambda e: "BUILD_FAILURE_UNCLASSIFIED")
    builder = LiveBuilder(ex.build_one, workspace_root=tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("sentinel\n", "utf-8")
    result = run_probe(
        unknown_task_ids=[TASK],
        limits=ProbeLimits(max_tasks=1, disk_floor_bytes=1, per_task_disk_bytes=1),
        authorize=lambda: None,
        builder=builder,
        free_bytes=lambda: 10**18,
        verify_ledgers=lambda: None,
        cleanup_after_task=None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_INFRA_FAILURE_RATE"
    assert result.compatible == 0
    assert ledger.read_text("utf-8") == "sentinel\n"
