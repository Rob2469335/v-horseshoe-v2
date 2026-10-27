"""Adapter tests: pilot_build.build -> BuildCell, with fakes (no real build)."""

from __future__ import annotations

import os
import pathlib
import subprocess
import types

import pytest

from qwen_train.f2_authorization import AuthorizationError
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


def _run_with_abort_payload(payload, *, batch=("t1",), task_ids=("t1",)):
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
