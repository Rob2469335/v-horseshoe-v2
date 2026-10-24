"""Comprehensive regression tests for S8 v2 authorization, budgets, and accounting.

Covers the full matrix required by owner policy decisions OD-1 and OD-2:
  - Schema and compatibility (v1 legacy verification, v1 live rejection, b8 rejection)
  - Grant-wide budgets (executions, tasks, retries)
  - Execution identity and atomic pre-locking
  - Multi-process serialization and persistent coordination locking
  - All twelve mutually exclusive accounting states and fail-closed sentinels
  - Direct consumption bypass closure in runner and orchestrator
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
from pathlib import Path
import pytest

from qwen_train import f2_authorization as A
from qwen_train import f2_authz_budget as B
from qwen_train.f2_authorization import AuthorizationError, record_digest
from tests.f2_authz_testkit import (
    apply_repo_identity,
    keypair,
    signed_record,
    write_record,
    write_trust_root,
)


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    priv, pub = keypair()
    root_path = write_trust_root(tmp_path, {"owner": (priv, pub)})
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(root_path))
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(root_path))
    apply_repo_identity(monkeypatch)
    return priv, tmp_path


def make_v2_record(
    executions: int = 5,
    tasks: int = 50,
    retries: int = 10,
    candidate_pool_digest: str = "a" * 64,
    registration_manifest_sha256: str = "b" * 64,
    **kwargs,
) -> dict:
    rec = signed_record(
        stage="S8-001",
        ops=("base_gold_production",),
        schema="f2-authorization/v2",
        candidate_manifest_digest="m" * 64,
        registration_sha256="r" * 64,
        candidate_pool_digest=candidate_pool_digest,
        registration_manifest_sha256=registration_manifest_sha256,
        budget={
            "max_executions": executions,
            "max_total_tasks": tasks,
            "max_retries": retries,
        },
    )
    rec.update(kwargs)
    return rec


# ===========================================================================
# 1. Schema & Compatibility
# ===========================================================================


def test_valid_historical_v1_verification(auth_env):
    """Historical v1 records verify under legacy contract."""
    priv, tmp = auth_env
    rec = signed_record(
        stage="S8-001",
        ops=("base_gold_production",),
        schema="f2-authorization/v1",
        candidate_manifest_digest="m" * 64,
        registration_sha256="r" * 64,
    )
    p = write_record(tmp, rec, priv)
    verified = A.verify_authorization(
        p,
        stage="S8-001",
        operation="base_gold_production",
        binding={
            "candidate_manifest_digest": "m" * 64,
            "registration_sha256": "r" * 64,
        },
    )
    assert verified["schema"] == "f2-authorization/v1"


def test_rejection_of_v1_live_execution(auth_env):
    """Under OD-2, verify_and_reserve rejects v1 records fail-closed."""
    priv, tmp = auth_env
    rec = signed_record(
        stage="S8-001",
        ops=("base_gold_production",),
        schema="f2-authorization/v1",
        candidate_manifest_digest="m" * 64,
        registration_sha256="r" * 64,
    )
    p = write_record(tmp, rec, priv)
    with pytest.raises(AuthorizationError, match="verify_and_reserve requires schema 'f2-authorization/v2'"):
        A.verify_and_reserve(
            record_path=p,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_manifest_digest": "m" * 64,
                "registration_sha256": "r" * 64,
            },
            budget_ledger_root=tmp / "budget",
        )


def test_rejection_of_unsigned_b8_record(auth_env):
    """Batch b8 has no signature and is strictly prohibited from execution."""
    # Even if an unsigned b8 record JSON is loaded, verification fails closed
    unsigned_b8 = {
        "id": "s8-001-batch-b8-2026-10-23",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["base_gold_production"],
        "approver": "owner",
        "budget": {"max_executions": 1, "max_total_tasks": 5, "max_retries": 0},
        "candidate_pool_digest": "0" * 64,
        "not_before": "2026-10-23T00:00:00Z",
        "not_after": "2026-10-24T00:00:00Z",
        "protocol_digest": "proto",
    }
    _, tmp = auth_env
    p = tmp / "unsigned_b8.json"
    p.write_text(
        json.dumps({
            "record": unsigned_b8,
            "signature": {
                "alg": "ed25519",
                "key_id": "owner",
                "signer": "owner",
                "sig": "UNSIGNED_PENDING_OWNER_SIGNATURE",
            },
        }),
        encoding="utf-8",
    )
    with pytest.raises(AuthorizationError):
        A.verify_authorization(
            p,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_manifest_digest": "m" * 64,
                "registration_sha256": "r" * 64,
                "registration_manifest_sha256": "b" * 64,
            },
        )


def test_strict_v2_required_field_validation(auth_env):
    """A v2 record missing budget, pool, or registration_manifest is rejected."""
    priv, tmp = auth_env
    # Missing budget
    rec_no_budget = make_v2_record()
    del rec_no_budget["budget"]
    p1 = write_record(tmp, rec_no_budget, priv, name="no_budget.json")
    with pytest.raises(AuthorizationError, match="declares no budget object"):
        A.verify_authorization(p1, stage="S8-001", operation="base_gold_production")

    # Missing candidate_pool_digest
    rec_no_pool = make_v2_record()
    del rec_no_pool["candidate_pool_digest"]
    p2 = write_record(tmp, rec_no_pool, priv, name="no_pool.json")
    with pytest.raises(AuthorizationError, match="missing a 64-hex candidate_pool_digest"):
        A.verify_authorization(p2, stage="S8-001", operation="base_gold_production")

    # Missing registration_manifest_sha256
    rec_no_reg = make_v2_record()
    del rec_no_reg["registration_manifest_sha256"]
    p3 = write_record(tmp, rec_no_reg, priv, name="no_reg.json")
    with pytest.raises(AuthorizationError, match="missing a 64-hex registration_manifest_sha256"):
        A.verify_authorization(p3, stage="S8-001", operation="base_gold_production")

    # Malformed registration_manifest_sha256 (uppercase hex or non-hex)
    rec_bad_reg = make_v2_record(registration_manifest_sha256="B" * 64)
    p4 = write_record(tmp, rec_bad_reg, priv, name="bad_reg.json")
    with pytest.raises(AuthorizationError, match="missing a 64-hex registration_manifest_sha256"):
        A.verify_authorization(p4, stage="S8-001", operation="base_gold_production")


def test_missing_or_malformed_v2_budget_values():
    """Booleans, negative values, and out-of-range limits are rejected strictly."""
    rec = make_v2_record()
    # Reject boolean
    rec["budget"]["max_executions"] = True
    with pytest.raises(B.BudgetError, match="malformed"):
        B.budget_of(rec)

    rec["budget"]["max_executions"] = 0
    with pytest.raises(B.BudgetError, match="max_executions"):
        B.budget_of(rec)

    rec["budget"]["max_executions"] = 501
    with pytest.raises(B.BudgetError, match="max_executions"):
        B.budget_of(rec)

    rec["budget"]["max_executions"] = 5
    rec["budget"]["max_retries"] = -1
    with pytest.raises(B.BudgetError, match="max_retries"):
        B.budget_of(rec)


def test_v1_v2_field_separation(auth_env):
    """v1 records declaring v2 fields are rejected fail closed."""
    priv, tmp = auth_env
    rec = signed_record(
        stage="S8-001",
        ops=("base_gold_production",),
        schema="f2-authorization/v1",
        candidate_manifest_digest="m" * 64,
        registration_sha256="r" * 64,
        budget={"max_executions": 1, "max_total_tasks": 1, "max_retries": 0},
    )
    p = write_record(tmp, rec, priv)
    with pytest.raises(AuthorizationError, match="v1 authorization record cannot declare 'budget'"):
        A.verify_authorization(p, stage="S8-001", operation="base_gold_production")


def test_candidate_pool_digest_mismatch(auth_env):
    """Caller supplying mismatching candidate_pool_digest is denied."""
    priv, tmp = auth_env
    rec = make_v2_record(candidate_pool_digest="a" * 64)
    p = write_record(tmp, rec, priv)
    with pytest.raises(AuthorizationError, match="candidate_pool_digest mismatch"):
        A.verify_authorization(
            p,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_manifest_digest": "m" * 64,
                "registration_sha256": "r" * 64,
                "candidate_pool_digest": "f" * 64,
                "registration_manifest_sha256": "b" * 64,
            },
        )


def test_registration_manifest_sha256_mismatch(auth_env):
    """Caller supplying mismatching registration_manifest_sha256 is denied."""
    priv, tmp = auth_env
    rec = make_v2_record(registration_manifest_sha256="b" * 64)
    p = write_record(tmp, rec, priv)
    with pytest.raises(AuthorizationError, match="registration_manifest_sha256 mismatch"):
        A.verify_authorization(
            p,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_manifest_digest": "m" * 64,
                "registration_sha256": "r" * 64,
                "candidate_pool_digest": "a" * 64,
                "registration_manifest_sha256": "c" * 64,
            },
        )


def test_verify_and_reserve_requires_matching_registration_manifest(auth_env):
    """verify_and_reserve enforces matching registration_manifest_sha256."""
    priv, tmp = auth_env
    rec = make_v2_record(registration_manifest_sha256="b" * 64)
    p = write_record(tmp, rec, priv)
    # Missing from binding
    with pytest.raises(AuthorizationError, match="no independently obtained expected value for required binding 'registration_manifest_sha256'"):
        A.verify_and_reserve(
            record_path=p,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_manifest_digest": "m" * 64,
                "registration_sha256": "r" * 64,
                "candidate_pool_digest": "a" * 64,
            },
            budget_ledger_root=tmp / "budget_root",
        )
    # Mismatch in binding
    with pytest.raises(AuthorizationError, match="registration_manifest_sha256 mismatch"):
        A.verify_and_reserve(
            record_path=p,
            stage="S8-001",
            operation="base_gold_production",
            binding={
                "candidate_manifest_digest": "m" * 64,
                "registration_sha256": "r" * 64,
                "candidate_pool_digest": "a" * 64,
                "registration_manifest_sha256": "f" * 64,
            },
            budget_ledger_root=tmp / "budget_root",
        )


# ===========================================================================
# 2. Budgets & Execution Identity
# ===========================================================================


def test_execution_identity_requirements(tmp_path):
    """Explicit execution_id is required when max_executions > 1."""
    rec = make_v2_record(executions=2)
    binding = {"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64}
    with pytest.raises(B.BudgetError, match="explicit execution_id is required"):
        B.reserve(
            record=rec,
            binding=binding,
            binding_digest="b" * 64,
            stage="S8-001",
            operation="base_gold_production",
            execution_id="",
            task_count=1,
            ledger_root=tmp_path,
        )

    # When max_executions == 1, grant ID is accepted as default execution_id
    rec1 = make_v2_record(executions=1, id="grant-single-exec")
    entry = B.reserve(
        record=rec1,
        binding=binding,
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="",
        task_count=1,
        ledger_root=tmp_path,
    )
    assert entry["execution_id"] == "grant-single-exec"


def test_duplicate_execution_id_rejected(tmp_path):
    rec = make_v2_record(executions=5)
    binding = {"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64}
    B.reserve(
        record=rec,
        binding=binding,
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="run-1",
        task_count=2,
        ledger_root=tmp_path,
    )
    with pytest.raises(B.BudgetError, match="duplicate execution_id 'run-1' already reserved"):
        B.reserve(
            record=rec,
            binding=binding,
            binding_digest="b" * 64,
            stage="S8-001",
            operation="base_gold_production",
            execution_id="run-1",
            task_count=2,
            ledger_root=tmp_path,
        )


def test_task_count_forwarding_and_attribution(tmp_path):
    rec = make_v2_record(executions=5, tasks=10)
    binding = {"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64}
    entry = B.reserve(
        record=rec,
        binding=binding,
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="run-batch",
        task_count=3,
        task_ids=["task-A", "task-B", "task-C"],
        manifest_digest="m" * 64,
        ledger_root=tmp_path,
    )
    assert entry["task_count"] == 3
    assert entry["task_ids"] == ["task-A", "task-B", "task-C"]
    report = B.classify_store(tmp_path / record_digest(rec), record_digest(rec))
    assert report["counts"]["tasks"] == 3
    assert len(report["tasks"]) == 3
    assert [t["task_id"] for t in report["tasks"]] == ["task-A", "task-B", "task-C"]


def test_verify_and_consume_rejects_v2(auth_env):
    """Direct consumption of v2 grants via verify_and_consume is closed."""
    priv, tmp = auth_env
    rec = make_v2_record()
    p = write_record(tmp, rec, priv)
    with pytest.raises(AuthorizationError, match="verify_and_consume does not support v2 authorizations"):
        A.verify_and_consume(
            record_path=p,
            stage="S8-001",
            operation="base_gold_production",
            binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
            ledger_root=tmp / "ledger",
        )


def test_orchestrator_rejects_v2_grant(auth_env, monkeypatch):
    """Orchestrator rejects v2 grant early before any execution."""
    from qwen_train.f2_arm_orchestrator import run_f2_arm
    priv, tmp = auth_env
    rec = signed_record(
        stage="CONF-001",
        ops=("confirmatory_run",),
        schema="f2-authorization/v2",
        budget={"max_executions": 1, "max_total_tasks": 1, "max_retries": 0},
        candidate_pool_digest="0" * 64,
        experiment_id="experiment_j",
        protocol_version="f2_v1",
        arm="X",
    )
    p = write_record(tmp, rec, priv)
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(p))
    with pytest.raises(AuthorizationError, match="f2_arm_orchestrator does not support v2 authorizations"):
        run_f2_arm(
            arm="X",
            stage="CONF-001",
            execute=True,
            manifest_dir=tmp / "manifests",
            worker_script=tmp / "w.py",
            repo_root=tmp,
            render_t=None,
            lesson_l_id="l",
            lesson_l_hash="h",
            task_id="t",
        )


# ===========================================================================
# 3. Atomicity & Concurrency
# ===========================================================================


def _concurrency_worker(args):
    rec, root_str, exec_id, task_count = args
    try:
        B.reserve(
            record=rec,
            binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
            binding_digest="b" * 64,
            stage="S8-001",
            operation="base_gold_production",
            execution_id=exec_id,
            task_count=task_count,
            ledger_root=Path(root_str),
        )
        return "win"
    except Exception:
        return "lose"


def test_two_competing_reservations_cannot_overspend(tmp_path):
    rec = make_v2_record(executions=3, tasks=100)
    args_list = [(rec, str(tmp_path), f"exec-{i}", 1) for i in range(10)]
    with cf.ProcessPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_concurrency_worker, args_list))
    assert results.count("win") == 3
    assert results.count("lose") == 7
    assert B.usage(record_digest(rec), tmp_path) == 3


def test_persistent_transaction_lock_file(tmp_path):
    rec = make_v2_record(executions=2)
    B.reserve(
        record=rec,
        binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="e0",
        task_count=1,
        ledger_root=tmp_path,
    )
    lock_file = tmp_path / record_digest(rec) / ".transaction.lock"
    assert lock_file.exists(), ".transaction.lock must persist after transaction"


# ===========================================================================
# 4. Accounting & Integrity (12 Cases)
# ===========================================================================


def test_case_1_healthy_committed_reservation(tmp_path):
    rec = make_v2_record(executions=2, tasks=5)
    B.reserve(
        record=rec,
        binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="e0",
        task_count=2,
        ledger_root=tmp_path,
    )
    report = B.classify_store(tmp_path / record_digest(rec), record_digest(rec))
    assert report["status"] == "HEALTHY"
    assert report["counts"]["slots"] == 1
    assert report["counts"]["tasks"] == 2


def test_case_2_incomplete_execution_reservation(tmp_path):
    rec = make_v2_record(executions=2)
    d = tmp_path / record_digest(rec)
    (d / "exec").mkdir(parents=True)
    payload = {
        "schema": B.RESERVATION_SCHEMA,
        "kind": "execution-claim",
        "execution_id": "interrupted-run",
        "task_count": 1,
        "claimed_at_utc": "2026-10-23T00:00:00Z",
        "pid": 1234,
    }
    (d / "exec" / (hashlib.sha256(b"interrupted-run").hexdigest() + ".lock")).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert "incomplete reservation" in report["reason"]


def test_case_3_provisional_task_count_exceeds_declared(tmp_path):
    rec = make_v2_record(executions=2, tasks=10)
    B.reserve(
        record=rec,
        binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="e0",
        task_count=1,
        ledger_root=tmp_path,
    )
    d = tmp_path / record_digest(rec)
    # Inject extra task for e0
    extra_task = {
        "schema": B.TASK_SCHEMA,
        "record_digest": record_digest(rec),
        "execution_id": "e0",
        "task_id": "task-extra",
        "task_index": 1,
        "seq": 1,
    }
    (d / "tasks" / "000001.json").write_text(json.dumps(extra_task), encoding="utf-8")
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert "task count (2) exceeds declared (1)" in report["reason"]


def test_case_4_inconsistent_task_id_duplicates(tmp_path):
    rec = make_v2_record(executions=2, tasks=10)
    binding = {"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64}
    with pytest.raises(B.BudgetError, match="duplicate task_id"):
        B.reserve(
            record=rec,
            binding=binding,
            binding_digest="b" * 64,
            stage="S8-001",
            operation="base_gold_production",
            execution_id="e0",
            task_count=2,
            task_ids=["duplicate-id", "duplicate-id"],
            ledger_root=tmp_path,
        )


def test_case_5_malformed_execution_lock(tmp_path):
    rec = make_v2_record()
    d = tmp_path / record_digest(rec)
    (d / "exec").mkdir(parents=True)
    (d / "exec" / "bad.lock").write_text("{ corrupt json", encoding="utf-8")
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "MALFORMED_LOCK"


def test_case_6_malformed_committed_slot(tmp_path):
    rec = make_v2_record()
    d = tmp_path / record_digest(rec)
    d.mkdir(parents=True)
    (d / "000000.json").write_text("{ corrupt json", encoding="utf-8")
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "MALFORMED_SLOT"


def test_case_7_orphan_task_records(tmp_path):
    rec = make_v2_record()
    d = tmp_path / record_digest(rec)
    (d / "tasks").mkdir(parents=True)
    orphan = {
        "schema": B.TASK_SCHEMA,
        "record_digest": record_digest(rec),
        "execution_id": "no-such-execution",
        "task_id": "orphan-1",
        "task_index": 0,
        "seq": 0,
    }
    (d / "tasks" / "000000.json").write_text(json.dumps(orphan), encoding="utf-8")
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert "orphan task record" in report["reason"]


def test_case_8_conflicting_duplicate_committed_slots(tmp_path):
    rec = make_v2_record(executions=5, tasks=10)
    B.reserve(
        record=rec,
        binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="e0",
        task_count=1,
        ledger_root=tmp_path,
    )
    d = tmp_path / record_digest(rec)
    # Duplicate slot 000000.json as 000001.json with same execution_id e0
    slot0 = json.loads((d / "000000.json").read_text("utf-8"))
    slot1 = dict(slot0, seq=1, task_count=2)
    (d / "000001.json").write_text(json.dumps(slot1), encoding="utf-8")

    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "UNRESOLVED"
    assert "conflicting duplicate committed slots" in report["reason"]

    # Operational remaining capacity reports sentinel zero
    rem = B.remaining(rec, ledger_root=tmp_path)
    assert rem == {"executions": 0, "tasks": 0, "retries": 0}


def test_case_9_retry_without_committed_execution(tmp_path):
    rec = make_v2_record(retries=5)
    with pytest.raises(B.BudgetError, match="retry references uncommitted execution_id"):
        B.claim_retry(
            record=rec,
            execution_id="nonexistent-exec",
            task_id="task-1",
            ledger_root=tmp_path,
        )


def test_case_10_orphan_retry_record(tmp_path):
    rec = make_v2_record(executions=2, tasks=5, retries=5)
    B.reserve(
        record=rec,
        binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="e0",
        task_count=1,
        task_ids=["task-valid"],
        ledger_root=tmp_path,
    )
    with pytest.raises(B.BudgetError, match="retry references unknown task 'task-bogus'"):
        B.claim_retry(
            record=rec,
            execution_id="e0",
            task_id="task-bogus",
            ledger_root=tmp_path,
        )


def test_case_11_clean_duplicate_retry_claim(tmp_path):
    rec = make_v2_record(executions=2, tasks=5, retries=2)
    B.reserve(
        record=rec,
        binding={"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64},
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id="e0",
        task_count=2,
        task_ids=["t1", "t2"],
        ledger_root=tmp_path,
    )
    slot1 = B.claim_retry(record=rec, execution_id="e0", task_id="t1", retry_attempt=1, ledger_root=tmp_path)
    assert slot1 == "000000.json"
    # Duplicate claim for same attempt fails with already claimed
    with pytest.raises(B.BudgetError, match="already claimed"):
        B.claim_retry(record=rec, execution_id="e0", task_id="t1", retry_attempt=1, ledger_root=tmp_path)

    # Prove that the duplicate did NOT consume retry capacity: attempt 2 for t1 succeeds as slot 000001
    slot2 = B.claim_retry(record=rec, execution_id="e0", task_id="t1", retry_attempt=2, ledger_root=tmp_path)
    assert slot2 == "000001.json"


def test_case_12_accounting_anomaly(tmp_path):
    rec = make_v2_record()
    d = tmp_path / record_digest(rec)
    d.mkdir(parents=True)
    (d / "unauthorized_dir").mkdir()
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "ANOMALY"
    assert "unexpected directory" in report["reason"]


# ===========================================================================
# 5. Interrupted-transaction tests (fault INJECTED inside the real reserve /
#    claim_retry code path, not hand-written fixtures)
# ===========================================================================


class _Interrupted(Exception):
    """Stands in for a process dying mid-transaction."""


def _fail_nth_allocate(monkeypatch, n: int):
    real = B._allocate
    state = {"calls": 0}

    def wrapper(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == n:
            raise _Interrupted(f"injected failure at _allocate call {n}")
        return real(*args, **kwargs)

    monkeypatch.setattr(B, "_allocate", wrapper)
    return state


_BIND = {"candidate_manifest_digest": "m" * 64, "registration_sha256": "r" * 64}


def _reserve_kw(rec, root, eid, **kw):
    return dict(
        record=rec,
        binding=_BIND,
        binding_digest="b" * 64,
        stage="S8-001",
        operation="base_gold_production",
        execution_id=eid,
        ledger_root=root,
        **kw,
    )


@pytest.mark.parametrize("fail_at", [1, 2, 3], ids=["first_task", "second_task", "slot_commit"])
def test_interrupted_reservation_is_incomplete_blocks_and_never_refunds(
    tmp_path, monkeypatch, fail_at
):
    """task_count=2 => _allocate calls are task0, task1, slot. Fail at each."""
    rec = make_v2_record(executions=3, tasks=10)
    d = tmp_path / record_digest(rec)
    with monkeypatch.context() as m:
        _fail_nth_allocate(m, fail_at)
        with pytest.raises(_Interrupted):
            B.reserve(**_reserve_kw(rec, tmp_path, "crash", task_count=2, task_ids=["a", "b"]))

    # The real transaction left a real half-state: claim lock present, NO slot.
    assert (d / "exec").is_dir() and len(list((d / "exec").glob("*.lock"))) == 1
    assert not [p for p in d.glob("0*.json")], "a committed slot exists after an interrupt"

    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert "incomplete reservation" in report["reason"]

    # Blocks every further allocation (different execution id, plenty of budget).
    with pytest.raises(B.BudgetError, match="incomplete reservation"):
        B.reserve(**_reserve_kw(rec, tmp_path, "fresh", task_count=1))
    with pytest.raises(B.BudgetError, match="RECONCILIATION_REQUIRED"):
        B.audit(record_digest(rec), tmp_path)
    # The lock was released by the failed transaction (no 30s timeout above) and
    # persists on disk.
    assert (d / ".transaction.lock").exists()
    # No automatic refund: the claim and any partial task records are still there.
    assert len(list((d / "exec").glob("*.lock"))) == 1


def test_interrupted_retry_claim_blocks_and_never_refunds(tmp_path, monkeypatch):
    rec = make_v2_record(executions=2, tasks=5, retries=3)
    B.reserve(**_reserve_kw(rec, tmp_path, "e0", task_count=1, task_ids=["t1"]))
    d = tmp_path / record_digest(rec)
    with monkeypatch.context() as m:
        _fail_nth_allocate(m, 1)  # the retry slot allocation
        with pytest.raises(_Interrupted):
            B.claim_retry(record=rec, execution_id="e0", task_id="t1", ledger_root=tmp_path)
    assert len(list((d / "retries" / "claims").glob("*.lock"))) == 1  # claim persisted
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "RECONCILIATION_REQUIRED"
    assert "incomplete retry claim" in report["reason"]
    # Free retry capacity must NOT appear: a different retry is refused too.
    with pytest.raises(B.BudgetError, match="RECONCILIATION_REQUIRED"):
        B.claim_retry(
            record=rec, execution_id="e0", task_id="t1", retry_attempt=2, ledger_root=tmp_path
        )
    # Same identity retried is refused as unresolved, never silently re-granted.
    with pytest.raises(B.BudgetError):
        B.claim_retry(record=rec, execution_id="e0", task_id="t1", ledger_root=tmp_path)


def test_committed_reservation_survives_missing_audit_artifacts(tmp_path):
    """No receipt/audit file is required: removing the (optional) audit dir cannot
    undo a committed reservation or reduce the charged usage."""
    rec = make_v2_record(executions=2, tasks=5)
    B.reserve(**_reserve_kw(rec, tmp_path, "e0", task_count=2, task_ids=["a", "b"]))
    d = tmp_path / record_digest(rec)
    audit_dir = d / "audit"
    assert not audit_dir.exists()  # nothing writes it; absence must be healthy
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "HEALTHY" and report["counts"]["slots"] == 1
    assert B.remaining(rec, tmp_path) == {"executions": 1, "tasks": 3, "retries": 10}


def test_malformed_task_record_cannot_make_capacity_appear(tmp_path):
    """Corrupting a committed task record must fail closed, not free task units."""
    rec = make_v2_record(executions=3, tasks=10)
    B.reserve(**_reserve_kw(rec, tmp_path, "e0", task_count=2, task_ids=["a", "b"]))
    d = tmp_path / record_digest(rec)
    (d / "tasks" / "000001.json").write_text("{ corrupt", encoding="utf-8")
    assert B.classify_store(d, record_digest(rec))["status"] == "MALFORMED_TASK"
    with pytest.raises(B.BudgetError):
        B.remaining(rec, tmp_path)
    with pytest.raises(B.BudgetError):
        B.reserve(**_reserve_kw(rec, tmp_path, "e1", task_count=1))


def test_deleted_task_record_is_inconsistent_not_free(tmp_path):
    rec = make_v2_record(executions=3, tasks=10)
    B.reserve(**_reserve_kw(rec, tmp_path, "e0", task_count=2, task_ids=["a", "b"]))
    d = tmp_path / record_digest(rec)
    (d / "tasks" / "000001.json").unlink()
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "INCONSISTENT"
    with pytest.raises(B.BudgetError, match="INCONSISTENT"):
        B.reserve(**_reserve_kw(rec, tmp_path, "e1", task_count=1))


def test_duplicate_task_index_and_duplicate_task_id_are_inconsistent(tmp_path):
    rec = make_v2_record(executions=3, tasks=10)
    B.reserve(**_reserve_kw(rec, tmp_path, "e0", task_count=2, task_ids=["a", "b"]))
    d = tmp_path / record_digest(rec)
    t1 = json.loads((d / "tasks" / "000001.json").read_text("utf-8"))
    # duplicate index
    (d / "tasks" / "000001.json").write_text(json.dumps(dict(t1, task_index=0)), encoding="utf-8")
    assert B.classify_store(d, record_digest(rec))["status"] == "INCONSISTENT"
    # restore index, duplicate id instead
    (d / "tasks" / "000001.json").write_text(
        json.dumps(dict(t1, task_id="a")), encoding="utf-8"
    )
    report = B.classify_store(d, record_digest(rec))
    assert report["status"] == "INCONSISTENT" and "duplicate task_id" in report["reason"]
    # out-of-range index
    (d / "tasks" / "000001.json").write_text(
        json.dumps(dict(t1, task_index=7)), encoding="utf-8"
    )
    assert B.classify_store(d, record_digest(rec))["status"] == "INCONSISTENT"


def test_retry_cap_and_task_membership_are_enforced_end_to_end(tmp_path):
    rec = make_v2_record(executions=2, tasks=5, retries=1)
    B.reserve(**_reserve_kw(rec, tmp_path, "e0", task_count=1, task_ids=["t1"]))
    B.reserve(**_reserve_kw(rec, tmp_path, "e1", task_count=1, task_ids=["t9"]))
    # task belongs to e1, not e0
    with pytest.raises(B.BudgetError, match="unknown task"):
        B.claim_retry(record=rec, execution_id="e0", task_id="t9", ledger_root=tmp_path)
    B.claim_retry(record=rec, execution_id="e0", task_id="t1", ledger_root=tmp_path)
    with pytest.raises(B.BudgetError, match="retry capacity exhausted"):
        B.claim_retry(record=rec, execution_id="e1", task_id="t9", ledger_root=tmp_path)
    # the refused over-cap attempt left no stray claim lock (store still healthy)
    assert B.classify_store(tmp_path / record_digest(rec), record_digest(rec))["status"] == "HEALTHY"
