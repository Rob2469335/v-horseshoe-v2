"""Cleanup safety tests. All targets are temporary directories; no real deletion."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from unittest.mock import patch

import pytest

from qwen_train.f2_s8_cleanup import (
    CleanupRefused,
    _check_path,
    cleanup_failed_build,
    delete_workspace,
    sweep_run_temp,
)
from qwen_train.f2_s8_factory import AppendOnlyLedger
from tests.conftest import _REAL_POPEN

TASK = "acme__widget-1"


def _setup(tmp_path):
    root = tmp_path / "ws"
    ws = root / TASK
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "junit.xml").write_text("<testsuite/>", encoding="utf-8")
    (ws / "repo" / "code.py").write_text("x=1\n", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    dest = evidence_root / TASK / "junit.xml"
    dest.parent.mkdir(parents=True)
    dest.write_bytes((ws / "repo" / "junit.xml").read_bytes())
    ledger = tmp_path / "ledger.jsonl"
    AppendOnlyLedger(ledger).append(
        {"task_id": TASK, "outcome": "base_fail_gold_pass", "outcome_class": "scientific"}
    )
    manifest = tmp_path / "deletion.jsonl"
    return root, ws, evidence_root, dest, ledger, manifest


def _delete(**over):
    root, ws, evidence_root, dest, ledger, manifest = over.pop("env")
    kwargs = dict(
        task_id=TASK,
        workspace=ws,
        allowed_roots=[root],
        ledger_paths=[ledger],
        evidence_root=evidence_root,
        evidence_files=[(ws / "repo" / "junit.xml", dest)],
        manifest_path=manifest,
    )
    kwargs.update(over)
    return delete_workspace(**kwargs)


def test_dry_run_deletes_nothing(tmp_path):
    env = _setup(tmp_path)
    root, ws, evidence_root, dest, ledger, manifest = env
    result = _delete(env=env)
    assert result.deleted is False
    assert result.bytes_freed > 0
    assert ws.is_dir()
    assert not manifest.exists()


def test_verified_workspace_is_removed_and_manifest_written(tmp_path):
    env = _setup(tmp_path)
    root, ws, evidence_root, dest, ledger, manifest = env
    result = _delete(env=env, allow_delete=True)
    assert result.deleted is True
    assert not ws.exists()
    rows = [json.loads(line) for line in manifest.read_text("utf-8").splitlines()]
    assert rows and all(row["reason"] == "verified_task_cleanup" for row in rows)


def test_unverified_evidence_is_kept(tmp_path):
    env = _setup(tmp_path)
    root, ws, evidence_root, dest, ledger, manifest = env
    dest.write_text("tampered", encoding="utf-8")
    with pytest.raises(CleanupRefused, match="digest mismatch"):
        _delete(env=env, allow_delete=True)
    assert ws.is_dir()


def test_missing_ledger_outcome_is_refused(tmp_path):
    env = _setup(tmp_path)
    root, ws, evidence_root, dest, ledger, manifest = env
    other = tmp_path / "empty.jsonl"
    other.write_text("", encoding="utf-8")
    with pytest.raises(CleanupRefused, match="outcome not recorded"):
        _delete(env=env, ledger_paths=[other], allow_delete=True)
    assert ws.is_dir()


def test_retry_pending_and_stuck_are_refused(tmp_path):
    env = _setup(tmp_path)
    with pytest.raises(CleanupRefused):
        _delete(env=env, retry_pending=True, allow_delete=True)
    with pytest.raises(CleanupRefused):
        _delete(env=env, stuck=True, allow_delete=True)


def test_allowlist_escape_is_refused(tmp_path):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside" / TASK
    outside.mkdir(parents=True)
    (outside / "x").write_text("x", encoding="utf-8")
    with pytest.raises(CleanupRefused, match="outside allowlist"):
        _check_path(outside, [root], TASK)
    with pytest.raises(CleanupRefused):
        _check_path(tmp_path / TASK / ".." / "outside" / TASK, [root], TASK)


def test_windows_junction_is_refused_and_target_untouched(tmp_path):
    if os.name != "nt":
        pytest.skip("junction test is Windows-only")
    root = tmp_path / "allowed"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    link = root / TASK
    with patch("subprocess.Popen", new=_REAL_POPEN):
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
    if made.returncode != 0:
        pytest.skip(f"junction unavailable: {made.stderr or made.stdout}")
    try:
        with pytest.raises(CleanupRefused, match="symlink/junction"):
            _check_path(link, [root], TASK)
        assert (target / "keep.txt").exists()
    finally:
        with patch("subprocess.Popen", new=_REAL_POPEN):
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Users\rober\F2_AUTHZ\ledger\acme__widget-1",
        r"C:\Users\rober\F2_AUTHZ\budget_v2\acme__widget-1",
        r"C:\Users\rober\F2_AUTHZ\s8_001_grant.json",
        r"C:\Users\rober\F2_AUTHZ\trust_root.json",
        r"C:\Users\rober\F2_AUTHZ\f2_owner_signing_key.pem",
        r"C:\Users\rober\F2_EVIDENCE\s8\acme__widget-1",
        r"C:\Users\rober\Projects\v-horseshoe-v2\.env",
        r"C:\Users\rober\Projects\v-horseshoe-v2\candidate_pool.json",
        r"C:\Users\rober\Projects\v-horseshoe-v2\registered_tasks.json",
        r"C:\f2_pilot\wheelhouse\acme__widget-1",
        r"C:\f2_pilot\_cache\acme__widget-1",
        r"C:\Users\rober\F2_SCRATCH\backups\acme__widget-1",
        r"C:\Users\rober\F2_SCRATCH\quarantine\acme__widget-1",
        r"C:\Users\rober\Projects\v-horseshoe-v2\acme__widget-1",
    ],
)
def test_sensitive_paths_are_refused(path):
    p = pathlib.Path(path)
    with pytest.raises(CleanupRefused):
        _check_path(p, [p.parent])


@pytest.mark.skipif(os.name != "nt", reason="Windows file-in-use behavior")
def test_file_in_use_is_skipped(tmp_path):
    env = _setup(tmp_path)
    root, ws, evidence_root, dest, ledger, manifest = env
    held = ws / "repo" / "held.txt"
    held.write_text("held", encoding="utf-8")
    with held.open("r+") as fh:
        result = _delete(env=env, allow_delete=True)
        assert result.skipped
        assert held.exists()
        fh.seek(0)


def test_failed_build_keeps_log(tmp_path):
    root = tmp_path / "ws"
    ws = root / TASK
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "partial.py").write_text("x", encoding="utf-8")
    (ws / "build.log").write_text("failure", encoding="utf-8")
    manifest = tmp_path / "failed.jsonl"
    result = cleanup_failed_build(
        task_id=TASK,
        workspace=ws,
        allowed_roots=[root],
        failure_class="infrastructure",
        manifest_path=manifest,
        allow_delete=True,
    )
    assert result.deleted is True
    assert (ws / "build.log").exists()
    assert not (ws / "repo" / "partial.py").exists()


def test_retention_sweep_only_old_run_temp(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    old = root / "run-old"
    new = root / "run-new"
    old.mkdir()
    new.mkdir()
    (old / "f").write_text("old", encoding="utf-8")
    (new / "f").write_text("new", encoding="utf-8")
    old_time = 1_000_000
    os.utime(old, (old_time, old_time))
    manifest = tmp_path / "sweep.jsonl"
    dry = sweep_run_temp(
        root=root,
        allowed_roots=[root],
        older_than_seconds=10,
        manifest_path=manifest,
        allow_delete=False,
        now=old_time + 100,
    )
    assert dry.deleted is False
    assert old.exists() and new.exists()
    result = sweep_run_temp(
        root=root,
        allowed_roots=[root],
        older_than_seconds=10,
        manifest_path=manifest,
        allow_delete=True,
        now=old_time + 100,
    )
    assert result.deleted is True
    assert not old.exists()
    assert new.exists()
