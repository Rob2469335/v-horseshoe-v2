"""v2 budget: executions, tasks, retries, strict allowlist, cross-process safety."""

from __future__ import annotations

import pytest

from qwen_train import f2_authz_budget as B
from qwen_train.f2_authorization import record_digest

BINDING = {"repository_revision": "a" * 40, "candidate_manifest_digest": "b" * 64}


def v2_record(executions: int = 3, tasks: int = 50, retries: int = 2) -> dict:
    return {
        "id": "v2-grant", "schema": "f2-authorization/v2",
        "stage": "S8-001", "operations": ["base_gold_production"],
        "budget": {"max_executions": executions, "max_total_tasks": tasks,
                   "max_retries": retries},
    }


def _reserve(record, root, exec_id, task_count=1):
    return B.reserve(record=record, ledger_root=root, binding=BINDING,
                     binding_digest="c" * 64, stage="S8-001",
                     operation="base_gold_production", execution_id=exec_id,
                     task_count=task_count)


def _race(args):
    record, root, exec_id, n = args
    try:
        B.reserve(record=record, ledger_root=root, binding=BINDING,
                  binding_digest="c" * 64, stage="S8-001",
                  operation="base_gold_production", execution_id=exec_id,
                  task_count=n)
        return "win"
    except Exception:
        return "lose"


# --- policy -----------------------------------------------------------------


def test_all_three_limits_are_required():
    for missing in ("max_executions", "max_total_tasks", "max_retries"):
        rec = v2_record()
        del rec["budget"][missing]
        with pytest.raises(B.BudgetError, match=missing):
            B.budget_of(rec)


def test_v1_has_no_budget():
    assert B.budget_of({"id": "x", "schema": "f2-authorization/v1"}) is None


def test_bounds():
    for bad in (0, -1, B.MAX_EXECUTIONS_CEILING + 1):
        rec = v2_record()
        rec["budget"]["max_executions"] = bad
        with pytest.raises(B.BudgetError, match="max_executions"):
            B.budget_of(rec)
    rec = v2_record()
    rec["budget"]["max_retries"] = -1
    with pytest.raises(B.BudgetError, match="max_retries"):
        B.budget_of(rec)


def test_production_root_fails_closed_with_no_env_seam(monkeypatch, tmp_path):
    assert B.PRODUCTION_BUDGET_ROOT is None
    monkeypatch.setenv("F2_AUTHZ_BUDGET_ROOT", str(tmp_path))
    with pytest.raises(B.BudgetError, match="unset"):
        B.budget_root(None)


# --- executions -------------------------------------------------------------


def test_execution_cap(tmp_path):
    rec = v2_record(executions=2)
    _reserve(rec, tmp_path, "e0")
    _reserve(rec, tmp_path, "e1")
    with pytest.raises(B.BudgetError, match="exhausted"):
        _reserve(rec, tmp_path, "e2")


def test_restart_durability(tmp_path):
    rec = v2_record(executions=2)
    _reserve(rec, tmp_path, "e0")
    assert B.usage(record_digest(rec), tmp_path) == 1
    _reserve(rec, tmp_path, "e1")
    with pytest.raises(B.BudgetError, match="exhausted"):
        _reserve(rec, tmp_path, "e2")


# --- tasks ------------------------------------------------------------------


def test_task_capacity_is_grant_wide(tmp_path):
    rec = v2_record(executions=5, tasks=5)
    _reserve(rec, tmp_path, "e0", task_count=3)
    _reserve(rec, tmp_path, "e1", task_count=2)
    with pytest.raises(B.BudgetError, match="task capacity exhausted"):
        _reserve(rec, tmp_path, "e2", task_count=1)


def test_task_count_is_validated(tmp_path):
    with pytest.raises(B.BudgetError, match="task_count"):
        _reserve(v2_record(), tmp_path, "e0", task_count=0)


# --- retries ----------------------------------------------------------------


def test_retry_cap_is_grant_wide(tmp_path):
    rec = v2_record(retries=2)
    _reserve(rec, tmp_path, "e0", task_count=3)
    B.claim_retry(record=rec, execution_id="e0", task_id="task-0", ledger_root=tmp_path)
    B.claim_retry(record=rec, execution_id="e0", task_id="task-1", ledger_root=tmp_path)
    with pytest.raises(B.BudgetError, match="retry capacity exhausted"):
        B.claim_retry(record=rec, execution_id="e0", task_id="task-2", ledger_root=tmp_path)


def test_retry_for_same_execution_and_task_is_refused(tmp_path):
    rec = v2_record(retries=5)
    _reserve(rec, tmp_path, "e0", task_count=1)
    B.claim_retry(record=rec, execution_id="e0", task_id="task-0", ledger_root=tmp_path)
    with pytest.raises(B.BudgetError, match="already claimed"):
        B.claim_retry(record=rec, execution_id="e0", task_id="task-0", ledger_root=tmp_path)


def test_retry_zero_means_none(tmp_path):
    rec = v2_record(retries=0)
    _reserve(rec, tmp_path, "e0", task_count=1)
    with pytest.raises(B.BudgetError, match="retry capacity exhausted"):
        B.claim_retry(record=rec, execution_id="e0", task_id="task-0", ledger_root=tmp_path)


# --- allowlist --------------------------------------------------------------


def test_unexpected_file_fails_closed(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path, "e0")
    (tmp_path / record_digest(rec) / "README.txt").write_text("x", encoding="utf-8")
    with pytest.raises(B.BudgetError, match="unexpected file"):
        B.usage(record_digest(rec), tmp_path)


def test_renamed_slot_does_not_free_capacity(tmp_path):
    rec = v2_record(executions=1)
    _reserve(rec, tmp_path, "e0")
    d = tmp_path / record_digest(rec)
    (d / "000000.json").rename(d / "000000.bak")
    with pytest.raises(B.BudgetError, match="unexpected file"):
        B.usage(record_digest(rec), tmp_path)
    with pytest.raises(B.BudgetError, match="unexpected file"):
        _reserve(rec, tmp_path, "e1")


def test_unexpected_directory_fails_closed(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path, "e0")
    (tmp_path / record_digest(rec) / "extra").mkdir()
    with pytest.raises(B.BudgetError, match="unexpected directory"):
        B.usage(record_digest(rec), tmp_path)


def test_malformed_slot_fails_closed(tmp_path):
    rec = v2_record()
    _reserve(rec, tmp_path, "e0")
    (tmp_path / record_digest(rec) / "000001.json").write_bytes(b"{ truncated")
    with pytest.raises(B.BudgetError, match="malformed reservation"):
        B.usage(record_digest(rec), tmp_path)


# --- concurrency ------------------------------------------------------------


def test_same_id_across_processes_yields_one(tmp_path):
    import concurrent.futures as cf
    rec = v2_record(executions=5)
    with cf.ProcessPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_race, [(rec, str(tmp_path), "same", 1)] * 8))
    assert results.count("win") == 1
    assert B.usage(record_digest(rec), tmp_path) == 1


def test_execution_overspend_across_processes(tmp_path):
    import concurrent.futures as cf
    rec = v2_record(executions=4, tasks=100)
    with cf.ProcessPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(_race, [(rec, str(tmp_path), f"p{i}", 1) for i in range(12)]))
    assert results.count("win") == 4
    assert B.usage(record_digest(rec), tmp_path) == 4


def test_task_overspend_across_processes(tmp_path):
    import concurrent.futures as cf
    rec = v2_record(executions=50, tasks=6)
    with cf.ProcessPoolExecutor(max_workers=6) as ex:
        list(ex.map(_race, [(rec, str(tmp_path), f"t{i}", 2) for i in range(6)]))
    # 6 workers x 2 tasks = 12 requested against a 6-task budget.
    counts = B.audit(record_digest(rec), tmp_path)["tasks"]
    assert counts <= 6, f"task cap overspent: {counts}"


def test_crash_between_id_and_slot_does_not_refund(tmp_path):
    import json
    rec = v2_record(executions=2)
    d = tmp_path / record_digest(rec)
    (d / "exec").mkdir(parents=True)
    payload = {
        "schema": B.RESERVATION_SCHEMA,
        "kind": "execution-claim",
        "execution_id": "crashed",
        "task_count": 1,
        "claimed_at_utc": "2026-10-23T00:00:00Z",
        "pid": 1234,
    }
    (d / "exec" / (__import__("hashlib").sha256(b"crashed").hexdigest() + ".lock")).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(B.BudgetError, match="incomplete reservation"):
        _reserve(rec, tmp_path, "crashed")
    # Incomplete reservation is detected; subsequent reservations fail closed
    with pytest.raises(B.BudgetError, match="incomplete reservation"):
        _reserve(rec, tmp_path, "fresh")
    # Capacity is not refunded automatically
    assert len(list(d.glob("*.json"))) == 0


def test_remaining_reports_all_three(tmp_path):
    rec = v2_record(executions=3, tasks=10, retries=2)
    _reserve(rec, tmp_path, "e0", task_count=4)
    B.claim_retry(record=rec, execution_id="e0", task_id="task-0", ledger_root=tmp_path)
    assert B.remaining(rec, tmp_path) == {"executions": 2, "tasks": 6, "retries": 1}
