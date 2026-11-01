"""Step 2 budget-hardening tests: atomic units, stuck claims, validity window."""

from __future__ import annotations

import pytest

from qwen_train import f2_authz_budget as B
from qwen_train.f2_authorization import record_digest


def _record(executions=10, tasks=20, retries=10):
    return {
        "id": "phase2-grant",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["base_gold_production"],
        "not_before": "2000-01-01T00:00:00+00:00",
        "not_after": "2100-12-31T23:59:59+00:00",
        "target_credited": 300,
        "budget": {
            "max_executions": executions,
            "max_total_tasks": tasks,
            "max_retries": retries,
        },
    }


def _reserve(root, record, execution_id, task_count=1, task_ids=None):
    return B.reserve(
        record=record,
        binding={},
        binding_digest="c" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id=execution_id,
        task_count=task_count,
        task_ids=task_ids,
        ledger_root=root,
    )


def test_thirty_requested_with_twenty_free_consumes_zero(tmp_path):
    record = _record(executions=5, tasks=20)
    root = tmp_path
    _reserve(root, record, "e0", 10, [f"a{i}" for i in range(10)])
    _reserve(root, record, "e1", 10, [f"b{i}" for i in range(10)])
    digest = record_digest(record)
    before = B.audit(digest, root)
    with pytest.raises(B.BudgetError, match="task capacity exhausted"):
        _reserve(root, record, "e2", 30, [f"c{i}" for i in range(30)])
    after = B.audit(digest, root)
    assert after == before == {"slots": 2, "exec": 2, "tasks": 20, "retries": 0}
    assert not (root / digest / ".reservation.lock").exists()


def test_stuck_reservation_lock_fails_closed_and_remains(tmp_path):
    record = _record()
    digest = record_digest(record)
    gdir = tmp_path / digest
    gdir.mkdir()
    stuck = gdir / ".reservation.lock"
    stuck.mkdir()
    with pytest.raises(B.BudgetError, match="stuck reservation transaction lock"):
        _reserve(tmp_path, record, "e0")
    assert stuck.is_dir()
    stuck.rmdir()


def test_injected_write_failure_rolls_back_all_units(tmp_path, monkeypatch):
    record = _record(tasks=5)
    real_allocate = B._allocate
    calls = {"task": 0}

    def flaky(dirpath, limit, payload, label):
        if label == "task":
            calls["task"] += 1
            if calls["task"] == 2:
                raise B.BudgetError("injected write failure")
        return real_allocate(dirpath, limit, payload, label)

    monkeypatch.setattr(B, "_allocate", flaky)
    with pytest.raises(B.BudgetError, match="injected write failure"):
        _reserve(tmp_path, record, "e0", 2, ["t1", "t2"])
    digest = record_digest(record)
    assert B.audit(digest, tmp_path) == {
        "slots": 0,
        "exec": 0,
        "tasks": 0,
        "retries": 0,
    }
    assert not (tmp_path / digest / ".reservation.lock").exists()


def test_expired_and_not_yet_valid_are_refused_before_store_creation(tmp_path):
    cases = [
        ("expired", "2000-01-01T00:00:00+00:00", "2000-01-02T00:00:00+00:00"),
        (
            "not yet valid",
            "2100-01-01T00:00:00+00:00",
            "2100-01-02T00:00:00+00:00",
        ),
    ]
    for label, not_before, not_after in cases:
        record = dict(_record(), not_before=not_before, not_after=not_after)
        digest = record_digest(record)
        with pytest.raises(B.BudgetError, match=label.replace(" ", r"\s+")):
            _reserve(tmp_path, record, "e0")
        assert not (tmp_path / digest).exists()
