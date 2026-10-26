"""S8 Stage A - Probe Authorization, Gate, Budget, and Cleanup Verification Suite.

The referenced STAGE_A_TEST_MATRIX.md was not found in the available repository
or inspected local records. The provenance of the T-01 through T-21 numbering is
NOT ESTABLISHED. The tests are retained as newly authored regression and safety
coverage; this statement does not assert that the missing matrix was their source.

All tests run completely offline using tmp_path, ephemeral Ed25519 keys,
and synthetic stores. No production keys, ledgers, or budget roots are touched.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
from typing import Any, Sequence

import pytest

from qwen_train import f2_authorization as A
from qwen_train import f2_authz_budget as B
from qwen_train.f2_s8_acquisition import PrivilegedIdentityError
from qwen_train.f2_s8_cleanup import (
    CleanupRefused,
    cleanup_failed_build,
    cleanup_probe_workspace,
)
from qwen_train.f2_s8_pilot_build import main as pilot_build_main
from qwen_train.f2_s8_probe_driver import (
    ProbeLimits,
    compute_ordered_probe_candidates,
    run_probe,
)
from qwen_train.f2_s8_v2_gate import (
    PROBE_OPERATION,
    V2GateError,
    authorize_s8_probe_run,
    check_probe_grant_still_valid,
)
from tests.f2_authz_testkit import (
    TEST_REPO_REVISION,
    apply_repo_identity,
    keypair,
    signed_record,
    write_record,
    write_trust_root,
)

PROBE_STAGE = "S8-001"
VALID_RETENTION_POLICY = "RETAIN_COMPATIBLE_DELETE_FAILED_AND_INCOMPATIBLE"


def _make_reg_and_compat(
    tmp_path: pathlib.Path,
    task_ids: Sequence[str] = ("acme__widget-1", "acme__gadget-2"),
    *,
    conflicts: Sequence[str] = (),
):
    reg_file = tmp_path / "registered_tasks.json"
    reg_file.write_text(json.dumps(list(task_ids)), encoding="utf-8")
    compat_file = tmp_path / "compat_report.json"
    compat_data = {
        "schema": "f2-plugin-compat/v1",
        "tasks": {
            tid: {"classification": "UNKNOWN"} for tid in task_ids
        },
        "resolved_by_source_priority": list(conflicts),
    }
    compat_file.write_text(json.dumps(compat_data), encoding="utf-8")
    return reg_file, compat_file


def _compute_digests(
    task_ids: Sequence[str],
    ordering_policy: str = "REGISTRATION_ORDER_UNBUILT",
    max_tasks: int = 10,
    *,
    conflicts: Sequence[str] = (),
):
    conflict_set = set(conflicts)
    return compute_ordered_probe_candidates(
        registration_order=task_ids,
        compat_classes={
            tid: "UNKNOWN" for tid in task_ids if tid not in conflict_set
        },
        ordering_policy=ordering_policy,
        max_tasks=max_tasks,
    )


def _setup_probe_env(
    tmp_path: pathlib.Path,
    monkeypatch,
    *,
    pool_digest: str | None = None,
    selection_digest: str | None = None,
    ordering_policy: str = "REGISTRATION_ORDER_UNBUILT",
    allowed_identities: tuple[str, ...] = ("f2_s8_builder", "testuser"),
    network_policy: str = "OFFLINE_WHEELHOUSE_ONLY",
    retention_policy: str = VALID_RETENTION_POLICY,
    awards_credits: bool = False,
    target_credited: int = 0,
    expires_delta: dt.timedelta = dt.timedelta(hours=2),
    stage: str = PROBE_STAGE,
    operations: tuple[str, ...] = (PROBE_OPERATION,),
    repo_revision: str = TEST_REPO_REVISION,
    dirty: bool = False,
    task_ids: Sequence[str] = ("acme__widget-1", "acme__gadget-2"),
    conflicts: Sequence[str] = (),
    **over: Any,
) -> dict[str, Any]:
    pathlib.Path(tmp_path).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "qwen_train.f2_s8_acquisition.effective_identity_is_admin", lambda: False
    )
    apply_repo_identity(monkeypatch, revision=repo_revision, dirty=dirty)
    priv, pub = keypair()
    trust_root = write_trust_root(tmp_path, {"owner": (priv, pub)})
    root_sha = A.trust_root_digest(trust_root)
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT", str(trust_root))
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", root_sha)

    reg_file, compat_file = _make_reg_and_compat(tmp_path, task_ids, conflicts=conflicts)
    file_sha256 = hashlib.sha256(reg_file.read_bytes()).hexdigest()
    content_digest = hashlib.sha256(
        ("semantic:" + ",".join(task_ids)).encode("utf-8")
    ).hexdigest()
    manifest_file = tmp_path / "registration_manifest.json"
    manifest_file.write_text(
        json.dumps(
            {
                "tasks_sha256": file_sha256,
                "registration_content_sha256": content_digest,
            }
        ),
        encoding="utf-8",
    )
    if pool_digest is None or selection_digest is None:
        computed_pool, _sel, computed_sel = _compute_digests(
            task_ids, ordering_policy=ordering_policy, conflicts=conflicts
        )
        pool_digest = pool_digest or computed_pool
        selection_digest = selection_digest or computed_sel

    t_now = dt.datetime.now(dt.timezone.utc)
    rec = signed_record(
        stage=stage,
        ops=operations,
        repository_revision=repo_revision,
        schema="f2-authorization/v2",
        target_credited=target_credited,
    )
    rec.update({
        "ordering_policy": ordering_policy,
        "allowed_identities": list(allowed_identities),
        "network_policy": network_policy,
        "retention_policy": retention_policy,
        "awards_credits": awards_credits,
        "probe_pool_digest": pool_digest,
        "probe_selection_digest": selection_digest,
        "registration_sha256": content_digest,
        "registration_manifest_sha256": over.get("registration_manifest_sha256", file_sha256),
        "not_before": (t_now - dt.timedelta(minutes=5)).isoformat(),
        "not_after": (t_now + expires_delta).isoformat(),
        "budget": {"max_executions": 5, "max_total_tasks": 100, "max_retries": 10},
    })
    rec.update(over)
    grant_file = write_record(tmp_path, rec, priv)
    monkeypatch.setenv("F2_AUTHORIZATION_RECORD", str(grant_file))

    expected_binding = {
        "repository_revision": repo_revision,
        "registration_sha256": content_digest,
        "registration_manifest_sha256": rec["registration_manifest_sha256"],
        "probe_pool_digest": pool_digest,
        "probe_selection_digest": selection_digest,
        "ordering_policy": ordering_policy,
        "allowed_identities": list(allowed_identities),
        "network_policy": network_policy,
        "retention_policy": retention_policy,
        "awards_credits": awards_credits,
    }

    return {
        "priv": priv,
        "trust_root": trust_root,
        "trust_root_sha256": root_sha,
        "grant_file": grant_file,
        "record": rec,
        "reg_file": reg_file,
        "manifest_file": manifest_file,
        "compat_file": compat_file,
        "task_ids": list(task_ids),
        "expected_binding": expected_binding,
    }


# ---------------------------------------------------------------------------
# T-01: test_unsigned_probe_grant_refused
# ---------------------------------------------------------------------------
def test_unsigned_probe_grant_refused(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    raw_grant = tmp_path / "unsigned.json"
    raw_grant.write_text(json.dumps({"record": env["record"]}), encoding="utf-8")
    with pytest.raises(A.AuthorizationError, match="signature"):
        A.verify_authorization(
            raw_grant,
            stage=PROBE_STAGE,
            operation=PROBE_OPERATION,
            binding=env["expected_binding"],
            trust_root=env["trust_root"],
        )


# ---------------------------------------------------------------------------
# T-02: test_tampered_probe_grant_refused
# ---------------------------------------------------------------------------
def test_tampered_probe_grant_refused(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    tampered_grant = tmp_path / "tampered.json"
    data = json.loads(env["grant_file"].read_text(encoding="utf-8"))
    data["record"]["budget"]["max_total_tasks"] = 999  # Tamper 1 byte
    tampered_grant.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(A.AuthorizationError, match="signature"):
        A.verify_authorization(
            tampered_grant,
            stage=PROBE_STAGE,
            operation=PROBE_OPERATION,
            binding=env["expected_binding"],
            trust_root=env["trust_root"],
        )


# ---------------------------------------------------------------------------
# T-03: test_expired_probe_grant_refused
# ---------------------------------------------------------------------------
def test_expired_probe_grant_refused(tmp_path, monkeypatch):
    env = _setup_probe_env(
        tmp_path,
        monkeypatch,
        expires_delta=-dt.timedelta(hours=1),  # Expired 1 hour ago
    )
    with pytest.raises(A.AuthorizationError, match="validity window"):
        A.verify_authorization(
            env["grant_file"],
            stage=PROBE_STAGE,
            operation=PROBE_OPERATION,
            binding=env["expected_binding"],
            trust_root=env["trust_root"],
        )


# ---------------------------------------------------------------------------
# T-04: test_revoked_probe_grant_refused_before_and_mid_run
# ---------------------------------------------------------------------------
def test_revoked_probe_grant_refused_before_and_mid_run(tmp_path, monkeypatch):
    broot = tmp_path / "probe_budget"
    env = _setup_probe_env(
        tmp_path,
        monkeypatch,
        allowed_identities=("testuser",),
    )

    # (a) Revocation before launch
    root_data = json.loads(env["trust_root"].read_text("utf-8"))
    root_data["revoked_record_ids"] = [env["record"]["id"]]
    env["trust_root"].write_text(json.dumps(root_data), "utf-8")
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(env["trust_root"]))

    with pytest.raises(A.AuthorizationError, match="is revoked via the trust root"):
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            compat_report_file=env["compat_file"],
            trust_root_path=env["trust_root"],
            budget_ledger_root=broot,
            current_username="testuser",
        )

    # (b) Mid-run revocation check
    root_data["revoked_record_ids"] = []
    env["trust_root"].write_text(json.dumps(root_data), "utf-8")
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(env["trust_root"]))

    broot2 = tmp_path / "probe_budget_midrun"
    auth_run = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        compat_report_file=env["compat_file"],
        trust_root_path=env["trust_root"],
        budget_ledger_root=broot2,
        current_username="testuser",
    )
    # Revoke in trust root after authorization
    root_data["revoked_record_ids"] = [env["record"]["id"]]
    env["trust_root"].write_text(json.dumps(root_data), "utf-8")
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", A.trust_root_digest(env["trust_root"]))

    with pytest.raises(V2GateError) as exc_info2:
        check_probe_grant_still_valid(auth_run, budget_ledger_root=broot2)
    assert exc_info2.value.code == "PROBE_REVOKED"


# ---------------------------------------------------------------------------
# T-05: test_wrong_operation_grant_refused_cross_isolation
# ---------------------------------------------------------------------------
def test_wrong_operation_grant_refused_cross_isolation(tmp_path, monkeypatch):
    # Probe grant cannot authorize scored base_gold_production
    probe_env = _setup_probe_env(tmp_path, monkeypatch)
    with pytest.raises(A.AuthorizationError, match="not in authorised scope"):
        A.verify_authorization(
            probe_env["grant_file"],
            stage=PROBE_STAGE,
            operation="base_gold_production",
            trust_root=probe_env["trust_root"],
        )

    # Scored grant cannot authorize build_probe
    scored_env = _setup_probe_env(
        tmp_path / "scored",
        monkeypatch,
        operations=("base_gold_production",),
        awards_credits=True,
        target_credited=300,
    )
    with pytest.raises(A.AuthorizationError, match="not in authorised scope|PROBE_CREDITS_FORBIDDEN"):
        A.verify_authorization(
            scored_env["grant_file"],
            stage=PROBE_STAGE,
            operation="build_probe",
            trust_root=scored_env["trust_root"],
        )


# ---------------------------------------------------------------------------
# T-06: test_wrong_revision_or_dirty_checkout_refused
# ---------------------------------------------------------------------------
def test_wrong_revision_or_dirty_checkout_refused(tmp_path, monkeypatch):
    # (a) Revision mismatch
    env = _setup_probe_env(
        tmp_path, monkeypatch, repo_revision="0" * 40
    )
    apply_repo_identity(monkeypatch, revision="f" * 40, dirty=False)
    with pytest.raises(A.AuthorizationError, match="repository revision mismatch"):
        A.verify_authorization(
            env["grant_file"],
            stage=PROBE_STAGE,
            operation=PROBE_OPERATION,
            binding=env["expected_binding"],
            trust_root=env["trust_root"],
        )

    # (b) Dirty checkout
    apply_repo_identity(monkeypatch, revision="0" * 40, dirty=True)
    with pytest.raises(A.AuthorizationError, match="dirty"):
        A.verify_authorization(
            env["grant_file"],
            stage=PROBE_STAGE,
            operation=PROBE_OPERATION,
            binding=env["expected_binding"],
            trust_root=env["trust_root"],
        )


# ---------------------------------------------------------------------------
# T-07: test_wrong_population_or_selection_digest_refused
# ---------------------------------------------------------------------------
def test_wrong_population_or_selection_digest_refused(tmp_path, monkeypatch):
    # (a) Mismatched pool digest
    env = _setup_probe_env(
        tmp_path / "wrong_pool",
        monkeypatch,
        pool_digest="c" * 64,
        allowed_identities=("testuser",),
    )
    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            compat_report_file=env["compat_file"],
            trust_root_path=env["trust_root"],
            budget_ledger_root=tmp_path / "broot1",
            current_username="testuser",
        )
    assert exc_info.value.code == "PROBE_POOL_DIGEST_MISMATCH"

    # (b) Mismatched selection digest
    env2 = _setup_probe_env(
        tmp_path / "wrong_sel",
        monkeypatch,
        selection_digest="d" * 64,
        allowed_identities=("testuser",),
    )
    with pytest.raises(V2GateError) as exc_info2:
        authorize_s8_probe_run(
            grant_path=env2["grant_file"],
            registration_file=env2["reg_file"],
            compat_report_file=env2["compat_file"],
            trust_root_path=env2["trust_root"],
            budget_ledger_root=tmp_path / "broot2",
            current_username="testuser",
        )
    assert exc_info2.value.code == "PROBE_SELECTION_DIGEST_MISMATCH"


# ---------------------------------------------------------------------------
# T-08: test_task_outside_unknown_set_or_selection_refused
# ---------------------------------------------------------------------------
def test_task_outside_unknown_set_or_selection_refused():
    limits = ProbeLimits(max_tasks=10, disk_floor_bytes=1, per_task_disk_bytes=1)
    calls = []

    def fake_builder(batch):
        calls.append(batch)
        return {tid: {"classification": "OBSERVED_COMPATIBLE"} for tid in batch}

    result = run_probe(
        unknown_task_ids=["u1", "u2"],
        limits=limits,
        allowed_task_ids=["u1"],  # u2 is outside selection
        verify_ledgers=lambda: None,
        authorize=lambda: None,
        builder=fake_builder,
        free_bytes=lambda: 10**12,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_TASK_OUTSIDE_SELECTION"
    assert calls == []


# ---------------------------------------------------------------------------
# T-09: test_already_terminal_or_built_task_excluded_by_selector
# ---------------------------------------------------------------------------
def test_already_terminal_or_built_task_excluded_by_selector():
    pool_digest, selected, selection_digest = compute_ordered_probe_candidates(
        registration_order=["acme__w-1", "acme__w-2", "acme__w-3"],
        compat_classes={
            "acme__w-1": "UNKNOWN",
            "acme__w-2": "UNKNOWN",
            "acme__w-3": "UNKNOWN",
        },
        ordering_policy="REGISTRATION_ORDER_UNBUILT",
        max_tasks=10,
        terminal_task_ids={"acme__w-1"},
        built_task_ids={"acme__w-2"},
    )
    assert selected == ["acme__w-3"]
    assert len(pool_digest) == 64
    assert len(selection_digest) == 64


# ---------------------------------------------------------------------------
# T-10: test_privileged_or_unauthorized_identity_refused
# ---------------------------------------------------------------------------
def test_privileged_or_unauthorized_identity_refused(tmp_path, monkeypatch):
    env = _setup_probe_env(
        tmp_path,
        monkeypatch,
        allowed_identities=("f2_s8_builder",),
    )
    # (a) Privileged identity
    monkeypatch.setattr(
        "qwen_train.f2_s8_acquisition.effective_identity_is_admin", lambda: True
    )
    with pytest.raises(PrivilegedIdentityError):
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            compat_report_file=env["compat_file"],
            trust_root_path=env["trust_root"],
            budget_ledger_root=tmp_path / "broot1",
            current_username="f2_s8_builder",
        )

    # (b) Identity mismatch
    monkeypatch.setattr(
        "qwen_train.f2_s8_acquisition.effective_identity_is_admin", lambda: False
    )
    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            compat_report_file=env["compat_file"],
            trust_root_path=env["trust_root"],
            budget_ledger_root=tmp_path / "broot2",
            current_username="unauthorized_user",
        )
    assert exc_info.value.code == "PROBE_IDENTITY_MISMATCH"


# ---------------------------------------------------------------------------
# T-11: test_batch_over_50_or_over_grant_batch_limit_refused
# ---------------------------------------------------------------------------
def test_batch_over_50_or_over_grant_batch_limit_refused():
    with pytest.raises(ValueError, match="batch_limit"):
        ProbeLimits(max_tasks=10, batch_limit=51)

    seen_batches = []

    def fake_builder(batch):
        seen_batches.append(len(batch))
        return {tid: {"classification": "UNKNOWN"} for tid in batch}

    limits = ProbeLimits(
        max_tasks=25, batch_limit=10, disk_floor_bytes=1, per_task_disk_bytes=1
    )
    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(25)],
        limits=limits,
        verify_ledgers=lambda: None,
        authorize=lambda: None,
        builder=fake_builder,
        free_bytes=lambda: 10**12,
        log=lambda _: None,
    )
    assert seen_batches == [10, 10, 5]
    assert result.attempted == 25


# ---------------------------------------------------------------------------
# T-12: test_disk_low_halts_before_batch_and_before_task
# ---------------------------------------------------------------------------
def test_disk_low_halts_before_batch_and_before_task():
    limits = ProbeLimits(
        max_tasks=10,
        batch_limit=5,
        disk_floor_bytes=25_000_000_000,
        per_task_disk_bytes=3_500_000_000,
    )
    calls = []

    def fake_builder(batch):
        calls.append(batch)
        return {}

    # (a) Halts before batch
    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(5)],
        limits=limits,
        authorize=lambda: None,
        builder=fake_builder,
        free_bytes=lambda: 20_000_000_000,  # Below 25 GB floor
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_DISK_LOW"
    assert calls == []


# ---------------------------------------------------------------------------
# T-13: test_high_infra_failure_rate_halts_probe
# ---------------------------------------------------------------------------
def test_high_infra_failure_rate_halts_probe():
    limits = ProbeLimits(max_tasks=10, disk_floor_bytes=1, per_task_disk_bytes=1)

    def builder_with_infra_errors(batch):
        return {
            tid: {
                "classification": "UNKNOWN",
                "failure": "infra_crash" if i < 2 else "",
            }
            for i, tid in enumerate(batch)
        }

    result = run_probe(
        unknown_task_ids=[f"u{i}" for i in range(10)],
        limits=limits,
        authorize=lambda: None,
        builder=builder_with_infra_errors,
        free_bytes=lambda: 10**12,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_INFRA_FAILURE_RATE"
    assert result.infrastructure_invalid == 2


# ---------------------------------------------------------------------------
# T-14: test_probe_cannot_award_credits_or_accept_nonzero_target
# ---------------------------------------------------------------------------
def test_probe_cannot_award_credits_or_accept_nonzero_target(tmp_path, monkeypatch):
    # (a) Probe with awards_credits = True rejected
    env_a = _setup_probe_env(tmp_path / "a", monkeypatch, awards_credits=True)
    with pytest.raises(A.AuthorizationError, match="awards_credits"):
        A.verify_authorization(
            env_a["grant_file"],
            stage=PROBE_STAGE,
            operation=PROBE_OPERATION,
            binding=env_a["expected_binding"],
            trust_root=env_a["trust_root"],
        )

    # (b) Probe with target_credited > 0 rejected
    env_b = _setup_probe_env(tmp_path / "b", monkeypatch, target_credited=300)
    with pytest.raises(A.AuthorizationError, match="target_credited"):
        A.verify_authorization(
            env_b["grant_file"],
            stage=PROBE_STAGE,
            operation=PROBE_OPERATION,
            binding=env_b["expected_binding"],
            trust_root=env_b["trust_root"],
        )

    # (c) Scored grant with awards_credits = False rejected
    env_c = _setup_probe_env(
        tmp_path / "c",
        monkeypatch,
        operations=("base_gold_production",),
        awards_credits=False,
        target_credited=300,
    )
    with pytest.raises(A.AuthorizationError, match="awards_credits"):
        A.verify_authorization(
            env_c["grant_file"],
            stage=PROBE_STAGE,
            operation="base_gold_production",
            trust_root=env_c["trust_root"],
        )


# ---------------------------------------------------------------------------
# T-15: test_probe_cannot_mutate_admission_ledger
# ---------------------------------------------------------------------------
def test_probe_cannot_mutate_admission_ledger(tmp_path):
    ledger_file = tmp_path / "s8_pilot_ledger.jsonl"
    ledger_file.write_text('{"entry": 1}\n', encoding="utf-8")
    limits = ProbeLimits(max_tasks=2, disk_floor_bytes=1, per_task_disk_bytes=1)

    def tampering_builder(batch):
        # Tamper ledger during probe execution
        ledger_file.write_text('{"entry": 1}\n{"entry": 2}\n', encoding="utf-8")
        return {tid: {"classification": "OBSERVED_COMPATIBLE"} for tid in batch}

    initial_bytes = ledger_file.read_bytes()

    def check_ledger():
        if ledger_file.read_bytes() != initial_bytes:
            raise RuntimeError("ledger mutated during probe")

    result = run_probe(
        unknown_task_ids=["u1", "u2"],
        limits=limits,
        verify_ledgers=check_ledger,
        authorize=lambda: None,
        builder=tampering_builder,
        free_bytes=lambda: 10**12,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_LEDGER_MUTATION_FORBIDDEN"


# ---------------------------------------------------------------------------
# T-16: test_probe_budget_isolated_from_production_scored_budget
# ---------------------------------------------------------------------------
def test_probe_budget_isolated_from_production_scored_budget(tmp_path):
    probe_root = B.budget_root(is_probe=True)
    scored_root = B.budget_root(is_probe=False)
    assert probe_root != scored_root
    assert probe_root == B.PRODUCTION_PROBE_BUDGET_ROOT
    assert scored_root == B.PRODUCTION_BUDGET_ROOT

    # Test budget reservation in probe store
    test_probe_root = tmp_path / "probe_budget"
    test_scored_root = tmp_path / "scored_budget"
    test_scored_root.mkdir()
    (test_scored_root / "ledger.jsonl").write_text("initial\n", encoding="utf-8")
    before_scored = (test_scored_root / "ledger.jsonl").read_bytes()

    probe_rec = {
        "id": "PROBE-GRANT-1",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["build_probe"],
        "not_before": "2000-01-01T00:00:00+00:00",
        "not_after": "2100-12-31T23:59:59+00:00",
        "target_credited": 0,
        "awards_credits": False,
        "budget": {"max_executions": 5, "max_total_tasks": 100, "max_retries": 10},
    }
    B.reserve(
        record=probe_rec,
        ledger_root=test_probe_root,
        binding={},
        binding_digest="0" * 64,
        stage="S8-001",
        operation="build_probe",
        execution_id="exec-1",
        task_count=1,
    )

    assert (test_scored_root / "ledger.jsonl").read_bytes() == before_scored
    assert (test_probe_root / B.record_digest(probe_rec)).is_dir()


# ---------------------------------------------------------------------------
# T-17: test_failed_build_cleanup_preserves_logs_and_removes_partial_ws
# ---------------------------------------------------------------------------
def test_failed_build_cleanup_preserves_logs_and_removes_partial_ws(tmp_path):
    ws_root = tmp_path / "ws"
    task_id = "acme__widget-1"
    ws = ws_root / task_id
    ws.mkdir(parents=True)
    (ws / "partial_file.txt").write_text("corrupted", encoding="utf-8")

    manifest = tmp_path / "deletion_manifest.jsonl"
    cleaned = cleanup_failed_build(
        task_id=task_id,
        workspace=ws,
        allowed_roots=[ws_root],
        failure_class="build",
        manifest_path=manifest,
        allow_delete=True,
    )
    assert cleaned.deleted is True
    assert not ws.exists()
    assert manifest.is_file()
    assert task_id in manifest.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T-18: test_incompatible_probe_workspace_cleanup_requires_verified_report
# ---------------------------------------------------------------------------
def test_incompatible_probe_workspace_cleanup_requires_verified_report(tmp_path):
    ws_root = tmp_path / "ws"
    task_id = "acme__gadget-2"
    ws = ws_root / task_id
    ws.mkdir(parents=True)
    (ws / "file.txt").write_text("content", encoding="utf-8")

    log_file = tmp_path / f"build_{task_id}.log"
    log_file.write_text("build log content", encoding="utf-8")

    report_file = tmp_path / "probe_report.json"
    report_file.write_text(
        json.dumps({
            task_id: {"classification": "OBSERVED_INCOMPATIBLE"},
            "acme__gizmo-3": {"classification": "OBSERVED_INCOMPATIBLE"},
        }),
        encoding="utf-8",
    )
    manifest = tmp_path / "deletion_manifest.jsonl"

    # (a) Success with verified report and log
    cleaned = cleanup_probe_workspace(
        task_id=task_id,
        workspace=ws,
        allowed_roots=[ws_root],
        probe_report_path=report_file,
        build_log_path=log_file,
        manifest_path=manifest,
        allow_delete=True,
    )
    assert cleaned.deleted is True
    assert not ws.exists()

    # (b) Refused if build log missing
    task_id3 = "acme__gizmo-3"
    ws3 = ws_root / task_id3
    ws3.mkdir(parents=True)
    with pytest.raises(CleanupRefused, match="build log missing or empty"):
        cleanup_probe_workspace(
            task_id=task_id3,
            workspace=ws3,
            allowed_roots=[ws_root],
            probe_report_path=report_file,
            build_log_path=tmp_path / "nonexistent.log",
            manifest_path=manifest,
            allow_delete=True,
        )


# ---------------------------------------------------------------------------
# T-19: test_deterministic_selection_order_across_policies
# ---------------------------------------------------------------------------
def test_deterministic_selection_order_across_policies():
    tasks = ["b__pkg-2", "a__pkg-1", "d__pkg-4", "c__pkg-3", "e__pkg-5"]
    compat = {t: "UNKNOWN" for t in tasks}
    p1, s1, sel1 = compute_ordered_probe_candidates(
        registration_order=tasks,
        compat_classes=compat,
        ordering_policy="REGISTRATION_ORDER_UNBUILT",
        max_tasks=3,
    )
    assert s1 == ["b__pkg-2", "a__pkg-1", "d__pkg-4"]

    p2, s2, sel2 = compute_ordered_probe_candidates(
        registration_order=tasks,
        compat_classes=compat,
        ordering_policy="ASCII_SORTED_UNBUILT",
        max_tasks=3,
    )
    assert s2 == ["a__pkg-1", "b__pkg-2", "c__pkg-3"]

    # Reproducibility
    p1_again, s1_again, sel1_again = compute_ordered_probe_candidates(
        registration_order=tasks,
        compat_classes=compat,
        ordering_policy="REGISTRATION_ORDER_UNBUILT",
        max_tasks=3,
    )
    assert (p1, s1, sel1) == (p1_again, s1_again, sel1_again)


# ---------------------------------------------------------------------------
# T-20: test_pilot_build_cli_refuses_without_probe_grant
# ---------------------------------------------------------------------------
def test_pilot_build_cli_refuses_without_probe_grant(monkeypatch):
    monkeypatch.delenv("F2_AUTHORIZATION_FILE", raising=False)
    monkeypatch.setattr(
        "qwen_train.f2_s8_pilot_build.require_unprivileged_identity", lambda _: None
    )
    rc = pilot_build_main(["acme__widget-1"])
    assert rc == 2


def test_direct_build_call_refuses_without_probe_grant(tmp_path, monkeypatch):
    from qwen_train import f2_s8_pilot_build as pb

    monkeypatch.delenv("F2_AUTHORIZATION_FILE", raising=False)
    monkeypatch.setattr(
        "qwen_train.f2_s8_pilot_build.require_unprivileged_identity", lambda _: None
    )
    monkeypatch.setattr(
        "qwen_train.f2_s8_pilot_build.require_ratified_pytest_admissibility", lambda: None
    )
    monkeypatch.setattr(
        "qwen_train.f2_s8_pilot_build.preserve_previous_workspace", lambda *a, **kw: None
    )
    monkeypatch.setattr("qwen_train.f2_s8_pilot_build.run", lambda *a, **kw: None)
    monkeypatch.setattr("shutil.copytree", lambda *a, **kw: None)

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "acme__widget-1.json").write_text(
        json.dumps({"base_commit": "a" * 40, "repo": "acme/widget"}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="BUILD_NOT_AUTHORIZED"):
        pb.build(
            "acme__widget-1",
            "https://example.invalid/acme/widget.git",
            inputs=inputs,
            patches=tmp_path,
            declared_python_minor="3.10",
        )

    # Calling with a tampered grant file also fails closed
    tampered_grant = tmp_path / "tampered_grant.json"
    tampered_grant.write_text(json.dumps({"id": "fake", "signature": "bad"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BUILD_NOT_AUTHORIZED"):
        pb.build(
            "acme__widget-1",
            "https://example.invalid/acme/widget.git",
            inputs=inputs,
            patches=tmp_path,
            declared_python_minor="3.10",
            grant_file=tampered_grant,
        )


# ---------------------------------------------------------------------------
# T-21: test_collect_only_blocks_outbound_network_socket
# ---------------------------------------------------------------------------
def test_collect_only_blocks_outbound_network_socket():
    import qwen_train._pytest_no_network as net

    net.install_network_blocker()
    try:
        # A hostname that merely LOOKS loopback must not be treated as loopback.
        assert net._is_loopback(("127.0.0.1", 80)) is True
        assert net._is_loopback(("127.attacker.example", 80)) is False
        assert net._is_loopback(("127.0.0.1.evil.example", 80)) is False
        assert net._is_loopback(("192.0.2.1", 80)) is False

        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_connect(None, ("192.0.2.1", 80))
        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_create_connection(("192.0.2.1", 53))
        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_getaddrinfo("example.com", 80)
        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_gethostbyname("example.com")
        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_gethostbyname_ex("example.com")
        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_gethostbyaddr("192.0.2.1")
        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_getfqdn("example.com")
        with pytest.raises(OSError, match="PROBE_COLLECTION_NETWORK_FORBIDDEN"):
            net._blocked_sendto(None, b"x", ("192.0.2.1", 53))
        # Loopback resolution remains available.
        assert net._blocked_getaddrinfo("localhost", 80)
    finally:
        net.uninstall_network_blocker()


# ---------------------------------------------------------------------------
# Adversarial Security Regression Tests (S1-S5 Remediations)
# ---------------------------------------------------------------------------

def test_probe_build_positive_and_selection_enforcement(tmp_path, monkeypatch):
    from qwen_train import f2_s8_pilot_build as pb

    monkeypatch.setenv("USERNAME", "testuser")
    env = _setup_probe_env(tmp_path, monkeypatch, task_ids=("task-A", "task-B"))
    monkeypatch.setenv("F2_REGISTRATION_FILE", str(env["reg_file"]))
    monkeypatch.setenv("F2_COMPAT_REPORT_FILE", str(env["compat_file"]))

    auth_run = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=tmp_path / "broot_positive",
        current_username="testuser",
    )
    assert "task-A" in auth_run.selected_task_ids
    assert "task-B" in auth_run.selected_task_ids

    # Authorized task succeeds
    rec1 = pb._verify_build_authorization(task_id="task-A", authorized_run=auth_run)
    assert rec1["id"] == auth_run.grant_id

    # Unauthorized task rejected with fail-closed RuntimeError
    with pytest.raises(RuntimeError, match="PROBE_TASK_OUTSIDE_SELECTION|not in authorized probe selection"):
        pb._verify_build_authorization(task_id="task-FORBIDDEN", authorized_run=auth_run)

    # Calling with grant file path directly enforces task selection
    rec2 = pb._verify_build_authorization(env["grant_file"], task_id="task-B")
    assert rec2["id"] == auth_run.grant_id

    with pytest.raises(RuntimeError, match="PROBE_TASK_OUTSIDE_SELECTION|not in authorized probe selection"):
        pb._verify_build_authorization(env["grant_file"], task_id="task-FORBIDDEN")


def test_monkeypatching_build_alone_fails_cli_main(tmp_path, monkeypatch, capsys):
    from qwen_train import f2_s8_pilot_build as pb

    monkeypatch.setattr(
        "qwen_train.f2_s8_acquisition.effective_identity_is_admin", lambda: False
    )
    monkeypatch.setattr(pb, "_PERMIT_UNAUTHORIZED_STUB_BUILD", False)
    monkeypatch.setattr(pb, "build", lambda *a, **kw: {"status": "built"})

    manifest = tmp_path / "build_manifest.json"
    code = pb.main(["built__task-1", "--manifest", str(manifest)])
    assert code == 2
    captured = capsys.readouterr()
    assert "BUILD_NOT_AUTHORIZED" in captured.out


def test_authorize_s8_probe_run_rejects_none_grant_path(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=None,
            registration_file=env["reg_file"],
            compat_report_file=env["compat_file"],
        )
    assert exc_info.value.code == "PROBE_NOT_AUTHORIZED"
    assert "grant_path is required" in str(exc_info.value)


def test_authorize_s8_probe_run_missing_registration_manifest_refused(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    missing_manifest = tmp_path / "absent_manifest.json"
    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=missing_manifest,
            compat_report_file=env["compat_file"],
        )
    assert exc_info.value.code == "MISSING_ARTIFACT"


def test_check_probe_grant_still_valid_pin_failure_fails_closed(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    auth_run = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=tmp_path / "broot_pin",
        current_username="testuser",
    )
    # Tamper the trust root pin hash
    monkeypatch.setenv("F2_AUTHORIZATION_TRUST_ROOT_SHA256", "0" * 64)
    with pytest.raises(V2GateError) as exc_info:
        check_probe_grant_still_valid(auth_run)
    assert exc_info.value.code == "PROBE_NOT_AUTHORIZED"
    assert "trust root verification failed" in str(exc_info.value)


def test_check_probe_grant_still_valid_corrupted_budget_fails_closed(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    broot = tmp_path / "probe_budget_corrupt"
    auth_run = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=broot,
        reserve_execution=True,
        current_username="testuser",
    )
    # Corrupt store file
    grant_dir = broot / auth_run.grant_digest
    assert grant_dir.is_dir()
    # Write corrupt JSON into a slot or lock file
    (grant_dir / "000001.json").write_text("CORRUPTED_JSON_NOT_VALID", encoding="utf-8")

    with pytest.raises(V2GateError) as exc_info:
        check_probe_grant_still_valid(auth_run, budget_ledger_root=broot)
    assert exc_info.value.code == "PROBE_NOT_AUTHORIZED"


def test_probe_driver_cli_live_refused_prerequisites(tmp_path, monkeypatch, capsys):
    import qwen_train.f2_s8_probe_driver as pdrv

    monkeypatch.setenv("USERNAME", "testuser")
    env = _setup_probe_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "qwen_train.f2_s8_acquisition.effective_identity_is_admin", lambda: False
    )

    code = pdrv.main([
        "--grant-file", str(env["grant_file"]),
        "--registration-file", str(env["reg_file"]),
        "--registration-manifest", str(env["manifest_file"]),
        "--compat-report", str(env["compat_file"]),
        "--budget-root", str(tmp_path / "broot_cli_live"),
        "--live",
    ])
    assert code == 1
    captured = capsys.readouterr()
    assert "PROBE_PREREQUISITES_MISSING" in captured.err
    # The refusal must happen BEFORE authorization/reservation: a blocked
    # --live invocation must not create or consume a probe-budget slot.
    assert not (tmp_path / "broot_cli_live").exists()


def test_probe_driver_cli_dry_run_completes_isolated(tmp_path, monkeypatch, capsys):
    """The real dry-run CLI path completes with temporary fixtures only."""
    import hashlib

    import qwen_train.f2_s8_probe_driver as pdrv
    from qwen_train import f2_authz_budget as budget

    monkeypatch.setenv("USERNAME", "testuser")
    env = _setup_probe_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "qwen_train.f2_s8_acquisition.effective_identity_is_admin", lambda: False
    )

    # Any accidental use of the production probe root becomes visible.
    sentinel_probe = tmp_path / "production_probe_budget_sentinel"
    monkeypatch.setattr(budget, "PRODUCTION_PROBE_BUDGET_ROOT", sentinel_probe)

    production_ledgers = [
        pathlib.Path(
            r"C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger.jsonl"
        ),
        pathlib.Path(
            r"C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger_corrected.jsonl"
        ),
    ]
    before = {
        p: (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
        for p in production_ledgers
    }

    ledger = tmp_path / "probe_ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    budget_root = tmp_path / "probe_budget"
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    code = pdrv.main([
        "--grant-file", str(env["grant_file"]),
        "--registration-file", str(env["reg_file"]),
        "--registration-manifest", str(env["manifest_file"]),
        "--compat-report", str(env["compat_file"]),
        "--ledgers", str(ledger),
        "--budget-root", str(budget_root),
        "--workspace-root", str(workspace_root),
        "--output-dir", str(output_dir),
        "--dry-run",
    ])
    captured = capsys.readouterr()
    assert code == 0, (captured.out + captured.err)
    assert "PROBE_COMPLETE" in captured.out
    # No reservation and no production budget use.
    assert not budget_root.exists()
    assert not sentinel_probe.exists()
    # Production admission ledgers are byte-identical.
    after = {
        p: (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
        for p in production_ledgers
    }
    assert after == before


def test_collect_only_subprocess_network_isolation(tmp_path, monkeypatch):
    """The REAL collection command must load the plugin and block a connect.

    This runs ``pytest --collect-only`` (not a plain script) with the same
    ``-p _pytest_no_network`` + PYTHONPATH wiring the precheck uses.  The child
    test attempts an outbound connection at module import time, i.e. during
    collection.  ``192.0.2.1`` is TEST-NET-1 and is never routed, so a failure
    of the blocker cannot contact a real host.
    """
    import os
    import subprocess
    import sys
    from tests.conftest import _REAL_POPEN

    monkeypatch.setattr(subprocess, "Popen", _REAL_POPEN)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    qwen_train_dir = str(repo_root / "qwen_train")
    env = dict(os.environ)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{qwen_train_dir}{os.pathsep}{existing_pp}" if existing_pp else qwen_train_dir
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    script = tmp_path / "test_probe_collection_net.py"
    script.write_text(
        "import socket\n"
        "socket.create_connection(('192.0.2.1', 80), timeout=1)\n"
        "def test_placeholder():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "-p", "no:cacheprovider", "-p", "_pytest_no_network", "--no-header",
            str(script),
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0, out
    assert "PROBE_COLLECTION_NETWORK_FORBIDDEN" in out, out


# ---------------------------------------------------------------------------
# S5: the production cleanup call site must use the keyword-only API
# ---------------------------------------------------------------------------
def test_cleanup_probe_task_uses_keyword_api_and_classification(tmp_path):
    from qwen_train.f2_s8_probe_driver import cleanup_probe_task

    root = tmp_path / "ws"
    task = "acme__widget-1"
    ws = root / task
    ws.mkdir(parents=True)
    (ws / "partial.txt").write_text("x", encoding="utf-8")
    manifest = tmp_path / "cleanup_manifest.jsonl"

    # A build failure takes cleanup_failed_build (dry-run: no deletion).
    cleanup_probe_task(
        task,
        {"classification": "BUILD_FAILURE"},
        workspace_root=root,
        output_dir=tmp_path,
        manifest_path=manifest,
        allow_delete=False,
    )
    assert ws.is_dir()
    assert not manifest.exists()

    # An incompatible workspace with verified report + log takes the probe path.
    output = tmp_path / "out"
    output.mkdir()
    (output / "probe_report.json").write_text(
        json.dumps({task: {"classification": "OBSERVED_INCOMPATIBLE"}}), encoding="utf-8"
    )
    (output / f"{task}.build.log").write_text("build log", encoding="utf-8")
    cleanup_probe_task(
        task,
        {"classification": "OBSERVED_INCOMPATIBLE"},
        workspace_root=root,
        output_dir=output,
        manifest_path=manifest,
        allow_delete=False,
    )
    assert ws.is_dir()

    # Without the verified report, the probe path refuses (not TypeError).
    with pytest.raises(CleanupRefused):
        cleanup_probe_task(
            task,
            {"classification": "OBSERVED_INCOMPATIBLE"},
            workspace_root=root,
            output_dir=tmp_path / "empty_out",
            manifest_path=manifest,
            allow_delete=False,
        )

    # A compatible workspace is retained without a cleanup call.
    cleanup_probe_task(
        task,
        {"classification": "OBSERVED_COMPATIBLE"},
        workspace_root=root,
        output_dir=output,
        manifest_path=manifest,
        allow_delete=False,
    )
    assert ws.is_dir()


# ---------------------------------------------------------------------------
# S1: production bindings are recomputed and the task is manifest-scoped
# ---------------------------------------------------------------------------
def test_production_build_binding_is_independent_and_task_scoped(tmp_path, monkeypatch):
    from qwen_train import f2_s8_pilot_build as pb

    manifest = tmp_path / "build_manifest.json"
    manifest.write_text(json.dumps({"task-A": {"status": "built"}}), encoding="utf-8")
    registration = tmp_path / "registered_tasks.json"
    registration.write_text(json.dumps(["task-A", "task-B"]), encoding="utf-8")
    provenance = tmp_path / "registration_manifest.json"
    provenance.write_text(
        json.dumps({"registration_content_sha256": "a" * 64}), encoding="utf-8"
    )
    grant = tmp_path / "grant.json"
    grant.write_text(
        json.dumps({"record": {"operations": ["base_gold_production"]}}), encoding="utf-8"
    )

    captured: dict[str, Any] = {}

    def fake_verify(path, *, stage, operation, binding=None, **kwargs):
        captured.update(binding or {})
        return {"id": "g", "stage": stage, "operations": ["base_gold_production"]}

    # The verifier is replaced only to capture the binding this module derives;
    # signature verification itself is covered by the authorization suites.
    monkeypatch.setattr("qwen_train.f2_authorization.verify_authorization", fake_verify)

    out = pb._verify_build_authorization(
        grant,
        task_id="task-A",
        manifest_file=manifest,
        registration_file=registration,
        registration_manifest_file=provenance,
    )
    assert out["id"] == "g"
    assert captured["candidate_manifest_digest"] == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    assert captured["registration_manifest_sha256"] == hashlib.sha256(
        registration.read_bytes()
    ).hexdigest()
    assert captured["registration_sha256"] == "a" * 64

    with pytest.raises(RuntimeError, match="not in the signed build manifest"):
        pb._verify_build_authorization(
            grant,
            task_id="task-B",
            manifest_file=manifest,
            registration_file=registration,
            registration_manifest_file=provenance,
        )

    with pytest.raises(RuntimeError, match="build manifest missing"):
        pb._verify_build_authorization(
            grant,
            task_id="task-A",
            manifest_file=tmp_path / "absent.json",
            registration_file=registration,
            registration_manifest_file=provenance,
        )


# ---------------------------------------------------------------------------
# Test isolation: an explicit probe budget root must be used, and the
# production root must not be touched.
# ---------------------------------------------------------------------------
def test_explicit_probe_budget_root_is_used_not_production(tmp_path, monkeypatch):
    from qwen_train import f2_authz_budget as budget

    sentinel = tmp_path / "production_probe_root"
    monkeypatch.setattr(budget, "PRODUCTION_PROBE_BUDGET_ROOT", sentinel)
    env = _setup_probe_env(tmp_path, monkeypatch)
    explicit = tmp_path / "explicit_probe_budget"

    auth_run = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=explicit,
        reserve_execution=True,
        current_username="testuser",
    )
    assert (explicit / auth_run.grant_digest).is_dir()
    assert not sentinel.exists()


def test_authorize_default_does_not_reserve_budget(tmp_path, monkeypatch):
    """Omitting reserve_execution is verification-only and must not reserve."""
    env = _setup_probe_env(tmp_path, monkeypatch)
    root = tmp_path / "broot_default_no_reserve"

    auth = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=root,
        current_username="testuser",
    )
    assert not root.exists()
    assert not (root / auth.grant_digest).exists()


def test_authorize_explicit_reservation_reserves_in_isolated_root(tmp_path, monkeypatch):
    """Explicit reservation preserves the execution-path behavior."""
    env = _setup_probe_env(tmp_path, monkeypatch)
    root = tmp_path / "broot_explicit_reserve"

    auth = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=root,
        reserve_execution=True,
        current_username="testuser",
    )
    grant_dir = root / auth.grant_digest
    assert grant_dir.is_dir()
    assert len(sorted(grant_dir.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# S2 hardening: unreadable registration ordering and corrupt ledgers fail closed
# ---------------------------------------------------------------------------
def test_authorize_probe_rejects_unreadable_registration_order(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    env["reg_file"].write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    manifest = json.loads(env["manifest_file"].read_text(encoding="utf-8"))
    manifest["tasks_sha256"] = hashlib.sha256(
        env["reg_file"].read_bytes()
    ).hexdigest()
    env["manifest_file"].write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=env["compat_file"],
            budget_ledger_root=tmp_path / "broot_badreg",
            current_username="testuser",
        )
    assert exc_info.value.code == "REGISTRATION_UNREADABLE"


def test_authorize_probe_rejects_corrupt_admission_ledger(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    bad = tmp_path / "bad_ledger.jsonl"
    bad.write_text(
        json.dumps(
            {
                "seq": 0,
                "prev_digest": "0" * 64,
                "record_digest": "0" * 64,
                "task_id": "some-task",
                "outcome_class": "scientific",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=env["compat_file"],
            ledger_paths=[bad],
            budget_ledger_root=tmp_path / "broot_badledger",
            current_username="testuser",
        )
    assert exc_info.value.code == "PROBE_NOT_AUTHORIZED"
    assert "failed verification" in str(exc_info.value)


def test_probe_gate_excludes_source_priority_conflicts(tmp_path, monkeypatch):
    env = _setup_probe_env(
        tmp_path,
        monkeypatch,
        task_ids=("acme__widget-1", "acme__gadget-2"),
        conflicts=("acme__widget-1",),
    )
    auth = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=tmp_path / "broot_conflicts",
        current_username="testuser",
    )
    assert "acme__widget-1" not in auth.selected_task_ids
    assert "acme__gadget-2" in auth.selected_task_ids


def test_probe_grant_binds_target_compatible(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch, target_compatible=1)
    auth = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        budget_ledger_root=tmp_path / "broot_target",
        current_username="testuser",
    )
    assert auth.target_compatible == 1


@pytest.mark.parametrize("value", [-1, True, "1", 101])
def test_probe_grant_rejects_invalid_target_compatible(tmp_path, monkeypatch, value):
    env = _setup_probe_env(tmp_path, monkeypatch, target_compatible=value)
    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=env["compat_file"],
            budget_ledger_root=tmp_path / "broot_bad_target",
            current_username="testuser",
        )
    assert exc_info.value.code == "PROBE_NOT_AUTHORIZED"


def test_probe_grant_record_must_be_a_json_object(tmp_path, monkeypatch):
    env = _setup_probe_env(tmp_path, monkeypatch)
    env["grant_file"].write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(V2GateError) as exc_info:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=env["compat_file"],
            budget_ledger_root=tmp_path / "broot_non_object_grant",
            current_username="testuser",
        )
    assert exc_info.value.code == "INVALID_GRANT"


# ---------------------------------------------------------------------------
# S3: mid-run revalidation re-derives authorization from authoritative disk
# ---------------------------------------------------------------------------
def _authorized_probe(tmp_path, monkeypatch, *, name="broot", ledger_paths=()):
    env = _setup_probe_env(tmp_path, monkeypatch)
    auth = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        ledger_paths=ledger_paths,
        budget_ledger_root=tmp_path / name,
        reserve_execution=True,
        current_username="testuser",
    )
    return env, auth


def test_revalidation_accepts_unchanged_authoritative_state(tmp_path, monkeypatch):
    env, auth = _authorized_probe(tmp_path, monkeypatch)
    assert (
        check_probe_grant_still_valid(
            auth, budget_ledger_root=tmp_path / "broot"
        )
        is True
    )


def test_revalidation_rejects_changed_grant_file(tmp_path, monkeypatch):
    env, auth = _authorized_probe(tmp_path, monkeypatch)
    env["grant_file"].write_text(
        json.dumps({"record": {"id": "tampered"}, "signature": {"sig": "bad"}}),
        encoding="utf-8",
    )
    with pytest.raises(V2GateError):
        check_probe_grant_still_valid(auth, budget_ledger_root=tmp_path / "broot")


def test_revalidation_rejects_changed_registration_bytes(tmp_path, monkeypatch):
    env, auth = _authorized_probe(tmp_path, monkeypatch)
    env["reg_file"].write_bytes(env["reg_file"].read_bytes() + b"\n")
    with pytest.raises(V2GateError):
        check_probe_grant_still_valid(auth, budget_ledger_root=tmp_path / "broot")


def test_revalidation_rejects_changed_registration_manifest(tmp_path, monkeypatch):
    env, auth = _authorized_probe(tmp_path, monkeypatch)
    manifest = json.loads(env["manifest_file"].read_text(encoding="utf-8"))
    manifest["registration_content_sha256"] = "0" * 64
    env["manifest_file"].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(V2GateError):
        check_probe_grant_still_valid(auth, budget_ledger_root=tmp_path / "broot")


def test_revalidation_rejects_changed_compat_report(tmp_path, monkeypatch):
    env, auth = _authorized_probe(tmp_path, monkeypatch)
    data = json.loads(env["compat_file"].read_text(encoding="utf-8"))
    data["tasks"]["acme__widget-1"]["classification"] = "OBSERVED_COMPATIBLE"
    env["compat_file"].write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(V2GateError):
        check_probe_grant_still_valid(auth, budget_ledger_root=tmp_path / "broot")


def test_revalidation_rejects_repository_revision_drift(tmp_path, monkeypatch):
    _env, auth = _authorized_probe(tmp_path, monkeypatch)
    apply_repo_identity(monkeypatch, revision="f" * 40)
    with pytest.raises(V2GateError):
        check_probe_grant_still_valid(auth, budget_ledger_root=tmp_path / "broot")


def test_revalidation_rejects_dirty_tree(tmp_path, monkeypatch):
    _env, auth = _authorized_probe(tmp_path, monkeypatch)
    apply_repo_identity(monkeypatch, revision=TEST_REPO_REVISION, dirty=True)
    with pytest.raises(V2GateError):
        check_probe_grant_still_valid(auth, budget_ledger_root=tmp_path / "broot")


def test_revalidation_rejects_expired_grant(tmp_path, monkeypatch):
    _env, auth = _authorized_probe(tmp_path, monkeypatch)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)
    with pytest.raises(V2GateError) as exc_info:
        check_probe_grant_still_valid(
            auth, now=later, budget_ledger_root=tmp_path / "broot"
        )
    assert exc_info.value.code == "EXPIRED"


def test_revalidation_rejects_mutated_admission_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("", encoding="utf-8")
    _env, auth = _authorized_probe(
        tmp_path, monkeypatch, ledger_paths=[ledger]
    )
    ledger.write_text("{}\n", encoding="utf-8")
    with pytest.raises(V2GateError) as exc_info:
        check_probe_grant_still_valid(auth, budget_ledger_root=tmp_path / "broot")
    assert exc_info.value.code == "PROBE_LEDGER_MUTATION_FORBIDDEN"


def test_revalidation_does_not_reserve_execution_budget(tmp_path, monkeypatch):
    env, auth = _authorized_probe(tmp_path, monkeypatch)
    root = tmp_path / "broot"
    slot_dir = root / auth.grant_digest
    before = sorted(p.name for p in slot_dir.glob("*.json"))
    check_probe_grant_still_valid(auth, budget_ledger_root=root)
    after = sorted(p.name for p in slot_dir.glob("*.json"))
    assert after == before


def test_revalidation_requires_retained_authoritative_paths(tmp_path, monkeypatch):
    from dataclasses import replace

    _env, auth = _authorized_probe(tmp_path, monkeypatch)
    broken = replace(auth, grant_path="")
    with pytest.raises(V2GateError, match="retained authoritative"):
        check_probe_grant_still_valid(broken, budget_ledger_root=tmp_path / "broot")


def test_failed_mid_run_revalidation_blocks_execution():
    calls = {"n": 0}
    batches = []

    def authorize():
        calls["n"] += 1
        if calls["n"] > 2:
            raise V2GateError("PROBE_REVOKED", "revoked before batch 2")

    def builder(batch):
        batches.append(list(batch))
        return {tid: {"classification": "UNKNOWN"} for tid in batch}

    result = run_probe(
        unknown_task_ids=["u1", "u2"],
        limits=ProbeLimits(
            max_tasks=10,
            disk_floor_bytes=1,
            per_task_disk_bytes=1,
            batch_limit=1,
        ),
        authorize=authorize,
        builder=builder,
        free_bytes=lambda: 10**18,
        verify_ledgers=lambda: None,
        log=lambda _: None,
    )
    assert result.stop_reason == "PROBE_REVOKED"
    assert batches == [["u1"]]


# ---------------------------------------------------------------------------
# S8 Final Push: EXCLUDE-185 non-bypassability & target_compatible drift
# ---------------------------------------------------------------------------
def _write_exclusion_policy(tmp_path: pathlib.Path, excluded_ids: Sequence[str]) -> pathlib.Path:
    sorted_ids = sorted(str(x) for x in excluded_ids)
    canon_ids = json.dumps(sorted_ids, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    policy = {
        "schema": "f2-s8-exclusion-policy/v1",
        "policy_id": "EXCLUDE-185",
        "count": len(sorted_ids),
        "excluded_task_ids": sorted_ids,
        "excluded_task_ids_sha256": hashlib.sha256(canon_ids).hexdigest(),
    }
    p = tmp_path / "exclude_185.json"
    p.write_text(json.dumps(policy, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return p


def test_exclude_185_cannot_be_bypassed_across_candidate_paths(tmp_path, monkeypatch):
    from qwen_train.f2_s8_candidate_pool import (
        PoolError,
        build_pool,
        canonical_bytes,
        load_exclusion_policy,
    )
    from qwen_train.f2_s8_driver import plugin_classes, select_batch
    from qwen_train.f2_s8_v2_gate import v2_preflight

    conflict_task = "acme__widget-1"
    good_task = "acme__gadget-2"
    policy_path = _write_exclusion_policy(tmp_path, [conflict_task])
    _pol, forbidden_ids, _sha = load_exclusion_policy(policy_path)
    assert forbidden_ids == {conflict_task}

    # 1. build_pool refuses forbidden_task_ids in task_ids
    with pytest.raises(PoolError, match="forbidden/excluded"):
        build_pool(
            task_ids=[conflict_task, good_task],
            registration_sha256="a" * 64,
            forbidden_task_ids=forbidden_ids,
        )

    # 2. plugin_classes strips resolved_by_source_priority even if marked OBSERVED_COMPATIBLE
    report = {
        "results": [
            {"task_id": conflict_task, "classification": "OBSERVED_COMPATIBLE"},
            {"task_id": good_task, "classification": "OBSERVED_COMPATIBLE"},
        ],
        "resolved_by_source_priority": [conflict_task],
    }
    cls_map = plugin_classes(report)
    assert conflict_task not in cls_map
    assert cls_map[good_task] == "OBSERVED_COMPATIBLE"

    # 3. select_batch refuses excluded_tasks even if a caller passes a custom class map & built workspace
    ws = tmp_path / "ws"
    ws.mkdir()
    selected = select_batch(
        [conflict_task, good_task],
        {conflict_task: "OBSERVED_COMPATIBLE", good_task: "OBSERVED_COMPATIBLE"},
        {
            conflict_task: {"status": "built", "workspace": str(ws)},
            good_task: {"status": "built", "workspace": str(ws)},
        },
        excluded_tasks=forbidden_ids,
    )
    assert selected == [good_task]

    # 4. authorize_s8_probe_run refuses when compat report conflicts disagree with exclusion_policy_file
    env = _setup_probe_env(
        tmp_path,
        monkeypatch,
        task_ids=(conflict_task, good_task),
        conflicts=("some_other_task-1",),
    )
    with pytest.raises(V2GateError, match="disagrees with exclusion policy"):
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=env["compat_file"],
            exclusion_policy_file=policy_path,
            budget_ledger_root=tmp_path / "broot_excl_mismatch",
            current_username="testuser",
        )

    # 5. v2_preflight refuses a pool that includes a task from exclusion_policy_file
    reg_bytes = json.dumps([conflict_task, good_task]).encode("utf-8")
    reg_file = tmp_path / "reg_v2.json"
    reg_file.write_bytes(reg_bytes)
    reg_file_sha = hashlib.sha256(reg_bytes).hexdigest()
    reg_content_sha = "b" * 64
    prov_file = tmp_path / "reg_v2.provenance.json"
    prov_file.write_text(
        json.dumps(
            {
                "tasks_sha256": reg_file_sha,
                "registration_content_sha256": reg_content_sha,
            }
        ),
        encoding="utf-8",
    )
    bad_pool = build_pool(
        task_ids=[conflict_task, good_task],
        registration_sha256=reg_content_sha,
        provenance={"registration_file_sha256": reg_file_sha},
    )
    bad_pool_file = tmp_path / "bad_pool.json"
    bad_pool_bytes = canonical_bytes(bad_pool)
    bad_pool_file.write_bytes(bad_pool_bytes)
    bad_pool_sha = hashlib.sha256(bad_pool_bytes).hexdigest()

    t_now = dt.datetime.now(dt.timezone.utc)
    prod_rec = {
        "id": "grant-prod-1",
        "schema": "f2-authorization/v2",
        "stage": "S8-001",
        "operations": ["base_gold_production"],
        "not_before": (t_now - dt.timedelta(minutes=5)).isoformat(),
        "not_after": (t_now + dt.timedelta(hours=2)).isoformat(),
        "registration_sha256": reg_content_sha,
        "candidate_pool_digest": bad_pool_sha,
        "target_credited": 300,
        "budget": {"max_executions": 5, "max_total_tasks": 50, "max_retries": 5},
    }
    with pytest.raises(V2GateError) as exc_info:
        v2_preflight(
            record=prod_rec,
            registration_file=reg_file,
            candidate_pool_file=bad_pool_file,
            manifest_task_ids=[good_task],
            exclusion_policy_file=policy_path,
        )
    assert exc_info.value.code == "EXCLUDED_TASK"


    # 6. authorize_s8_probe_run refuses if a configured ledger path is missing
    with pytest.raises(V2GateError) as exc_missing_ledger:
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=env["compat_file"],
            ledger_paths=[tmp_path / "nonexistent_ledger.jsonl"],
            budget_ledger_root=tmp_path / "broot_missing_ledger",
            current_username="testuser",
        )
    assert exc_missing_ledger.value.code == "MISSING_ARTIFACT"


def test_revalidation_rejects_target_compatible_drift(tmp_path, monkeypatch):
    from dataclasses import replace

    _env, auth = _authorized_probe(tmp_path, monkeypatch)
    mutated = replace(auth, target_compatible=auth.target_compatible + 1)
    with pytest.raises(V2GateError, match="target_compatible"):
        check_probe_grant_still_valid(mutated, budget_ledger_root=tmp_path / "broot")


def test_compat_report_missing_schema_refused(tmp_path, monkeypatch):
    """Compatibility reports lacking explicit schema or rule_version are refused."""
    env = _setup_probe_env(tmp_path, monkeypatch)
    bad_compat = tmp_path / "no_schema_compat.json"
    bad_compat.write_text(
        json.dumps({
            "tasks": {tid: {"classification": "UNKNOWN"} for tid in env["task_ids"]}
        }),
        encoding="utf-8",
    )
    with pytest.raises(V2GateError, match="unsupported or missing compat report schema"):
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=bad_compat,
            budget_ledger_root=tmp_path / "broot_no_schema",
            current_username="testuser",
        )


def test_compat_report_conflicting_grouping_refused(tmp_path, monkeypatch):
    """Compatibility reports with contradictory tasks and results are refused."""
    env = _setup_probe_env(tmp_path, monkeypatch)
    conflicting_compat = tmp_path / "conflicting_compat.json"
    conflicting_compat.write_text(
        json.dumps({
            "schema": "f2-plugin-compat/v1",
            "tasks": {
                env["task_ids"][0]: {"classification": "OBSERVED_COMPATIBLE"},
            },
            "results": [
                {"task_id": env["task_ids"][0], "classification": "OBSERVED_INCOMPATIBLE"},
            ],
        }),
        encoding="utf-8",
    )
    with pytest.raises(V2GateError, match="conflicting classifications between tasks and results"):
        authorize_s8_probe_run(
            grant_path=env["grant_file"],
            registration_file=env["reg_file"],
            registration_manifest_file=env["manifest_file"],
            compat_report_file=conflicting_compat,
            budget_ledger_root=tmp_path / "broot_conflict_compat",
            current_username="testuser",
        )


def test_revalidation_rejects_exclusion_policy_drift(tmp_path, monkeypatch):
    """Revalidation catches mid-run tampering or modification of exclusion policy."""
    policy_path = tmp_path / "test_exclude.json"
    policy_data = {
        "schema": "f2-s8-exclusion-policy/v1",
        "description": "test",
        "count": 1,
        "excluded_task_ids": ["acme__widget-1"],
        "excluded_task_ids_sha256": hashlib.sha256(json.dumps(["acme__widget-1"], separators=(",", ":")).encode("utf-8")).hexdigest(),
    }
    policy_path.write_text(json.dumps(policy_data), encoding="utf-8")

    env = _setup_probe_env(
        tmp_path,
        monkeypatch,
        task_ids=("acme__widget-1", "acme__gadget-2"),
        conflicts=("acme__widget-1",),
    )
    broot = tmp_path / "broot_excl_drift"
    auth = authorize_s8_probe_run(
        grant_path=env["grant_file"],
        registration_file=env["reg_file"],
        registration_manifest_file=env["manifest_file"],
        compat_report_file=env["compat_file"],
        exclusion_policy_file=policy_path,
        budget_ledger_root=broot,
        current_username="testuser",
    )
    assert auth.exclusion_policy_sha256 != ""

    # Tamper with policy file
    policy_data["description"] = "tampered"
    policy_path.write_text(json.dumps(policy_data), encoding="utf-8")

    with pytest.raises(V2GateError, match="exclusion_policy_sha256"):
        check_probe_grant_still_valid(auth, budget_ledger_root=broot)

