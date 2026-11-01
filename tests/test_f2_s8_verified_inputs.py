"""Verified input snapshot (Argon finding C).

Binding the inputs/patches to the signed grant needs a schema field; this module
implements the part that is safe without one: one read, one hash, one immutable
copy, and the build consumes the copy.
"""

from __future__ import annotations

import pathlib

import pytest

import dataclasses

from qwen_train.f2_s8_verified_inputs import (
    InputIntegrityError,
    snapshot_verified_inputs,
    verify_snapshot_unchanged,
    verify_sources_unchanged,
)

TASK = "owner__widget-1"
TASK2 = "owner__gadget-2"


def _setup(tmp_path: pathlib.Path, tasks=(TASK,)) -> tuple[pathlib.Path, pathlib.Path]:
    inputs = tmp_path / "inputs"
    patches = tmp_path / "patches"
    inputs.mkdir()
    patches.mkdir()
    for task in tasks:
        (inputs / f"{task}.json").write_text(
            '{"task_id": "%s"}' % task, encoding="utf-8"
        )
        (patches / f"{task}.test_patch").write_text("diff --git a/x b/x\n", encoding="utf-8")
        (patches / f"{task}.gold_patch").write_text("diff --git a/y b/y\n", encoding="utf-8")
    return inputs, patches


def _snap(tmp_path, inputs, patches, tasks=(TASK,)):
    return snapshot_verified_inputs(
        inputs, patches, task_ids=tasks, dest_root=tmp_path / "snap"
    )


def test_snapshot_copies_the_exact_bytes(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    for row in v.manifest:
        base = inputs if row["kind"] == "inputs" else patches
        assert (v.root / row["kind"] / row["name"]).read_bytes() == (
            base / row["name"]
        ).read_bytes()
    assert len(v.manifest) == 3
    assert v.task_ids == (TASK,)


def test_manifest_digest_is_stable_for_identical_inputs(tmp_path):
    inputs, patches = _setup(tmp_path)
    a = _snap(tmp_path, inputs, patches)
    b = _snap(tmp_path, inputs, patches)
    assert a.manifest_digest == b.manifest_digest
    assert a.root != b.root


def test_manifest_digest_changes_with_content(tmp_path):
    inputs, patches = _setup(tmp_path)
    before = _snap(tmp_path, inputs, patches).manifest_digest
    (patches / f"{TASK}.gold_patch").write_text("different\n", encoding="utf-8")
    after = _snap(tmp_path, inputs, patches).manifest_digest
    assert before != after


def test_missing_input_record_is_refused(tmp_path):
    inputs, patches = _setup(tmp_path)
    (inputs / f"{TASK}.json").unlink()
    with pytest.raises(InputIntegrityError, match="missing inputs file"):
        _snap(tmp_path, inputs, patches)


def test_missing_test_patch_is_refused(tmp_path):
    inputs, patches = _setup(tmp_path)
    (patches / f"{TASK}.test_patch").unlink()
    with pytest.raises(InputIntegrityError, match="missing patches file"):
        _snap(tmp_path, inputs, patches)


def test_duplicate_task_ids_are_refused(tmp_path):
    inputs, patches = _setup(tmp_path)
    with pytest.raises(InputIntegrityError, match="duplicate task id"):
        _snap(tmp_path, inputs, patches, tasks=(TASK, TASK))


def test_path_traversal_task_id_is_refused(tmp_path):
    inputs, patches = _setup(tmp_path)
    for bad in ("../escape", "owner__x-1/../../y", "..\\escape", ""):
        with pytest.raises(InputIntegrityError, match="safe task directory name"):
            _snap(tmp_path, inputs, patches, tasks=(bad,))


def test_unexpected_file_for_a_selected_task_is_refused(tmp_path):
    inputs, patches = _setup(tmp_path)
    (patches / f"{TASK}.extra_patch").write_text("x", encoding="utf-8")
    with pytest.raises(InputIntegrityError, match="unexpected file"):
        _snap(tmp_path, inputs, patches)


def test_other_tasks_files_are_ignored(tmp_path):
    inputs, patches = _setup(tmp_path, tasks=(TASK, TASK2))
    v = _snap(tmp_path, inputs, patches, tasks=(TASK,))
    assert v.task_ids == (TASK,)
    assert not (v.root / "inputs" / f"{TASK2}.json").exists()


def test_source_drift_after_snapshot_is_detected(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    verify_sources_unchanged(v, inputs, patches)  # unchanged: passes
    (inputs / f"{TASK}.json").write_text('{"task_id": "changed"}', encoding="utf-8")
    with pytest.raises(InputIntegrityError, match="source changed"):
        verify_sources_unchanged(v, inputs, patches)
    # The snapshot itself is untouched: it is what the build consumes.
    assert (v.root / "inputs" / f"{TASK}.json").read_text(encoding="utf-8") == (
        '{"task_id": "%s"}' % TASK
    )


def test_missing_source_after_snapshot_is_detected(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    (patches / f"{TASK}.gold_patch").unlink()
    with pytest.raises(InputIntegrityError, match="source disappeared"):
        verify_sources_unchanged(v, inputs, patches)


def test_missing_directories_are_refused(tmp_path):
    with pytest.raises(InputIntegrityError, match="does not exist"):
        snapshot_verified_inputs(
            tmp_path / "nope", tmp_path / "nope2", task_ids=(TASK,), dest_root=tmp_path
        )


def test_no_selected_tasks_is_refused(tmp_path):
    inputs, patches = _setup(tmp_path)
    with pytest.raises(InputIntegrityError, match="no selected task ids"):
        _snap(tmp_path, inputs, patches, tasks=())


def test_make_probe_live_builder_consumes_the_snapshot(tmp_path, monkeypatch):
    from qwen_train import f2_s8_build_executor as X

    inputs, patches = _setup(tmp_path)
    captured = {}

    class _FakeExecutor:
        def __init__(self, *a, **k):
            captured.update(k)
            self.build_one = lambda task_id: None

    class _FakeLive:
        def __init__(self, build_one, *, workspace_root, task_timeout_seconds=None):
            pass

    class _Authorized:
        selected_task_ids = (TASK,)

    monkeypatch.setattr(X, "HostBuildExecutor", _FakeExecutor)
    monkeypatch.setattr(X, "LiveBuilder", _FakeLive)
    builder = X.make_probe_live_builder(
        _Authorized(),
        inputs=inputs,
        patches=patches,
        workspace_root=tmp_path / "ws",
    )
    assert "_verified_inputs" in str(captured["inputs"])
    assert pathlib.Path(captured["inputs"]) != inputs
    assert builder.verified_inputs is not None
    assert builder.verified_inputs.manifest_digest


# --- G20: the snapshot the build consumes is what must be verified ----------


def test_clean_snapshot_passes(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    verify_snapshot_unchanged(v)  # no raise


def test_changed_snapshot_file_is_detected(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    target = v.inputs_dir / f"{TASK}.json"
    target.write_text('{"task_id": "tampered"}', encoding="utf-8")
    with pytest.raises(InputIntegrityError, match="changed or replaced"):
        verify_snapshot_unchanged(v)


def test_missing_snapshot_file_is_detected(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    (v.patches_dir / f"{TASK}.gold_patch").unlink()
    with pytest.raises(InputIntegrityError, match="missing"):
        verify_snapshot_unchanged(v)


def test_unexpected_snapshot_file_is_detected(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    (v.patches_dir / f"{TASK}.extra_patch").write_text("x", encoding="utf-8")
    with pytest.raises(InputIntegrityError, match="unexpected file"):
        verify_snapshot_unchanged(v)


def test_manifest_digest_mismatch_is_detected(tmp_path):
    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    forged = dataclasses.replace(v, manifest_digest="0" * 64)
    with pytest.raises(InputIntegrityError, match="manifest digest"):
        verify_snapshot_unchanged(forged)


def test_executor_aborts_before_build_when_snapshot_is_tampered(tmp_path):
    from qwen_train import f2_s8_build_executor as X
    from qwen_train.f2_s8_probe_driver import BuilderContractError

    inputs, patches = _setup(tmp_path)
    v = _snap(tmp_path, inputs, patches)
    (v.inputs_dir / f"{TASK}.json").write_text("{}", encoding="utf-8")
    calls = []

    class _Authorized:
        selected_task_ids = (TASK,)

    ex = X.HostBuildExecutor(
        _Authorized(),
        inputs=v.inputs_dir,
        patches=v.patches_dir,
        workspace_root=tmp_path,
        build_fn=lambda *a, **k: calls.append("build") or {},
        classify_fn=lambda exc: "NOISE",
        resolve_url_fn=lambda task, inputs: "https://example.com/r.git",
        declared_minor_fn=lambda task, inputs: "3.10",
        verified_inputs=v,
    )
    with pytest.raises(BuilderContractError, match="SNAPSHOT_INTEGRITY"):
        ex.build_one(TASK)
    assert calls == []
