"""Target-credit rules: 299/300, infra exclusion, cross-chain dedupe, schema floor."""

from __future__ import annotations

import hashlib
import json

import pytest

from qwen_train import f2_authz_budget as B
from qwen_train.f2_s8_batch import (
    _ALLOW_UNREGISTERED_CHAINS,
    reconcile_admissions_across_chains,
)
from qwen_train.f2_s8_factory import ADMITTED_OUTCOME, AppendOnlyLedger


def _ledger(path, task_ids, outcomes=None):
    ledger = AppendOnlyLedger(path)
    outcomes = outcomes or {}
    for task_id in task_ids:
        if task_id in outcomes:
            ledger.append(
                {
                    "task_id": task_id,
                    "outcome_class": outcomes[task_id][0],
                    "outcome": outcomes[task_id][1],
                }
            )
        else:
            ledger.append(
                {
                    "task_id": task_id,
                    "outcome_class": "scientific",
                    "outcome": ADMITTED_OUTCOME,
                }
            )
    return ledger


def test_target_below_300_is_a_schema_error():
    record = {
        "id": "target-299",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["base_gold_production"],
        "not_before": "2000-01-01T00:00:00+00:00",
        "not_after": "2100-12-31T23:59:59+00:00",
        "target_credited": 299,
        "budget": {"max_executions": 1, "max_total_tasks": 1, "max_retries": 0},
    }
    with pytest.raises(B.BudgetError, match="target_credited"):
        B.budget_of(record)


def test_299_admitted_plus_infra_credits_299(tmp_path):
    tasks = [f"t{i}" for i in range(300)]
    ledger = _ledger(
        tmp_path / "chain.jsonl",
        tasks,
        {tasks[-1]: ("infrastructure", "timeout")},
    )
    out = reconcile_admissions_across_chains(
        [ledger.path],
        tasks,
        allow_unregistered_chains=_ALLOW_UNREGISTERED_CHAINS,
    )
    assert out["credited_count"] == 299


def test_task_present_in_two_chains_is_credited_once(tmp_path):
    a = AppendOnlyLedger(tmp_path / "a.jsonl")
    a.append(
        {
            "task_id": "t0",
            "outcome_class": "infrastructure",
            "outcome": "timeout",
        }
    )
    a_raw = a.path.read_bytes()
    a_record = json.loads(a_raw.decode("utf-8").splitlines()[0])
    b = AppendOnlyLedger(tmp_path / "b.jsonl")
    b.append(
        {
            "task_id": "t0",
            "registration_id": "reg",
            "registration_sha256": "5" * 64,
            "outcome": ADMITTED_OUTCOME,
            "outcome_class": "scientific",
            "admitted": True,
            "attempt": 2,
            "correction_of_chain": a.path.name,
            "correction_of_chain_sha256": hashlib.sha256(a_raw).hexdigest(),
            "correction_of_record_digest": a_record["record_digest"],
            "prior_outcome": "timeout",
            "prior_outcome_class": "infrastructure",
        }
    )
    out = reconcile_admissions_across_chains(
        [a.path, b.path],
        ["t0"],
        allow_unregistered_chains=_ALLOW_UNREGISTERED_CHAINS,
    )
    assert out["credited_count"] == 1
