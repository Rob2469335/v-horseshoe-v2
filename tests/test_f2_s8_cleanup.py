"""Cleanup safety tests. Temporary trees only; no real deletion."""

from __future__ import annotations

import hashlib
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
from qwen_train.f2_s8_factory import (
    ADMITTED_OUTCOME,
    REQUIRED_EVIDENCE_ROLES,
    AppendOnlyLedger,
)
from tests.conftest import _REAL_POPEN

TASK = "acme__widget-1"


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(tmp_path):
    root = tmp_path / "ws"
    ws = root / TASK
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "code.py").write_text("x=1\n", encoding="utf-8")
    evidence_root = tmp_path / "evidence"
    role_files: dict[str, pathlib.Path] = {}
    artifacts: dict[str, dict[str, object]] = {}
    for role in REQUIRED_EVIDENCE_ROLES:
        p = evidence_root / f"{role}.bin"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"payload-{role}", encoding="utf-8")
        role_files[role] = p
        artifacts[role] = {
            "name": p.name,
            "sha256": _sha(p),
            "size_bytes": p.stat().st_size,
        }
    gold = role_files["gold_report"]
    record = {
        "task_id": TASK,
        "outcome": ADMITTED_OUTCOME,
        "outcome_class": "scientific",
        "admitted": True,
        "evidence": {
            "name": gold.name,
            "sha256": _sha(gold),
            "size_bytes": gold.stat().st_size,
        },
        "evidence_bundle": {"artifacts": artifacts},
    }
    ledger = tmp_path / "ledger.jsonl"
    AppendOnlyLedger(ledger).append(record)
    manifest = tmp_path / "deletion.jsonl"
    return root, ws, evidence_root, role_files, ledger, manifest


def _delete(env, **over):
    root, ws, evidence_root, role_files, ledger, manifest = env
    kwargs = dict(
        task_id=TASK,
        workspace=ws,
        allowed_roots=[root],
        ledger_paths=[ledger],
        evidence_root=evidence_root,
        evidence_files=dict(role_files),
        manifest_path=manifest,
    )
    kwargs.update(over)
    return delete_workspace(**kwargs)


def test_dry_run_deletes_nothing(tmp_path):
    env = _setup(tmp_path)
    result = _delete(env)
    assert result.deleted is False
    assert result.bytes_freed > 0
    assert env[1].is_dir()
    assert not env[5].exists()


def test_verified_workspace_is_removed_and_manifest_written(tmp_path):
    env = _setup(tmp_path)
    result = _delete(env, allow_delete=True)
    assert result.deleted is True
    assert not env[1].exists()
    rows = [json.loads(line) for line in env[5].read_text("utf-8").splitlines()]
    assert rows and all(row["reason"] == "verified_task_cleanup" for row in rows)


def test_missing_ledger_is_not_treated_as_empty(tmp_path):
    root, ws, _ledger = _record_store(
        tmp_path, {"outcome": "timeout", "outcome_class": "infrastructure"}
    )
    with pytest.raises(CleanupRefused, match="ledger missing"):
        delete_workspace(
            task_id=TASK,
            workspace=ws,
            allowed_roots=[root],
            ledger_paths=[tmp_path / "does-not-exist.jsonl"],
            evidence_root=tmp_path / "e",
            evidence_files={},
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=True,
        )
    assert ws.is_dir()


def test_broken_chain_is_refused_and_workspace_kept(tmp_path):
    env = _setup(tmp_path)
    ledger = env[4]
    raw = ledger.read_text("utf-8").replace(ADMITTED_OUTCOME, "gold_fail")
    ledger.write_text(raw, encoding="utf-8")
    with pytest.raises(CleanupRefused, match="chain invalid"):
        _delete(env, allow_delete=True)
    assert env[1].is_dir()


def test_missing_terminal_scientific_record_is_refused(tmp_path):
    root = tmp_path / "ws"
    ws = root / TASK
    ws.mkdir(parents=True)
    ledger = tmp_path / "ledger.jsonl"
    AppendOnlyLedger(ledger).append(
        {"task_id": TASK, "outcome": "timeout", "outcome_class": "infrastructure"}
    )
    with pytest.raises(CleanupRefused):
        delete_workspace(
            task_id=TASK,
            workspace=ws,
            allowed_roots=[root],
            ledger_paths=[ledger],
            evidence_root=tmp_path / "evidence",
            evidence_files={},
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=True,
        )


def test_conflicting_scientific_records_are_refused(tmp_path):
    env = _setup(tmp_path)
    other = tmp_path / "other.jsonl"
    AppendOnlyLedger(other).append(
        {
            "task_id": TASK,
            "outcome": "gold_fail",
            "outcome_class": "scientific",
            "admitted": False,
        }
    )
    with pytest.raises(CleanupRefused, match="conflicting"):
        _delete(env, ledger_paths=[env[4], other], allow_delete=True)


def test_missing_evidence_role_is_refused(tmp_path):
    env = _setup(tmp_path)
    files = dict(env[3])
    files.pop("run_log" if "run_log" in files else REQUIRED_EVIDENCE_ROLES[0])
    with pytest.raises(CleanupRefused, match="missing"):
        _delete(env, evidence_files=files, allow_delete=True)


def test_fake_evidence_digest_is_refused(tmp_path):
    env = _setup(tmp_path)
    fake = env[2] / "fake.bin"
    fake.write_text("not the bound bytes", encoding="utf-8")
    files = dict(env[3])
    files[REQUIRED_EVIDENCE_ROLES[0]] = fake
    with pytest.raises(CleanupRefused, match="digest mismatch"):
        _delete(env, evidence_files=files, allow_delete=True)
    assert env[1].is_dir()


def test_historical_record_without_artifacts_is_refused(tmp_path):
    root = tmp_path / "ws"
    ws = root / TASK
    ws.mkdir(parents=True)
    ledger = tmp_path / "ledger.jsonl"
    AppendOnlyLedger(ledger).append(
        {
            "task_id": TASK,
            "outcome": ADMITTED_OUTCOME,
            "outcome_class": "scientific",
            "admitted": True,
            "evidence": None,
        }
    )
    with pytest.raises(CleanupRefused, match="does not bind"):
        delete_workspace(
            task_id=TASK,
            workspace=ws,
            allowed_roots=[root],
            ledger_paths=[ledger],
            evidence_root=tmp_path / "evidence",
            evidence_files={},
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=True,
        )
    assert ws.is_dir()


def _record_store(tmp_path, record):
    """A workspace + one-record ledger for schema-shape tests."""
    root = tmp_path / "ws"
    ws = root / TASK
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "code.py").write_text("x=1\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    AppendOnlyLedger(ledger).append({"task_id": TASK, **record})
    return root, ws, ledger


def test_top_level_artifacts_are_not_the_canonical_binding(tmp_path):
    env = _setup(tmp_path)
    # Rebuild the record with the artifact set at the top level only: this is the
    # shape the factory never writes, and it must not satisfy the gate.
    root, ws, evidence_root, role_files, _ledger, manifest = env
    ledger = tmp_path / "top_level.jsonl"
    top = {
        role: {"name": p.name, "sha256": _sha(p), "size_bytes": p.stat().st_size}
        for role, p in role_files.items()
    }
    AppendOnlyLedger(ledger).append(
        {
            "task_id": TASK,
            "outcome": ADMITTED_OUTCOME,
            "outcome_class": "scientific",
            "admitted": True,
            "evidence": dict(top["gold_report"]),
            "artifacts": top,
        }
    )
    with pytest.raises(CleanupRefused, match="evidence_bundle.artifacts"):
        delete_workspace(
            task_id=TASK,
            workspace=ws,
            allowed_roots=[root],
            ledger_paths=[ledger],
            evidence_root=evidence_root,
            evidence_files=dict(role_files),
            manifest_path=manifest,
            allow_delete=True,
        )
    assert ws.is_dir()


def test_inconsistent_scientific_class_is_refused(tmp_path):
    root, ws, ledger = _record_store(
        tmp_path,
        {"outcome": ADMITTED_OUTCOME, "outcome_class": "infrastructure", "admitted": True},
    )
    with pytest.raises(CleanupRefused, match="inconsistent outcome_class"):
        delete_workspace(
            task_id=TASK, workspace=ws, allowed_roots=[root], ledger_paths=[ledger],
            evidence_root=tmp_path / "e", evidence_files={},
            manifest_path=tmp_path / "m.jsonl", allow_delete=True,
        )
    assert ws.is_dir()


def test_scientific_class_with_infrastructure_outcome_is_refused(tmp_path):
    root, ws, ledger = _record_store(
        tmp_path,
        {"outcome": "timeout", "outcome_class": "scientific", "admitted": False},
    )
    with pytest.raises(CleanupRefused, match="inconsistent outcome_class"):
        delete_workspace(
            task_id=TASK, workspace=ws, allowed_roots=[root], ledger_paths=[ledger],
            evidence_root=tmp_path / "e", evidence_files={},
            manifest_path=tmp_path / "m.jsonl", allow_delete=True,
        )
    assert ws.is_dir()


def test_duplicate_evidence_path_is_refused(tmp_path):
    env = _setup(tmp_path)
    files = dict(env[3])
    files[REQUIRED_EVIDENCE_ROLES[1]] = files[REQUIRED_EVIDENCE_ROLES[0]]
    with pytest.raises(CleanupRefused, match="duplicate evidence path"):
        _delete(env, evidence_files=files, allow_delete=True)
    assert env[1].is_dir()


def test_retryable_timeout_before_terminal_is_accepted(tmp_path):
    env = _setup(tmp_path)
    # Append a retryable infrastructure attempt BEFORE the terminal record.
    ledger = env[4]
    prior = env[1].parent / "prior.jsonl"
    AppendOnlyLedger(prior).append(
        {"task_id": TASK, "outcome": "timeout", "outcome_class": "infrastructure", "attempt": 1}
    )
    result = _delete(env, ledger_paths=[prior, ledger], allow_delete=True)
    assert result.deleted is True
    assert not env[1].exists()


def _two_chain_correction(tmp_path, task, prior_outcome, *, correction_chain=True):
    root = tmp_path / "ws"
    ws = root / task
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "code.py").write_text("x=1\n", encoding="utf-8")
    operative = tmp_path / "s8_pilot_ledger.jsonl"
    corrective = tmp_path / "s8_pilot_ledger_corrected.jsonl"
    prior = AppendOnlyLedger(operative).append(
        {"task_id": task, "outcome": prior_outcome, "outcome_class": "scientific", "admitted": False}
    )
    correction = {
        "task_id": task,
        "outcome": ADMITTED_OUTCOME,
        "outcome_class": "scientific",
        "admitted": True,
        "attempt": 2,
        "correction_of_record_digest": prior["record_digest"],
        "correction_of_chain": "s8_pilot_ledger.jsonl" if correction_chain else "unregistered.jsonl",
    }
    AppendOnlyLedger(corrective).append(correction)
    return root, ws, [operative, corrective]


def test_grandfathered_correction_is_not_a_conflict(tmp_path):
    from qwen_train.f2_s8_cleanup import _ledger_terminal_record

    root, ws, ledgers = _two_chain_correction(
        tmp_path, "HypothesisWorks__hypothesis-4532", "gold_fail"
    )
    record, _ = _ledger_terminal_record("HypothesisWorks__hypothesis-4532", ledgers)
    assert record["outcome"] == ADMITTED_OUTCOME
    # The corrected record binds no canonical evidence bundle, so cleanup still
    # refuses on evidence rather than silently deleting the workspace.
    with pytest.raises(CleanupRefused, match="evidence_bundle.artifacts"):
        delete_workspace(
            task_id="HypothesisWorks__hypothesis-4532",
            workspace=ws,
            allowed_roots=[root],
            ledger_paths=ledgers,
            evidence_root=tmp_path / "e",
            evidence_files={},
            manifest_path=tmp_path / "m.jsonl",
            allow_delete=True,
        )
    assert ws.is_dir()


def test_non_grandfathered_correction_is_refused_as_conflict(tmp_path):
    from qwen_train.f2_s8_cleanup import _ledger_terminal_record

    root, ws, ledgers = _two_chain_correction(tmp_path, "acme__widget-1", "gold_fail")
    with pytest.raises(CleanupRefused, match="conflicting scientific records"):
        _ledger_terminal_record("acme__widget-1", ledgers)
    assert ws.is_dir()


def test_correction_naming_unregistered_chain_is_refused(tmp_path):
    from qwen_train.f2_s8_cleanup import _ledger_terminal_record

    _root, _ws, ledgers = _two_chain_correction(
        tmp_path, "HypothesisWorks__hypothesis-4532", "gold_fail", correction_chain=False
    )
    with pytest.raises(CleanupRefused, match="unregistered chain"):
        _ledger_terminal_record("HypothesisWorks__hypothesis-4532", ledgers)


def test_retry_pending_and_stuck_are_refused(tmp_path):
    env = _setup(tmp_path)
    with pytest.raises(CleanupRefused):
        _delete(env, retry_pending=True, allow_delete=True)
    with pytest.raises(CleanupRefused):
        _delete(env, stuck=True, allow_delete=True)


def test_allowlist_escape_is_refused(tmp_path):
    root = tmp_path / "allowed"
    outside = tmp_path / "outside" / TASK
    outside.mkdir(parents=True)
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
    held = env[1] / "repo" / "held.txt"
    held.write_text("held", encoding="utf-8")
    with held.open("r+") as fh:
        result = _delete(env, allow_delete=True)
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
    os.utime(old, (1_000_000, 1_000_000))
    manifest = tmp_path / "sweep.jsonl"
    dry = sweep_run_temp(
        root=root,
        allowed_roots=[root],
        older_than_seconds=10,
        manifest_path=manifest,
        allow_delete=False,
        now=1_000_100,
    )
    assert dry.deleted is False
    result = sweep_run_temp(
        root=root,
        allowed_roots=[root],
        older_than_seconds=10,
        manifest_path=manifest,
        allow_delete=True,
        now=1_000_100,
    )
    assert result.deleted is True
    assert not old.exists()
    assert new.exists()
