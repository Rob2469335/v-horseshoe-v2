"""Budget-store integrity: every record must be bound to the granted digest.

Argon audit (2026-10-31): ``classify_store`` accepted ``record_digest_value`` but
never compared it with the records it read, and ``terminal`` was always ``None``.
These tests pin the corrected binding and characterise the terminal field.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from qwen_train import f2_authz_budget as B
from qwen_train.f2_authorization import record_digest

BINDING = {"repository_revision": "a" * 40, "candidate_manifest_digest": "b" * 64}
OTHER = "f" * 64


def v2_record(*, executions: int = 3, tasks: int = 50, retries: int = 2) -> dict:
    return {
        "id": "v2-integrity",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["build_probe"],
        "not_before": "2000-01-01T00:00:00+00:00",
        "not_after": "2100-12-31T23:59:59+00:00",
        "target_credited": 0,
        "awards_credits": False,
        "budget": {
            "max_executions": executions,
            "max_total_tasks": tasks,
            "max_retries": retries,
        },
    }


def _reserve(record, root, exec_id="exec-1", task_ids=("t1", "t2")):
    return B.reserve(
        record=record,
        ledger_root=root,
        binding=BINDING,
        binding_digest="c" * 64,
        stage="S8-001",
        operation="build_probe",
        execution_id=exec_id,
        task_count=len(task_ids),
        task_ids=list(task_ids),
        manifest_digest="d" * 64,
    )


def _gdir(root: pathlib.Path, record) -> pathlib.Path:
    return root / record_digest(record)


def _rewrite(path: pathlib.Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


# --- the corrected binding ---------------------------------------------------


def test_healthy_store_reports_bound(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    report = B.classify_store(_gdir(tmp_path, rec), record_digest(rec))
    assert report["status"] == "HEALTHY"
    assert report["counts"]["slots"] == 1
    assert report["counts"]["tasks"] == 2


def test_slot_digest_mismatch_is_refused(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    gdir = _gdir(tmp_path, rec)
    slot = sorted(p for p in gdir.iterdir() if p.suffix == ".json")[0]
    _rewrite(slot, lambda d: d.__setitem__("record_digest", OTHER))
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "DIGEST_MISMATCH"
    assert "reservation slot" in report["reason"]


def test_task_digest_mismatch_is_refused(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    gdir = _gdir(tmp_path, rec)
    task = sorted((gdir / "tasks").glob("*.json"))[0]
    _rewrite(task, lambda d: d.__setitem__("record_digest", OTHER))
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "DIGEST_MISMATCH"
    assert "task record" in report["reason"]


def test_retry_digest_mismatch_is_refused(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    B.claim_retry(record=rec, execution_id="exec-1", task_id="t1", ledger_root=tmp_path)
    gdir = _gdir(tmp_path, rec)
    retry = sorted((gdir / "retries").glob("*.json"))[0]
    _rewrite(retry, lambda d: d.__setitem__("record_digest", OTHER))
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "DIGEST_MISMATCH"
    assert "retry slot" in report["reason"]


def test_missing_digest_is_refused(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    gdir = _gdir(tmp_path, rec)
    task = sorted((gdir / "tasks").glob("*.json"))[0]
    _rewrite(task, lambda d: d.pop("record_digest", None))
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "DIGEST_MISMATCH"
    assert "missing or malformed" in report["reason"]


def test_malformed_expected_digest_is_refused(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    for bad in ("", "xyz", "A" * 64, "a" * 63):
        report = B.classify_store(_gdir(tmp_path, rec), bad)
        assert report["status"] == "INVALID_DIGEST"


def test_other_grants_digest_cannot_reopen_the_store(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    report = B.classify_store(_gdir(tmp_path, rec), OTHER)
    assert report["status"] == "DIGEST_MISMATCH"


def test_tampered_json_fails_closed(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    gdir = _gdir(tmp_path, rec)
    task = sorted((gdir / "tasks").glob("*.json"))[0]
    task.write_text("{not json", encoding="utf-8")
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "MALFORMED_TASK"


# --- terminal field: characterised, not implemented --------------------------


def test_terminal_is_none_for_a_healthy_store(tmp_path):
    """No terminal marker is written anywhere today; the field is reserved."""
    rec = v2_record()
    _reserve(rec, tmp_path)
    assert B.classify_store(_gdir(tmp_path, rec), record_digest(rec))["terminal"] is None


def test_terminal_is_none_after_a_retry(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    B.claim_retry(record=rec, execution_id="exec-1", task_id="t1", ledger_root=tmp_path)
    assert B.classify_store(_gdir(tmp_path, rec), record_digest(rec))["terminal"] is None


# --- reconciliation states ---------------------------------------------------


def test_interrupted_reservation_requires_reconciliation(tmp_path):
    rec = v2_record()
    gdir = _gdir(tmp_path, rec)
    gdir.mkdir(parents=True)
    (gdir / B._RESERVATION_LOCK_NAME).mkdir()
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert "reservation transaction lock" in report["reason"]


def test_orphaned_execution_lock_requires_reconciliation(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    gdir = _gdir(tmp_path, rec)
    slot = sorted(p for p in gdir.iterdir() if p.suffix == ".json")[0]
    slot.unlink()
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert "incomplete reservation" in report["reason"]


def test_duplicate_execution_id_is_refused_and_consumes_nothing(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    before = B.classify_store(_gdir(tmp_path, rec), record_digest(rec))["counts"]
    with pytest.raises(B.BudgetError, match="duplicate execution_id"):
        _reserve(rec, tmp_path)
    after = B.classify_store(_gdir(tmp_path, rec), record_digest(rec))
    assert after["status"] == "HEALTHY"
    assert after["counts"] == before


def test_unexpected_directory_is_an_anomaly(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path)
    gdir = _gdir(tmp_path, rec)
    (gdir / "surprise").mkdir()
    report = B.classify_store(gdir, record_digest(rec))
    assert report["status"] == "ANOMALY"
