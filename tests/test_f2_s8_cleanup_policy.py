"""Retention policy at cleanup time (Argon finding F).

The historical behaviour is RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE.
DELETE_ALL_AFTER_CLASSIFICATION is not silently pretended: it is refused because
this driver writes no probe report / per-task build log, and cleanup_probe_workspace
requires both as evidence. Refusal is fail-closed.
"""

from __future__ import annotations

import pathlib

import pytest

from qwen_train import f2_s8_cleanup as C
from qwen_train import f2_s8_probe_driver as D

RETAIN = "RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE"
DELETE_ALL = "DELETE_ALL_AFTER_CLASSIFICATION"
TASK = "owner__widget-1"


def _ws(tmp_path: pathlib.Path, task_id: str = TASK, *, make: bool = True) -> pathlib.Path:
    ws = tmp_path / "root" / task_id
    if make:
        (ws / "repo").mkdir(parents=True)
        (ws / "repo" / "file.py").write_text("x = 1\n", encoding="utf-8")
    return ws


def _call(tmp_path, row, *, allow_delete=False, task_id=TASK, policy=None):
    return D.cleanup_probe_task(
        task_id,
        row,
        workspace_root=tmp_path / "root",
        output_dir=tmp_path / "out",
        manifest_path=tmp_path / "out" / "cleanup_manifest.jsonl",
        allow_delete=allow_delete,
        retention_policy=policy,
    )


def test_compatible_is_retained_by_default(tmp_path, monkeypatch):
    ws = _ws(tmp_path)

    def _boom(**_kwargs):
        raise AssertionError("cleanup must not run for a compatible workspace")

    monkeypatch.setattr(C, "cleanup_failed_build", _boom)
    monkeypatch.setattr(C, "cleanup_probe_workspace", _boom)
    _call(tmp_path, {"classification": "OBSERVED_COMPATIBLE"}, allow_delete=True)
    assert ws.is_dir()


def test_compatible_is_retained_under_the_retain_policy(tmp_path):
    ws = _ws(tmp_path)
    _call(tmp_path, {"classification": "OBSERVED_COMPATIBLE"}, policy=RETAIN)
    assert ws.is_dir()


def test_delete_all_is_refused_rather_than_guessed(tmp_path):
    _ws(tmp_path)
    with pytest.raises(D.CleanupPolicyUnsupported, match="DELETE_ALL_AFTER_CLASSIFICATION"):
        _call(tmp_path, {"classification": "OBSERVED_COMPATIBLE"}, policy=DELETE_ALL)


def test_unknown_policy_is_refused(tmp_path):
    with pytest.raises(D.CleanupPolicyUnsupported, match="unsupported retention_policy"):
        _call(tmp_path, {"classification": "OBSERVED_COMPATIBLE"}, policy="WHATEVER")


def test_failed_workspace_is_retained_without_delete_authorization(tmp_path):
    ws = _ws(tmp_path)
    _call(tmp_path, {"classification": "BUILD_FAILURE"}, allow_delete=False)
    assert ws.is_dir()
    assert not (tmp_path / "out" / "cleanup_manifest.jsonl").exists()


def test_failed_workspace_is_deleted_only_with_authorization(tmp_path):
    ws = _ws(tmp_path)
    _call(tmp_path, {"classification": "BUILD_FAILURE"}, allow_delete=True)
    assert not ws.exists()
    manifest = tmp_path / "out" / "cleanup_manifest.jsonl"
    assert manifest.is_file()
    assert "failed_build_cleanup" in manifest.read_text(encoding="utf-8")


def test_incompatible_cleanup_requires_probe_evidence(tmp_path):
    """cleanup_probe_workspace refuses without a probe report; nothing is deleted."""
    ws = _ws(tmp_path)
    with pytest.raises(C.CleanupRefused, match="probe report missing"):
        _call(tmp_path, {"classification": "OBSERVED_INCOMPATIBLE"}, allow_delete=True)
    assert ws.is_dir()


def test_unknown_classification_is_never_cleaned(tmp_path):
    ws = _ws(tmp_path)
    _call(tmp_path, {"classification": "UNKNOWN"}, allow_delete=True)
    assert ws.is_dir()


def test_unrecognized_classification_is_refused(tmp_path):
    with pytest.raises(D.CleanupClassificationUnrecognized):
        _call(tmp_path, {"classification": "SOMETHING_NEW"}, allow_delete=True)


def test_path_traversal_task_id_is_refused(tmp_path):
    _ws(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(C.CleanupRefused):
        D.cleanup_probe_task(
            "../outside",
            {"classification": "BUILD_FAILURE"},
            workspace_root=tmp_path / "root",
            output_dir=tmp_path / "out",
            manifest_path=tmp_path / "out" / "cleanup_manifest.jsonl",
            allow_delete=True,
        )
    assert (outside / "keep.txt").is_file()


def test_cleanup_failure_propagates(tmp_path, monkeypatch):
    _ws(tmp_path)

    def _refuse(**_kwargs):
        raise C.CleanupRefused("simulated refusal")

    monkeypatch.setattr(C, "cleanup_failed_build", _refuse)
    with pytest.raises(C.CleanupRefused, match="simulated refusal"):
        _call(tmp_path, {"classification": "BUILD_FAILURE"}, allow_delete=True)


def test_compatible_plus_failure_flag_is_refused(tmp_path):
    with pytest.raises(D.CleanupClassificationUnrecognized):
        _call(
            tmp_path,
            {"classification": "OBSERVED_COMPATIBLE", "failure": "boom"},
            allow_delete=True,
        )
