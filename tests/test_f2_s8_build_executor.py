"""Adapter tests: pilot_build.build -> BuildCell, with fakes (no real build)."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import pathlib
import subprocess
import types

import pytest

from qwen_train import f2_s8_build_executor as be
from qwen_train import f2_s8_pilot_build as pb
from qwen_train import f2_s8_probe_driver as pd
from qwen_train.f2_authorization import AuthorizationError
from qwen_train.f2_s8_acquisition import (
    PREFERRED_PYTHON_MINORS,
    discover_interpreters,
)
from qwen_train.f2_s8_build_executor import (
    DEFAULT_DETERMINISTIC_INCOMPATIBILITY_CODES,
    HostBuildExecutor,
    make_probe_live_builder,
)
from qwen_train.f2_s8_cleanup import CleanupRefused, cleanup_failed_build
from qwen_train.f2_s8_probe_driver import (
    BuildCell,
    BuilderContractError,
    LiveBuilder,
    ProbeAuthorizationAbort,
    ProbeLimits,
    cleanup_probe_task,
    run_probe,
)
from qwen_train.f2_s8_v2_gate import V2GateError

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
        workspace_root=tmp_path,
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


# ===========================================================================
# Regression: authorization revalidation failures must abort (not be cells)
# ===========================================================================


def test_v2_gate_error_propagates_and_is_not_a_cell(tmp_path):
    """A real authorization-gate exception must abort, not become BUILD_FAILURE."""

    def build_fn(*a, **k):
        raise V2GateError("PROBE_REVOKED", "grant revoked mid-run")

    ex = _executor(tmp_path, build_fn=build_fn)
    with pytest.raises(V2GateError):
        ex.build_one(TASK)


def test_expired_gate_error_propagates_as_authorization_error(tmp_path):
    def build_fn(*a, **k):
        raise V2GateError("EXPIRED", "probe grant expired")

    ex = _executor(tmp_path, build_fn=build_fn)
    with pytest.raises(AuthorizationError):
        ex.build_one(TASK)


def test_live_builder_propagates_authorization_error(tmp_path):
    def build_one(tid):
        raise V2GateError("PROBE_LEDGER_MUTATION_FORBIDDEN", "admission ledger mutated")

    b = LiveBuilder(build_one, workspace_root=tmp_path)
    with pytest.raises(ProbeAuthorizationAbort) as ei:
        b([TASK])
    # Not swallowed/normalized: the original authorization error is the cause.
    assert isinstance(ei.value.__cause__, AuthorizationError)
    assert ei.value.cells == {}


def _input_multi(tmp_path, tasks):
    d = tmp_path / "inputs"
    d.mkdir(exist_ok=True)
    for t in tasks:
        (d / f"{t}.json").write_text("{}", "utf-8")
    return d


def test_run_probe_aborts_on_authorization_revalidation_failure(tmp_path):
    built = []

    def build_fn(task_id, url, **kw):
        built.append(task_id)
        if task_id == "t2":
            raise V2GateError("PROBE_REVOKED", "revoked mid-run")
        ws = tmp_path / task_id
        ws.mkdir(exist_ok=True)
        return {"task_id": task_id, "workspace": str(ws), "elapsed_s": 0.1}

    ex = HostBuildExecutor(
        _auth(selected=("t1", "t2", "t3")),
        inputs=_input_multi(tmp_path, ["t1", "t2", "t3"]),
        patches=tmp_path,
        workspace_root=tmp_path,
        build_fn=build_fn,
        classify_fn=lambda e: "BUILD_FAILURE_UNCLASSIFIED",
        resolve_url_fn=lambda t, i: "https://example.com/r.git",
        declared_minor_fn=lambda t, i: "3.11",
    )
    builder = LiveBuilder(ex.build_one, workspace_root=tmp_path)
    result = run_probe(
        unknown_task_ids=["t1", "t2", "t3"],
        limits=ProbeLimits(max_tasks=3, disk_floor_bytes=1, per_task_disk_bytes=1, batch_limit=3),
        authorize=lambda: None,
        builder=builder,
        free_bytes=lambda: 10**18,
        verify_ledgers=lambda: None,
        cleanup_after_task=None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_AUTHORIZATION_REFUSED"
    assert result.infrastructure_invalid == 0  # not counted as infra
    assert built == ["t1", "t2"]  # t3 was never built


# ===========================================================================
# Regression: workspace containment
# ===========================================================================


def test_valid_workspace_inside_root_is_accepted(tmp_path):
    ws = tmp_path / TASK
    ws.mkdir()
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": str(ws)},
    )
    cell = ex.build_one(TASK)
    assert cell.classification == "OBSERVED_COMPATIBLE"
    assert pathlib.Path(cell.workspace) == ws.resolve()


def test_workspace_equal_root_is_rejected(tmp_path):
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": str(tmp_path)},
    )
    with pytest.raises(BuilderContractError, match="root"):
        ex.build_one(TASK)


def test_workspace_outside_root_is_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": str(outside)},
    )
    with pytest.raises(BuilderContractError, match="escapes|task-specific"):
        ex.build_one(TASK)


def test_workspace_of_another_task_is_rejected(tmp_path):
    other = tmp_path / "some-other-task"
    other.mkdir()
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": str(other)},
    )
    with pytest.raises(BuilderContractError, match="task-specific"):
        ex.build_one(TASK)


def test_workspace_relative_traversal_is_rejected(tmp_path):
    escape = tmp_path.parent / f"{tmp_path.name}_escape"
    escape.mkdir(exist_ok=True)
    traversal = str(tmp_path / ".." / escape.name)
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": traversal},
    )
    with pytest.raises(BuilderContractError):
        ex.build_one(TASK)


def test_workspace_symlink_escape_is_rejected(tmp_path):
    target = tmp_path.parent / f"{tmp_path.name}_symlink_target"
    target.mkdir(exist_ok=True)
    link = tmp_path / TASK
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform
        pytest.skip(f"symlink creation unsupported: {exc}")
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": str(link)},
    )
    with pytest.raises(BuilderContractError):
        ex.build_one(TASK)


def test_workspace_nonexistent_is_rejected(tmp_path):
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": str(tmp_path / TASK / "nope")},
    )
    with pytest.raises(BuilderContractError):
        ex.build_one(TASK)


@pytest.mark.parametrize("bad", [None, "", 12345])
def test_workspace_malformed_is_rejected(tmp_path, bad):
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": bad},
    )
    with pytest.raises(BuilderContractError):
        ex.build_one(TASK)


def test_cleanup_never_touches_outside_or_foreign(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    task_dir = root / TASK
    task_dir.mkdir()
    foreign = root / "other__repo-2"
    foreign.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    cleanup_probe_task(
        TASK,
        {"classification": "BUILD_FAILURE"},
        workspace_root=root,
        output_dir=tmp_path,
        manifest_path=tmp_path / "cleanup_manifest.jsonl",
        allow_delete=True,
    )
    assert not task_dir.exists()   # the task's own workspace was removed
    assert foreign.exists()        # a foreign task dir is untouched
    assert outside.exists()        # an outside-root dir is untouched


def test_cleanup_refuses_outside_root_target(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(CleanupRefused):
        cleanup_failed_build(
            task_id=TASK,
            workspace=outside,
            allowed_roots=[root],
            failure_class="build",
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=True,
        )
    assert outside.exists()


# ===========================================================================
# Regression: real Windows junction escapes (junctions need no special rights)
# ===========================================================================


def _make_junction(link, target):
    # tests/conftest.py has an autouse subprocess mock; run the real mklink by
    # restoring the genuine Popen (the established _REAL_POPEN pattern).
    from unittest.mock import patch

    from tests.conftest import _REAL_POPEN

    with patch("subprocess.Popen", new=_REAL_POPEN):
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
    if res.returncode != 0:
        # Junctions do not require elevation; a failure is a real test-setup
        # problem, not a missing privilege, so do not hide it with a skip.
        raise AssertionError(f"mklink /J failed: {res.stdout!r} {res.stderr!r}")


def _executor_at(tmp_path, root, build_fn):
    return HostBuildExecutor(
        _auth(),
        inputs=_input(tmp_path),
        patches=tmp_path,
        workspace_root=root,
        build_fn=build_fn,
        classify_fn=lambda e: "BUILD_FAILURE_UNCLASSIFIED",
        resolve_url_fn=lambda t, i: "https://example.com/r.git",
        declared_minor_fn=lambda t, i: "3.11",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_junction_escape_outside_root_is_rejected(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / TASK
    _make_junction(link, outside)
    ex = _executor_at(
        tmp_path, root, lambda t, u, **k: {"task_id": t, "workspace": str(link)}
    )
    with pytest.raises(BuilderContractError):
        ex.build_one(TASK)
    with pytest.raises(CleanupRefused):
        cleanup_failed_build(
            task_id=TASK,
            workspace=link,
            allowed_roots=[root],
            failure_class="build",
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=True,
        )
    assert outside.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_junction_to_another_task_is_rejected(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    other = root / "other__repo-2"
    other.mkdir()
    link = root / TASK
    _make_junction(link, other)
    ex = _executor_at(
        tmp_path, root, lambda t, u, **k: {"task_id": t, "workspace": str(link)}
    )
    with pytest.raises(BuilderContractError):
        ex.build_one(TASK)
    with pytest.raises(CleanupRefused):
        cleanup_failed_build(
            task_id=TASK,
            workspace=link,
            allowed_roots=[root],
            failure_class="build",
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=True,
        )
    assert other.exists()


# ===========================================================================
# Regression: mid-batch authorization abort must not orphan completed work
# ===========================================================================


def test_mid_batch_authorization_abort_records_completed_cells(tmp_path):
    built = []
    cleaned = []

    def build_fn(task_id, url, **kw):
        built.append(task_id)
        if task_id == "t2":
            raise V2GateError("PROBE_REVOKED", "revoked mid-run")
        ws = tmp_path / task_id
        ws.mkdir(exist_ok=True)
        return {"task_id": task_id, "workspace": str(ws), "elapsed_s": 0.1}

    ex = HostBuildExecutor(
        _auth(selected=("t1", "t2", "t3")),
        inputs=_input_multi(tmp_path, ["t1", "t2", "t3"]),
        patches=tmp_path,
        workspace_root=tmp_path,
        build_fn=build_fn,
        classify_fn=lambda e: "BUILD_FAILURE_UNCLASSIFIED",
        resolve_url_fn=lambda t, i: "https://example.com/r.git",
        declared_minor_fn=lambda t, i: "3.11",
    )
    builder = LiveBuilder(ex.build_one, workspace_root=tmp_path)

    def cleanup(task_id, row):
        cleaned.append(task_id)

    result = run_probe(
        unknown_task_ids=["t1", "t2", "t3"],
        limits=ProbeLimits(max_tasks=3, disk_floor_bytes=1, per_task_disk_bytes=1, batch_limit=3),
        authorize=lambda: None,
        builder=builder,
        free_bytes=lambda: 10**18,
        verify_ledgers=lambda: None,
        cleanup_after_task=cleanup,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_AUTHORIZATION_REFUSED"
    assert built == ["t1", "t2"]          # t3 never built
    assert cleaned == ["t1"]              # completed task recorded before abort
    assert result.attempted == 1          # only the completed task is counted
    assert result.compatible == 1
    assert result.infrastructure_invalid == 0
    assert "t2" not in cleaned            # no cell/cleanup for the refused task


# ===========================================================================
# Regression: a malformed/foreign abort payload is refused, not recorded
# ===========================================================================


def _run_with_abort_payload(payload, *, task_ids=("t1",), workspace_root=None):
    def builder(_batch):
        raise ProbeAuthorizationAbort(payload, "authorization failed")

    cleaned = []
    result = run_probe(
        unknown_task_ids=list(task_ids),
        limits=ProbeLimits(
            max_tasks=len(task_ids),
            disk_floor_bytes=1,
            per_task_disk_bytes=1,
            batch_limit=max(1, len(task_ids)),
        ),
        authorize=lambda: None,
        builder=builder,
        free_bytes=lambda: 10**18,
        verify_ledgers=lambda: None,
        cleanup_after_task=lambda tid, row: cleaned.append(tid),
        log=lambda _: None,
        workspace_root=workspace_root,
    )
    return result, cleaned


def test_abort_payload_foreign_task_is_refused():
    result, cleaned = _run_with_abort_payload(
        {"tX": BuildCell("tX", "UNKNOWN")}, task_ids=("t1",)
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_non_buildcell_is_refused():
    result, cleaned = _run_with_abort_payload({"t1": "not-a-cell"}, task_ids=("t1",))
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_task_identity_mismatch_is_refused():
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t2", "UNKNOWN")}, task_ids=("t1",)
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_unrecognised_classification_is_refused():
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t1", "NOT_A_CLASS")}, task_ids=("t1",)
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_compatible_with_failure_is_refused():
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t1", "OBSERVED_COMPATIBLE", failure="boom")},
        task_ids=("t1",),
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_non_mapping_is_refused():
    result, cleaned = _run_with_abort_payload(["not", "a", "mapping"], task_ids=("t1",))
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


# --- atomicity: the WHOLE payload is validated before any recording ---------


def test_abort_payload_atomic_valid_then_invalid(tmp_path):
    """A valid first cell must NOT be recorded if a later cell is invalid."""
    ws = tmp_path / "t1"
    ws.mkdir()
    payload = {
        "t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=str(ws)),
        "t2": BuildCell("t2", "NOT_A_CLASS"),
    }
    result, cleaned = _run_with_abort_payload(
        payload, task_ids=("t1", "t2"), workspace_root=tmp_path
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert result.compatible == 0
    assert result.built == 0
    assert cleaned == []  # no recording-side effects


def test_abort_payload_atomic_valid_then_bad_identity(tmp_path):
    ws = tmp_path / "t1"
    ws.mkdir()
    payload = {
        "t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=str(ws)),
        "t2": BuildCell("tX", "UNKNOWN"),  # identity mismatch for key t2
    }
    result, cleaned = _run_with_abort_payload(
        payload, task_ids=("t1", "t2"), workspace_root=tmp_path
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert result.compatible == 0
    assert cleaned == []


# --- compatible cells require a valid, contained workspace -----------------


def test_abort_payload_compatible_without_workspace_is_refused(tmp_path):
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=None)},
        task_ids=("t1",),
        workspace_root=tmp_path,
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert result.compatible == 0
    assert cleaned == []


def test_abort_payload_compatible_nonexistent_workspace_is_refused(tmp_path):
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=str(tmp_path / "nope"))},
        task_ids=("t1",),
        workspace_root=tmp_path,
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_compatible_outside_root_is_refused(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}_abort_outside"
    outside.mkdir(exist_ok=True)
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=str(outside))},
        task_ids=("t1",),
        workspace_root=tmp_path,
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_compatible_foreign_task_workspace_is_refused(tmp_path):
    other = tmp_path / "other-task"
    other.mkdir()
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=str(other))},
        task_ids=("t1",),
        workspace_root=tmp_path,
    )
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert cleaned == []


def test_abort_payload_valid_cell_records_once(tmp_path):
    ws = tmp_path / "t1"
    ws.mkdir()
    result, cleaned = _run_with_abort_payload(
        {"t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=str(ws))},
        task_ids=("t1",),
        workspace_root=tmp_path,
    )
    assert result.stop_reason == "PROBE_AUTHORIZATION_REFUSED"
    assert result.attempted == 1
    assert result.compatible == 1
    assert result.built == 1
    assert cleaned == ["t1"]


# --- Task 1: builder construction must precede the reservation -------------


def test_builder_construction_failure_does_not_reserve(monkeypatch, capsys, tmp_path):
    """If builder construction fails, verify runs but reserve never does."""
    import qwen_train.f2_s8_acquisition as acq
    import qwen_train.f2_s8_build_executor as be
    import qwen_train.f2_s8_v2_gate as gate
    from qwen_train.f2_s8_probe_driver import main as probe_main

    monkeypatch.setattr(acq, "require_unprivileged_identity", lambda entry: None)
    calls = {"verify": 0, "reserve": 0}

    class _Stub:
        execution_id = "probe-test"

    def fake_authorize(**kw):
        if kw.get("reserve_execution"):
            calls["reserve"] += 1
            raise AssertionError("reserve must not run when builder construction fails")
        calls["verify"] += 1
        return _Stub()

    def boom(*_a, **_k):
        raise RuntimeError("builder construct failed")

    monkeypatch.setattr(gate, "authorize_s8_probe_run", fake_authorize)
    monkeypatch.setattr(be, "make_probe_live_builder", boom)
    grant = tmp_path / "grant.json"
    grant.write_text("{}", "utf-8")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()
    budget = tmp_path / "budget"
    rc = probe_main(
        [
            "--live",
            "--grant-file",
            str(grant),
            "--inputs",
            str(inputs),
            "--patches",
            str(patches),
            "--budget-root",
            str(budget),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert calls == {"verify": 1, "reserve": 0}
    assert "PROBE_PREREQUISITES_MISSING" in err
    assert not budget.exists()  # no reservation store created -> unconsumed


def test_real_authorizer_builder_failure_never_reserves(monkeypatch, capsys, tmp_path):
    """Real authorizer verification: if builder construction raises, no budget
    reservation file or ledger row is ever created."""
    import getpass
    import qwen_train.f2_s8_acquisition as acq
    import qwen_train.f2_s8_build_executor as be
    from qwen_train.f2_s8_probe_driver import main as probe_main
    from tests.test_f2_s8_probe_authz import _setup_probe_env

    monkeypatch.setattr(acq, "require_unprivileged_identity", lambda entry: None)

    env = _setup_probe_env(
        tmp_path / "env",
        monkeypatch,
        task_ids=("acme__widget-1",),
        allowed_identities=("f2_s8_builder", getpass.getuser()),
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()
    budget_root = tmp_path / "budget_root"
    ledger_file = tmp_path / "ledger.jsonl"
    ledger_file.write_text("", encoding="utf-8")

    def boom(*_a, **_k):
        raise RuntimeError("simulated builder construction failure")

    monkeypatch.setattr(be, "make_probe_live_builder", boom)

    rc = probe_main(
        [
            "--live",
            "--grant-file", str(env["grant_file"]),
            "--inputs", str(inputs),
            "--patches", str(patches),
            "--budget-root", str(budget_root),
            "--registration-file", str(env["reg_file"]),
            "--registration-manifest", str(env["manifest_file"]),
            "--compat-report", str(env["compat_file"]),
            "--ledgers", str(ledger_file),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "PROBE_PREREQUISITES_MISSING" in err
    assert "simulated builder construction failure" in err
    # Confirm no reservation files (.lock in exec/ or json in tasks/) exist in the budget root
    if budget_root.exists():
        assert list(budget_root.glob("*/exec/*.lock")) == []
        assert list(budget_root.glob("*/tasks/*.json")) == []
        assert list(budget_root.glob("*/*.json")) == []
    # Confirm ledger was not modified
    assert ledger_file.read_text(encoding="utf-8").strip() == ""


def test_real_authorizer_normal_live_run_reserves_after_builder(monkeypatch, tmp_path):
    """On a normal live run, reservation happens exactly once, AFTER builder construction,
    with the exact same execution_id obtained during verify."""
    from typing import Any
    import getpass
    import json
    import qwen_train.f2_s8_acquisition as acq
    import qwen_train.f2_s8_build_executor as be
    import qwen_train.f2_s8_probe_driver as driver
    import qwen_train.f2_s8_v2_gate as gate
    from qwen_train.f2_s8_probe_driver import ProbeResult, main as probe_main
    from tests.test_f2_s8_probe_authz import _setup_probe_env

    monkeypatch.setattr(acq, "require_unprivileged_identity", lambda entry: None)

    env = _setup_probe_env(
        tmp_path / "env",
        monkeypatch,
        task_ids=("acme__widget-1",),
        allowed_identities=("f2_s8_builder", getpass.getuser()),
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    patches = tmp_path / "patches"
    patches.mkdir()
    budget_root = tmp_path / "budget_root"
    ledger_file = tmp_path / "ledger.jsonl"
    ledger_file.write_text("", encoding="utf-8")

    events: list[tuple[str, Any]] = []
    real_authorize = gate.authorize_s8_probe_run

    def spy_authorize(**kwargs):
        res = real_authorize(**kwargs)
        events.append((
            "authorize",
            {
                "reserve_execution": kwargs.get("reserve_execution"),
                "execution_id": res.execution_id,
            },
        ))
        return res

    monkeypatch.setattr(gate, "authorize_s8_probe_run", spy_authorize)

    class DummyLiveBuilder:
        def __call__(self, batch):
            return {}

    def spy_make_builder(*args, **kwargs):
        events.append(("make_builder", None))
        return DummyLiveBuilder()

    monkeypatch.setattr(be, "make_probe_live_builder", spy_make_builder)

    def stub_run_probe(*args, **kwargs):
        events.append(("run_probe", None))
        return ProbeResult(
            stop_reason="PROBE_COMPLETE",
            attempted=1,
            compatible=1,
            incompatible=0,
            unknown=0,
        )

    monkeypatch.setattr(driver, "run_probe", stub_run_probe)

    rc = probe_main(
        [
            "--live",
            "--grant-file", str(env["grant_file"]),
            "--inputs", str(inputs),
            "--patches", str(patches),
            "--budget-root", str(budget_root),
            "--registration-file", str(env["reg_file"]),
            "--registration-manifest", str(env["manifest_file"]),
            "--compat-report", str(env["compat_file"]),
            "--ledgers", str(ledger_file),
            "--workspace-root", str(tmp_path / "ws"),
        ]
    )
    assert rc == 0

    # 1. Authorize was called first with reserve_execution=False
    assert events[0][0] == "authorize"
    assert events[0][1]["reserve_execution"] is False
    verify_exec_id = events[0][1]["execution_id"]
    assert verify_exec_id

    # 2. Builder was constructed AFTER verify and BEFORE reservation
    assert events[1][0] == "make_builder"

    # 3. Reservation happened AFTER builder construction, exactly once, with the same execution_id
    assert events[2][0] == "authorize"
    assert events[2][1]["reserve_execution"] is True
    reserve_exec_id = events[2][1]["execution_id"]
    assert reserve_exec_id == verify_exec_id

    assert events[3][0] == "run_probe"

    # 4. Check the budget root: exactly one reservation lock file exists in exec/ and matches execution_id
    exec_locks = list(budget_root.glob("*/exec/*.lock"))
    assert len(exec_locks) == 1
    lock_data = json.loads(exec_locks[0].read_text("utf-8"))
    assert lock_data["execution_id"] == verify_exec_id


# --- Task 2: existing-file workspace (not a directory) ----------------------


def test_workspace_existing_file_is_rejected(tmp_path):
    f = tmp_path / TASK
    f.write_text("x", "utf-8")  # a regular file, not a directory
    ex = _executor(
        tmp_path,
        build_fn=lambda t, u, **k: {"task_id": t, "workspace": str(f)},
    )
    with pytest.raises(BuilderContractError, match="not a directory"):
        ex.build_one(TASK)


# --- Task 3: atomicity across four invalid-second-entry shapes --------------


def _atomic_helper(tmp_path, second_cell):
    ws = tmp_path / "t1"
    ws.mkdir()
    payload = {
        "t1": BuildCell("t1", "OBSERVED_COMPATIBLE", workspace=str(ws)),
        "t2": second_cell,
    }
    return _run_with_abort_payload(
        payload, task_ids=("t1", "t2"), workspace_root=tmp_path
    )


def _assert_contract_no_effects(result, cleaned):
    assert result.stop_reason == "PROBE_BUILDER_CONTRACT"
    assert result.attempted == 0
    assert result.compatible == 0
    assert result.built == 0
    assert cleaned == []


def test_atomic_valid_then_bad_workspace(tmp_path):
    _assert_contract_no_effects(
        *_atomic_helper(tmp_path, BuildCell("t2", "OBSERVED_COMPATIBLE", workspace=None))
    )


def test_atomic_valid_then_not_a_buildcell(tmp_path):
    _assert_contract_no_effects(*_atomic_helper(tmp_path, "not-a-cell"))


def test_atomic_valid_then_task_not_in_allowed_set(tmp_path):
    result, cleaned = _run_with_abort_payload(
        {
            "t1": BuildCell(
                "t1", "OBSERVED_COMPATIBLE", workspace=str(_mkdir(tmp_path, "t1"))
            ),
            "t3": BuildCell("t3", "UNKNOWN"),  # not in task_ids
        },
        task_ids=("t1", "t2"),
        workspace_root=tmp_path,
    )
    _assert_contract_no_effects(result, cleaned)


def test_atomic_valid_then_compatible_with_failure(tmp_path):
    _assert_contract_no_effects(
        *_atomic_helper(
            tmp_path,
            BuildCell("t2", "OBSERVED_COMPATIBLE", failure="boom"),
        )
    )


def _mkdir(tmp_path, name):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


# --- Task 3 note: cleanup raising midway through pass 2 --------------------


def test_abort_payload_cleanup_failure_mid_pass2(tmp_path):
    """A valid payload is recorded in order; a cleanup exception is terminal.

    Pass 1 passed, so pass 2 records t1 (counters + cleanup) then t2 (counters)
    before t2's cleanup raises. The function returns CLEANUP_FAILED; earlier
    recorded counters are **not** rolled back (cleanup is a terminal side
    effect). This is tested here.
    """
    _mkdir(tmp_path, "t1")
    _mkdir(tmp_path, "t2")
    payload = {
        "t1": BuildCell(
            "t1", "OBSERVED_COMPATIBLE", workspace=str(tmp_path / "t1")
        ),
        "t2": BuildCell("t2", "UNKNOWN"),
    }
    seen = []

    def cleanup(task_id, row):
        seen.append(task_id)
        if task_id == "t2":
            raise RuntimeError("cleanup blew up")

    def builder(_batch):
        raise ProbeAuthorizationAbort(payload, "authorization failed")

    result = run_probe(
        unknown_task_ids=["t1", "t2"],
        limits=ProbeLimits(
            max_tasks=2, disk_floor_bytes=1, per_task_disk_bytes=1, batch_limit=2
        ),
        authorize=lambda: None,
        builder=builder,
        free_bytes=lambda: 10**18,
        verify_ledgers=lambda: None,
        cleanup_after_task=cleanup,
        log=lambda _: None,
        workspace_root=tmp_path,
    )
    assert result.stop_reason == "CLEANUP_FAILED"
    assert result.attempted == 2
    assert seen == ["t1", "t2"]


# ===========================================================================
# Regression: the live probe must resolve the DECLARED Python minor from the
# RATIFIED sidecar.  The production default used to be pb._declare_from_input,
# which reads only the materialised input record -- and f2_s8_materialise's
# field whitelist never writes a declaration, so every real task raised
# DeclaredInterpreterError and counted as infrastructure.
# ===========================================================================

#: The 18 real first-batch input records.  Overridable for CI/machines without
#: the local materialisation; tests skip with an explicit reason when absent.
_MATERIALISED = pathlib.Path(os.environ.get(
    "F2_S8_MATERIALISED_INPUTS",
    r"C:\Users\rober\F2_SCRATCH\v2_stageA_probe_2026-10-26\materialised_first18\inputs",
))

_SIDECAR_SCHEMA = "f2-s8-declared-environments/v2"
_PROVENANCE_SCHEMA = "f2-s8-declared-environments-provenance/v2"

#: Real Popen captured at import time, before tests/conftest's autouse
#: ``global_subprocess_mock`` replaces subprocess.Popen for each test.
_REAL_POPEN = subprocess.Popen


def _sha256_file(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _ratified(minor: str = "3.10") -> dict:
    return {
        "status": "ratified",
        "ratified_by": "Rob",
        "ratified_at": "2026-10-28",
        "declared_python_minor": minor,
        "requires_python_compatibility": "OK",
    }


def _sidecar_bundle(root, declarations: dict):
    """Write a valid sidecar+provenance bundle that satisfies every loader rule."""
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def dump(name: str, obj) -> pathlib.Path:
        p = root / name
        p.write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n",
                     encoding="utf-8", newline="\n")
        return p

    old = dump("old_sidecar.json", {"schema": _SIDECAR_SCHEMA, "declarations": {}})
    policy = dump("policy_evidence.json",
                  {"schema": "f2-s8-declared-environment-evidence/v2", "tasks": []})
    side = dump("sidecar.json", {"schema": _SIDECAR_SCHEMA, "ratified_by": "Rob",
                                 "declarations": declarations})
    prov = dump("provenance.json", {
        "schema": _PROVENANCE_SCHEMA,
        "ratified_by": "Rob",
        "ratified_at": "2026-10-28",
        "sidecar": {"path": str(side), "sha256": _sha256_file(side),
                    "schema": _SIDECAR_SCHEMA},
        "source_old_sidecar": {"path": str(old), "sha256": _sha256_file(old),
                               "schema": _SIDECAR_SCHEMA},
        "policy_evidence": {"path": str(policy), "sha256": _sha256_file(policy),
                            "schema": "f2-s8-declared-environment-evidence/v2"},
    })
    return side, prov


def _point_loader_at(monkeypatch, side, prov) -> None:
    # The loader resolves these module globals at call time (they are computed
    # from the environment at import time, so setenv alone would not work).
    monkeypatch.setattr(pb, "DECLARED_ENVIRONMENTS_SIDECAR", pathlib.Path(side))
    monkeypatch.setattr(pb, "DECLARED_ENVIRONMENTS_PROVENANCE", pathlib.Path(prov))


def _default_executor(tmp_path, *, auth=(), build_fn=None, inputs=None, patches=None):
    """A HostBuildExecutor built through the PRODUCTION default path.

    ``declared_minor_fn`` is deliberately omitted: the default under test.
    Only the build itself is stubbed, so nothing is cloned or installed.
    """
    inputs = pathlib.Path(inputs or (tmp_path / "inputs"))
    patches = pathlib.Path(patches or (tmp_path / "patches"))
    inputs.mkdir(parents=True, exist_ok=True)
    patches.mkdir(parents=True, exist_ok=True)
    return HostBuildExecutor(
        types.SimpleNamespace(selected_task_ids=tuple(auth)),
        inputs=inputs,
        patches=patches,
        workspace_root=tmp_path,
        build_fn=build_fn or (lambda task_id, url, **kw: {}),
        classify_fn=pb.classify_build_failure,
        resolve_url_fn=lambda task_id, inputs_dir: "https://example.com/x.git",
    )


def _input_record(path, task, record: dict) -> pathlib.Path:
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    p = path / f"{task}.json"
    p.write_text(json.dumps(record) + "\n", encoding="utf-8", newline="\n")
    return p


def test_default_declared_minor_fn_is_the_ratified_sidecar(tmp_path):
    ex = _default_executor(tmp_path)
    assert ex._declared_minor is pb._declared_minor_for


def test_default_path_resolves_every_real_input_through_the_sidecar(tmp_path, monkeypatch):
    if not _MATERIALISED.is_dir():
        pytest.skip(f"materialised first-batch inputs absent: {_MATERIALISED}")
    tasks = sorted(p.stem for p in _MATERIALISED.glob("*.json"))
    assert len(tasks) == 18, f"expected the 18 materialised inputs, found {len(tasks)}"
    minors = ("3.9", "3.10", "3.11")
    side, prov = _sidecar_bundle(
        tmp_path / "bundle",
        {t: _ratified(minors[i % len(minors)]) for i, t in enumerate(tasks)},
    )
    _point_loader_at(monkeypatch, side, prov)

    handed: dict[str, object] = {}

    def build_fn(task_id, url, **kw):
        handed[task_id] = kw["declared_python_minor"]
        ws = tmp_path / task_id
        ws.mkdir(exist_ok=True)
        return {"task_id": task_id, "workspace": str(ws), "elapsed_s": 0.0}

    ex = _default_executor(tmp_path, build_fn=build_fn, inputs=_MATERIALISED)
    for task in tasks:
        expected = pb._declared_minor_for(task, _MATERIALISED)
        assert expected is not None, task
        cell = ex.build_one(task)
        assert cell.classification == "OBSERVED_COMPATIBLE", (task, cell)
        assert handed[task] == expected, task
    assert len(handed) == 18


def test_absent_task_is_not_silently_defaulted(tmp_path, monkeypatch):
    if not _MATERIALISED.is_dir():
        pytest.skip(f"materialised first-batch inputs absent: {_MATERIALISED}")
    side, prov = _sidecar_bundle(tmp_path / "bundle",
                                 {"some__other-task-1": _ratified("3.11")})
    _point_loader_at(monkeypatch, side, prov)
    task = "a2aproject__a2a-python-443"
    seen: dict[str, object] = {}

    def build_fn(task_id, url, **kw):
        seen["declared"] = kw["declared_python_minor"]
        raise pb.DeclaredInterpreterError(
            f"declared Python minor is missing or unparseable: "
            f"{kw['declared_python_minor']!r}; refusing to assume a policy value")

    ex = _default_executor(tmp_path, build_fn=build_fn, inputs=_MATERIALISED)
    cell = ex.build_one(task)
    expected_code = pb.classify_build_failure(pb.DeclaredInterpreterError(
        "declared Python minor is missing or unparseable: None; "
        "refusing to assume a policy value"))
    assert seen["declared"] is None
    assert cell.classification == "BUILD_FAILURE"
    assert cell.reason_code == expected_code
    assert "declared Python minor" in (cell.failure or "")


def test_blocked_conflict_yields_no_minor(tmp_path, monkeypatch):
    side, prov = _sidecar_bundle(tmp_path / "bundle", {TASK: {
        "status": "blocked_conflict",
        "declared_python_minor": None,
        "evidence_basis": "NOT ESTABLISHED",
    }})
    _point_loader_at(monkeypatch, side, prov)
    inputs = tmp_path / "inputs"
    _input_record(inputs, TASK, {"base_commit": "deadbeef"})
    seen: dict[str, object] = {}

    def build_fn(task_id, url, **kw):
        seen["declared"] = kw["declared_python_minor"]
        ws = tmp_path / task_id
        ws.mkdir(exist_ok=True)
        return {"task_id": task_id, "workspace": str(ws), "elapsed_s": 0.0}

    ex = _default_executor(tmp_path, build_fn=build_fn, inputs=inputs)
    cell = ex.build_one(TASK)
    assert seen["declared"] is None          # blocked: no usable declaration
    assert cell.classification == "OBSERVED_COMPATIBLE"


def test_proposed_declaration_is_never_used(tmp_path, monkeypatch):
    side, prov = _sidecar_bundle(tmp_path / "bundle", {TASK: {
        **_ratified("3.11"), "status": "proposed"}})
    _point_loader_at(monkeypatch, side, prov)
    inputs = tmp_path / "inputs"
    _input_record(inputs, TASK, {"base_commit": "deadbeef"})
    called: list[object] = []

    def build_fn(task_id, url, **kw):
        called.append(task_id)
        return {"task_id": task_id, "workspace": str(tmp_path / task_id)}

    ex = _default_executor(tmp_path, build_fn=build_fn, inputs=inputs)
    cell = ex.build_one(TASK)
    assert called == []                       # never built under a proposed record
    assert cell.classification == "BUILD_FAILURE"
    assert "not ratified" in (cell.failure or "")


def test_input_sidecar_conflict_fails_closed_before_build(tmp_path, monkeypatch):
    side, prov = _sidecar_bundle(tmp_path / "bundle", {TASK: _ratified("3.11")})
    _point_loader_at(monkeypatch, side, prov)
    inputs = tmp_path / "inputs"
    _input_record(inputs, TASK, {"base_commit": "deadbeef",
                                 "install_config": {"base_image_name": "python_base_310"}})
    called: list[object] = []

    def build_fn(task_id, url, **kw):
        called.append(task_id)
        return {"task_id": task_id, "workspace": str(tmp_path / task_id)}

    ex = _default_executor(tmp_path, build_fn=build_fn, inputs=inputs)
    cell = ex.build_one(TASK)
    assert called == []
    assert cell.classification == "BUILD_FAILURE"
    assert "input declares" in (cell.failure or "")


def test_no_production_default_uses_input_only_declaration():
    init_src = inspect.getsource(be.HostBuildExecutor.__init__)
    assert "declared_minor_fn = declared_minor_fn or pb._declared_minor_for" in init_src
    assert "_declare_from_input" not in init_src
    # The composition point must not re-inject an override that bypasses the
    # production default (the driver passes no declared_minor_fn).
    assert "declared_minor_fn" not in inspect.getsource(be.make_probe_live_builder)


@pytest.mark.skipif(
    not pathlib.Path(r"C:\Users\rober\F2_SCRATCH\s8_resolve_out\interp").is_dir(),
    reason="F2 isolated interpreter root is not present on this machine",
)
def test_declared_minors_visible_in_f2builder_search_roots(tmp_path, monkeypatch):
    """The f2builder view is C:\\ plus the interp folder -- never %APPDATA%."""
    from unittest.mock import patch
    # tests/conftest.py installs an autouse subprocess.Popen mock; interpreter
    # discovery must actually execute each candidate, so restore the real one.
    interp = pathlib.Path(r"C:\Users\rober\F2_SCRATCH\s8_resolve_out\interp")
    with patch("subprocess.Popen", _REAL_POPEN):
        found = discover_interpreters(version_of=pb._version_of,
                                      roots=[r"C:\\", interp],
                                      prefer=PREFERRED_PYTHON_MINORS)
        by_minor: dict[tuple[int, int], str] = {}
        for path in found:
            by_minor.setdefault(pb._version_of(path), path)
        for minor in ((3, 7), (3, 8), (3, 9), (3, 10), (3, 11), (3, 13)):
            assert minor in by_minor, f"f2builder-visible roots lack {minor}"
        assert (3, 12) not in by_minor, "3.12 must not be discovered in these roots"

        def available(prefer=None):
            wanted = prefer if prefer is not None else PREFERRED_PYTHON_MINORS
            return [by_minor[m] for m in wanted if m in by_minor]

        monkeypatch.setattr(pb, "available_interpreters", available)
        project = tmp_path / "proj"
        project.mkdir()
        (project / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\nrequires-python = ">=3.7,<99"\n',
            encoding="utf-8")
        assert pb.resolve_declared_interpreter(project, "3.13") == by_minor[(3, 13)]
        with pytest.raises(pb.DeclaredInterpreterError, match="3.12"):
            pb.resolve_declared_interpreter(project, "3.12")


# ===========================================================================
# Regression: HostBuildExecutor advertises workspace_root but the builder wrote
# to the hardcoded ROOT, so any non-default workspace_root produced a
# BuilderContractError after a real build (and a rehearsal could not avoid
# writing into C:\f2_pilot).  The root must be threaded through.
# ===========================================================================


def test_workspace_path_defaults_to_the_fixed_pilot_root():
    assert pb.workspace_path(None, "acme__widget-1") == pb.ROOT / "acme__widget-1"


def test_workspace_path_honours_an_explicit_root(tmp_path):
    assert pb.workspace_path(tmp_path, "acme__widget-1") == tmp_path / "acme__widget-1"


def test_build_uses_workspace_path_with_the_root_argument():
    params = inspect.signature(pb.build).parameters
    assert "root" in params
    assert params["root"].default is None
    src = inspect.getsource(pb.build)
    assert "workspace_path(root" in src
    assert "ws = ROOT / task_id" not in src


def test_executor_forwards_its_workspace_root_to_the_builder(tmp_path):
    inputs = tmp_path / "inputs"
    _input_record(inputs, TASK, {"base_commit": "deadbeef"})
    seen: dict[str, object] = {}

    def build_fn(task_id, url, **kw):
        seen["root"] = kw.get("root")
        ws = tmp_path / task_id
        ws.mkdir(exist_ok=True)
        return {"task_id": task_id, "workspace": str(ws), "elapsed_s": 0.0}

    ex = _default_executor(tmp_path, build_fn=build_fn, inputs=inputs)
    ex.build_one(TASK)
    assert seen["root"] == str(tmp_path)


def test_module_entry_point_publishes_the_canonical_module_name():
    """``python -m`` runs the driver as ``__main__``; the build adapter imports
    the canonical module, which used to load a SECOND copy with a different
    ``BuildCell`` class, so the live builder failed its own isinstance contract.
    The entry point must publish the canonical name before main() runs."""
    src = inspect.getsource(pd)
    marker = ('sys.modules.setdefault("qwen_train.f2_s8_probe_driver", '
              'sys.modules[__name__])')
    assert marker in src
    assert src.index(marker) < src.index("raise SystemExit(main())")

