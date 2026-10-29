"""Behavioural tests for the S8 factory (ledger, retry, scheduler, resume).

Synthetic only.  The runner is always an in-test callable, so no task command,
VM, evaluator or network is ever touched.
"""
from __future__ import annotations

import json

import pytest

from qwen_train.f2_s8_factory import (
    ADMITTED_OUTCOME,
    GENESIS_DIGEST,
    REQUIRED_EVIDENCE_ROLES,
    AppendOnlyLedger,
    ExecutionResult,
    FactoryError,
    LedgerError,
    RegistrationMismatch,
    RetryPolicy,
    counts,
    plan_run,
    run_once,
)

REG_SHA = "a" * 64
EVAL = {
    "evaluator_id": "f2_pytest_junit_evaluator",
    "version": "1.0.0",
    "implementation_digest": "b" * 64,
    "procedure_id": "f2_pytest_junit_identity_v1",
    "protocol_version": "f2_experiment_j_v1",
}
TASKS = ["t-1", "t-2", "t-3"]


def _ok(task_id, attempt, **over):
    kw = dict(
        task_id=task_id,
        outcome=ADMITTED_OUTCOME,
        attempt=attempt,
        attempt_id=f"{task_id}#{attempt}",
        disk_id=f"{task_id}-disk-{attempt}",
        evidence_name=f"{task_id}.junit.xml",
        evidence_sha256="c" * 64,
        evidence_size=1234,
        evaluator=EVAL,
        # The prospective evidence contract (Decision 3): an admission must bind
        # the durable record digest and the run/environment/state identities.
        evidence_record_digest="d" * 64,
        environment_identity="test-env",
        execution_state_digest="e" * 64,
        base_state_digest="b" * 64,
        started_at="2026-10-16T00:00:00Z",
        finished_at="2026-10-16T00:00:01Z",
        repository_revision="a" * 40,
        artifacts={
            role: {"name": f"{role}.bin", "sha256": "f" * 64, "size_bytes": 10}
            for role in REQUIRED_EVIDENCE_ROLES
        } | {"gold_report": {"name": f"{task_id}.junit.xml", "sha256": "c" * 64,
                             "size_bytes": 1234}},
    )
    kw.update(over)
    return ExecutionResult(**kw)


def _runner(results):
    def _run(task_id, attempt):
        return results[(task_id, attempt)]

    return _run


def _ledger(tmp_path, name="ledger.jsonl"):
    return AppendOnlyLedger(tmp_path / name)


class TestLedgerIntegrity:
    def test_genesis_on_empty_ledger(self, tmp_path):
        ledger = _ledger(tmp_path)
        ok, reason = ledger.verify()
        assert ok and "intact" in reason
        assert ledger.records() == []

    def test_append_chains_and_verifies(self, tmp_path):
        ledger = _ledger(tmp_path)
        first = ledger.append({"task_id": "t-1", "outcome": "test_not_run", "outcome_class": "scientific"})
        second = ledger.append({"task_id": "t-2", "outcome": "timeout", "outcome_class": "infrastructure"})
        assert first["seq"] == 0 and first["prev_digest"] == GENESIS_DIGEST
        assert second["seq"] == 1 and second["prev_digest"] == first["record_digest"]
        assert ledger.verify() == (True, "ledger chain intact")

    def test_tampering_is_detected(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.append({"task_id": "t-1", "outcome": "test_not_run", "outcome_class": "scientific"})
        ledger.append({"task_id": "t-2", "outcome": "timeout", "outcome_class": "infrastructure"})
        lines = ledger.path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0])
        row["outcome"] = "base_fail_gold_pass"
        row["admitted"] = True
        lines[0] = json.dumps(row, sort_keys=True)
        ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, reason = ledger.verify()
        assert ok is False and "digest" in reason

    def test_deletion_and_reorder_are_detected(self, tmp_path):
        ledger = _ledger(tmp_path)
        for task in ("t-1", "t-2", "t-3"):
            ledger.append({"task_id": task, "outcome": "timeout", "outcome_class": "infrastructure"})
        lines = ledger.path.read_text(encoding="utf-8").splitlines()
        ledger.path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
        ok, reason = ledger.verify()
        assert ok is False and "seq" in reason

    def test_a_second_terminal_scientific_record_is_refused(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.append({"task_id": "t-1", "outcome": "base_pass", "outcome_class": "scientific"})
        with pytest.raises(LedgerError, match="terminal scientific"):
            ledger.append({"task_id": "t-1", "outcome": "gold_fail", "outcome_class": "scientific"})

    def test_infrastructure_records_may_repeat_for_the_same_task(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.append({"task_id": "t-1", "outcome": "timeout", "outcome_class": "infrastructure"})
        ledger.append({"task_id": "t-1", "outcome": "timeout", "outcome_class": "infrastructure"})
        assert ledger.verify()[0] is True


class TestRunOnce:
    def test_admitted_outcome_is_recorded_with_evidence(self, tmp_path):
        ledger = _ledger(tmp_path)
        rec = run_once(
            task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): _ok("t-1", 1)}),
            registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL, ts="T",
        )
        assert rec["admitted"] is True
        assert rec["evidence"]["sha256"] == "c" * 64
        assert rec["evaluator"]["procedure_id"] == "f2_pytest_junit_identity_v1"
        assert ledger.verify()[0] is True

    def test_admitted_without_evidence_is_refused(self, tmp_path):
        ledger = _ledger(tmp_path)
        bad = _ok("t-1", 1, evidence_name="", evidence_sha256="")
        with pytest.raises(FactoryError, match="missing evidence is never a pass"):
            run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): bad}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        assert ledger.records() == []

    def test_admitted_without_evaluator_identity_is_refused(self, tmp_path):
        ledger = _ledger(tmp_path)
        bad = _ok("t-1", 1, evaluator={})
        with pytest.raises(FactoryError, match="evaluator"):
            run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): bad}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator={})

    def test_wrong_task_identity_from_the_runner_is_refused(self, tmp_path):
        ledger = _ledger(tmp_path)
        with pytest.raises(FactoryError, match="runner returned task"):
            run_once(task_id="t-1", ledger=ledger,
                     runner=_runner({("t-1", 1): _ok("t-OTHER", 1)}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)

    def test_wrong_attempt_number_is_refused(self, tmp_path):
        ledger = _ledger(tmp_path)
        with pytest.raises(FactoryError, match="attempt"):
            run_once(task_id="t-1", ledger=ledger,
                     runner=_runner({("t-1", 1): _ok("t-1", 7)}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)

    def test_unknown_outcome_is_refused(self, tmp_path):
        ledger = _ledger(tmp_path)
        with pytest.raises(FactoryError, match="unknown outcome"):
            run_once(task_id="t-1", ledger=ledger,
                     runner=_runner({("t-1", 1): _ok("t-1", 1, outcome="looks_fine")}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)

    def test_infrastructure_failure_is_not_a_scientific_outcome(self, tmp_path):
        ledger = _ledger(tmp_path)
        rec = run_once(
            task_id="t-1", ledger=ledger,
            runner=_runner({("t-1", 1): _ok("t-1", 1, outcome="infrastructure_failure",
                                           evidence_name="", evidence_sha256="")}),
            registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
        )
        assert rec["outcome_class"] == "infrastructure"
        assert rec["admitted"] is False
        assert ledger.terminal_scientific("t-1") is None

    @pytest.mark.parametrize("name", ["../escape.xml", "sub/dir.xml", "C:\\x.xml", "a;b.xml", ""])
    def test_unsafe_artifact_names(self, tmp_path, name):
        ledger = _ledger(tmp_path)
        result = _ok("t-1", 1, evidence_name=name, evidence_sha256="c" * 64)
        if name == "":
            with pytest.raises(FactoryError, match="missing evidence"):
                run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): result}),
                         registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        else:
            with pytest.raises(FactoryError, match="unsafe artifact name"):
                run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): result}),
                         registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)

    def test_retry_cap_is_enforced(self, tmp_path):
        ledger = _ledger(tmp_path)
        policy = RetryPolicy(max_attempts=2)
        infra = _ok("t-1", 1, outcome="timeout", evidence_name="", evidence_sha256="")
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): infra}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL, policy=policy)
        infra2 = _ok("t-1", 2, outcome="timeout", evidence_name="", evidence_sha256="")
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 2): infra2}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL, policy=policy)
        with pytest.raises(FactoryError, match="exceeded max_attempts"):
            run_once(task_id="t-1", ledger=ledger, runner=_runner({}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
                     policy=policy)

    def test_run_once_refuses_a_terminal_task_before_invoking_the_runner(self, tmp_path):
        """REGRESSION: the recording seam must refuse before spending execution."""
        ledger = _ledger(tmp_path)
        run_once(task_id="t-1", ledger=ledger,
                 runner=_runner({("t-1", 1): _ok("t-1", 1, outcome="gold_fail",
                                                 evidence_name="", evidence_sha256="")}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)

        def _explode(task_id, attempt):
            raise AssertionError("runner must not be called for a terminally recorded task")

        with pytest.raises(LedgerError, match="terminal scientific record"):
            run_once(task_id="t-1", ledger=ledger, runner=_explode,
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        # nothing ran, nothing was written: still exactly the one seeded record
        assert len(ledger.records()) == 1
        assert ledger.verify()[0] is True


class TestEvidenceBindingIsFailClosed:
    """Decision 3: an ADMITTED outcome must bind its evidence identities.

    A missing, substituted or inconsistent binding is refused -- the ledger must
    never carry a half-bound admission.
    """

    def _run(self, tmp_path, **over):
        return run_once(
            task_id="t-1", ledger=AppendOnlyLedger(tmp_path / "l.jsonl"),
            runner=lambda tid, attempt: _ok(tid, attempt, **over),
            registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
        )

    def test_a_bound_admission_carries_the_bundle_into_the_record(self, tmp_path):
        rec = self._run(tmp_path)
        assert rec["admitted"] is True
        assert rec["evidence_record_digest"] == "d" * 64
        bundle = rec["evidence_bundle"]
        assert bundle["environment_identity"] == "test-env"
        assert bundle["execution_state_digest"] == "e" * 64
        assert bundle["repository_revision"] == "a" * 40
        assert set(REQUIRED_EVIDENCE_ROLES) <= set(bundle["artifacts"])

    def test_a_missing_record_digest_is_refused(self, tmp_path):
        with pytest.raises(FactoryError, match="persisted evidence record"):
            self._run(tmp_path, evidence_record_digest="")

    def test_a_substituted_record_digest_is_refused(self, tmp_path):
        with pytest.raises(FactoryError, match="persisted evidence record"):
            self._run(tmp_path, evidence_record_digest="NOT-A-DIGEST")

    def test_a_missing_environment_identity_is_refused(self, tmp_path):
        with pytest.raises(FactoryError, match="environment identity"):
            self._run(tmp_path, environment_identity="")

    def test_a_missing_execution_state_digest_is_refused(self, tmp_path):
        with pytest.raises(FactoryError, match="execution-state digest"):
            self._run(tmp_path, execution_state_digest="")

    def test_a_missing_repository_revision_is_refused(self, tmp_path):
        with pytest.raises(FactoryError, match="repository_revision"):
            self._run(tmp_path, repository_revision="")

    def test_a_placeholder_timestamp_is_refused(self, tmp_path):
        with pytest.raises(FactoryError, match="ISO-8601 UTC"):
            self._run(tmp_path, started_at="t0")

    def test_a_missing_required_artifact_role_is_refused(self, tmp_path):
        artifacts = {r: {"name": f"{r}.bin", "sha256": "f" * 64, "size_bytes": 1}
                     for r in REQUIRED_EVIDENCE_ROLES}
        artifacts.pop("gold_run_log")
        with pytest.raises(FactoryError, match="missing required evidence artifacts"):
            self._run(tmp_path, artifacts=artifacts)

    def test_an_artifact_with_an_inconsistent_digest_is_refused(self, tmp_path):
        artifacts = {r: {"name": f"{r}.bin", "sha256": "f" * 64, "size_bytes": 1}
                     for r in REQUIRED_EVIDENCE_ROLES}
        artifacts["base_report"] = {"name": "b.bin", "sha256": "short", "size_bytes": 1}
        with pytest.raises(FactoryError, match="no 64-hex SHA-256"):
            self._run(tmp_path, artifacts=artifacts)

    def test_an_artifact_without_a_size_is_refused(self, tmp_path):
        artifacts = {r: {"name": f"{r}.bin", "sha256": "f" * 64, "size_bytes": 1}
                     for r in REQUIRED_EVIDENCE_ROLES}
        artifacts["gold_report"] = {"name": "g.bin", "sha256": "f" * 64}
        with pytest.raises(FactoryError, match="non-negative size_bytes"):
            self._run(tmp_path, artifacts=artifacts)

    def test_a_non_admitted_outcome_is_unaffected(self, tmp_path):
        """A non-admitted outcome carries no admission binding (and is not counted)."""
        rec = run_once(
            task_id="t-2", ledger=AppendOnlyLedger(tmp_path / "l2.jsonl"),
            runner=lambda tid, attempt: _ok(
                tid, attempt, outcome="gold_fail", evidence_name="",
                evidence_sha256="", evidence_size=0),
            registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
        )
        assert rec["admitted"] is False
        assert "evidence_bundle" not in rec


class TestPlanning:
    def test_registration_digest_mismatch_blocks_selection(self, tmp_path):
        ledger = _ledger(tmp_path)
        with pytest.raises(RegistrationMismatch, match="not the pinned operative"):
            plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                     registration_sha256="d" * 64, expected_registration_sha256=REG_SHA)

    def test_stale_registration_is_rejected_the_same_way(self, tmp_path):
        ledger = _ledger(tmp_path)
        with pytest.raises(RegistrationMismatch):
            plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                     registration_sha256=REG_SHA, expected_registration_sha256="e" * 64)

    def test_selection_is_deterministic_and_in_registered_order(self, tmp_path):
        ledger = _ledger(tmp_path)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
                        policy=RetryPolicy(max_tasks_per_run=2))
        assert plan.runnable == ("t-1", "t-2")

    def test_max_tasks_per_run_caps_the_batch(self, tmp_path):
        ledger = _ledger(tmp_path)
        plan = plan_run(ordered_task_ids=[f"t-{i}" for i in range(50)], ledger=ledger,
                        registration_id="reg", registration_sha256=REG_SHA,
                        expected_registration_sha256=REG_SHA, policy=RetryPolicy(max_tasks_per_run=25))
        assert len(plan.runnable) == 25

    def test_admitted_tasks_are_skipped(self, tmp_path):
        ledger = _ledger(tmp_path)
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): _ok("t-1", 1)}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert plan.skipped_admitted == ("t-1",)
        assert plan.skipped_terminal == ()
        assert plan.runnable == ("t-2", "t-3")

    @pytest.mark.parametrize("outcome", ["base_pass", "gold_fail", "test_not_run"])
    def test_terminal_scientific_failure_is_never_selected_again(self, tmp_path, outcome):
        """REGRESSION (contract invariant 5): a non-admitted scientific outcome
        is terminal exactly like an admitted one, and the scheduler must never
        re-select it (the ledger would refuse the resulting record)."""
        ledger = _ledger(tmp_path)
        run_once(task_id="t-1", ledger=ledger,
                 runner=_runner({("t-1", 1): _ok("t-1", 1, outcome=outcome,
                                                 evidence_name="", evidence_sha256="")}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert plan.runnable == ("t-2", "t-3")
        assert plan.skipped_terminal == ("t-1",)
        assert plan.skipped_admitted == ()
        # counts stay separate: completed counts it, admitted does not
        assert plan.counts["completed"] == 1
        assert plan.counts["admitted"] == 0

    def test_retryable_infrastructure_failure_remains_runnable_below_the_cap(self, tmp_path):
        ledger = _ledger(tmp_path)
        infra = _ok("t-1", 1, outcome="timeout", evidence_name="", evidence_sha256="")
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): infra}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert plan.runnable == ("t-1", "t-2", "t-3")
        assert plan.skipped_terminal == ()
        assert plan.skipped_exhausted == ()

    def test_plan_never_yields_a_task_the_ledger_would_refuse(self, tmp_path):
        """The plan's runnable set and the ledger's refusal rule must agree."""
        ledger = _ledger(tmp_path)
        for task, outcome in (("t-1", "gold_fail"), ("t-2", "base_pass")):
            run_once(task_id=task, ledger=ledger,
                     runner=_runner({(task, 1): _ok(task, 1, outcome=outcome,
                                                    evidence_name="", evidence_sha256="")}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert plan.runnable == ("t-3",)
        assert all(ledger.terminal_scientific(t) is None for t in plan.runnable)
        # the append-only guard refuses exactly the tasks the plan skipped
        with pytest.raises(LedgerError, match="terminal scientific record"):
            ledger.append({"task_id": "t-1", "outcome": "gold_fail",
                           "outcome_class": "scientific", "attempt": 2})

    def test_mixed_ledger_separates_runnable_from_every_skip_bucket(self, tmp_path):
        ledger = _ledger(tmp_path)
        five = [f"t-{i}" for i in range(1, 6)]
        policy = RetryPolicy(max_attempts=2)
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): _ok("t-1", 1)}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
                 policy=policy)
        run_once(task_id="t-2", ledger=ledger,
                 runner=_runner({("t-2", 1): _ok("t-2", 1, outcome="gold_fail",
                                                 evidence_name="", evidence_sha256="")}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
                 policy=policy)
        run_once(task_id="t-3", ledger=ledger,
                 runner=_runner({("t-3", 1): _ok("t-3", 1, outcome="build_failure",
                                                 evidence_name="", evidence_sha256="")}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
                 policy=policy)
        for attempt in (1, 2):
            run_once(task_id="t-4", ledger=ledger,
                     runner=_runner({("t-4", attempt): _ok("t-4", attempt, outcome="timeout",
                                                           evidence_name="", evidence_sha256="")}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL,
                     policy=policy)
        plan = plan_run(ordered_task_ids=five, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
                        policy=policy, in_flight=["t-5"])
        assert plan.runnable == ("t-3",)
        assert plan.skipped_admitted == ("t-1",)
        assert plan.skipped_terminal == ("t-2",)
        assert plan.skipped_exhausted == ("t-4",)
        assert plan.skipped_running == ("t-5",)
        assert plan.counts == {
            "registered": 5, "attempted": 4, "completed": 2,
            "admitted": 1, "analyzable": 0,
        }

    def test_tasks_at_the_retry_cap_are_not_runnable(self, tmp_path):
        ledger = _ledger(tmp_path)
        policy = RetryPolicy(max_attempts=1)
        infra = _ok("t-1", 1, outcome="build_failure", evidence_name="", evidence_sha256="")
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): infra}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL, policy=policy)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
                        policy=policy)
        assert plan.skipped_exhausted == ("t-1",)
        assert plan.skipped_terminal == ()
        assert plan.runnable == ("t-2", "t-3")

    def test_in_flight_tasks_are_skipped_for_concurrency(self, tmp_path):
        ledger = _ledger(tmp_path)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
                        in_flight=["t-2"])
        assert plan.skipped_running == ("t-2",)
        assert plan.skipped_terminal == ()
        assert plan.runnable == ("t-1", "t-3")

    def test_concurrency_policy_rejects_a_zero_limit(self):
        with pytest.raises(FactoryError, match="max_concurrency"):
            RetryPolicy(max_concurrency=0)

    @pytest.mark.parametrize("kw", [{"max_attempts": 0}, {"max_tasks_per_run": 0}])
    def test_invalid_policy_is_refused(self, kw):
        with pytest.raises(FactoryError):
            RetryPolicy(**kw)


class TestResumeAndCounts:
    def test_resume_after_a_partial_run_is_idempotent(self, tmp_path):
        ledger = _ledger(tmp_path)
        policy = RetryPolicy(max_attempts=2, max_tasks_per_run=1)
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): _ok("t-1", 1)}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL, policy=policy)
        # simulate a crash before t-2 was ever attempted, then resume
        reopened = AppendOnlyLedger(ledger.path)
        assert reopened.verify()[0] is True
        plan = plan_run(ordered_task_ids=TASKS, ledger=reopened, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
                        policy=policy)
        assert plan.runnable == ("t-2",)
        assert plan.counts["admitted"] == 1

    def test_replanning_without_new_work_selects_nothing(self, tmp_path):
        ledger = _ledger(tmp_path)
        for task in ("t-1", "t-2", "t-3"):
            run_once(task_id=task, ledger=ledger,
                     runner=_runner({(task, 1): _ok(task, 1)}),
                     registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        plan = plan_run(ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                        registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert plan.runnable == ()

    def test_counts_separate_the_five_states(self, tmp_path):
        ledger = _ledger(tmp_path)
        run_once(task_id="t-1", ledger=ledger, runner=_runner({("t-1", 1): _ok("t-1", 1)}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        run_once(task_id="t-2", ledger=ledger,
                 runner=_runner({("t-2", 1): _ok("t-2", 1, outcome="base_pass",
                                                 evidence_name="", evidence_sha256="")}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        run_once(task_id="t-3", ledger=ledger,
                 runner=_runner({("t-3", 1): _ok("t-3", 1, outcome="timeout",
                                                 evidence_name="", evidence_sha256="")}),
                 registration_id="reg", registration_sha256=REG_SHA, evaluator=EVAL)
        c = counts(ledger, TASKS)
        assert c == {"registered": 3, "attempted": 3, "completed": 2, "admitted": 1, "analyzable": 0}

    def test_unregistered_ledger_entries_do_not_inflate_counts(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.append({"task_id": "not-registered", "outcome": "base_fail_gold_pass",
                       "outcome_class": "scientific", "admitted": True})
        c = counts(ledger, TASKS)
        assert c["attempted"] == 0 and c["admitted"] == 0 and c["registered"] == 3

class TestLifetimeRetryAccounting:
    """R1-D: every attempt counts, and determinism is not retried blindly."""

    def _infra(self, task_id, attempt, **over):
        return _ok(task_id, attempt, outcome="timeout",
                   evidence_name="", evidence_sha256="", **over)

    def test_every_recorded_attempt_counts_toward_the_cap(self, tmp_path):
        from qwen_train.f2_s8_factory import _attempts_used

        ledger = _ledger(tmp_path)
        policy = RetryPolicy(max_attempts=3)
        for attempt in (1, 2, 3):
            run_once(task_id="t-1", ledger=ledger,
                     runner=_runner({("t-1", attempt): self._infra("t-1", attempt)}),
                     registration_id="reg", registration_sha256=REG_SHA,
                     evaluator=EVAL, policy=policy)
        assert _attempts_used(ledger, "t-1") == 3
        with pytest.raises(FactoryError, match="exceeded max_attempts"):
            run_once(task_id="t-1", ledger=ledger, runner=_runner({}),
                     registration_id="reg", registration_sha256=REG_SHA,
                     evaluator=EVAL, policy=policy)

    def test_a_different_failure_class_does_not_buy_another_attempt(self, tmp_path):
        ledger = _ledger(tmp_path)
        policy = RetryPolicy(max_attempts=2)
        outcomes = ["timeout", "build_failure"]
        for attempt, outcome in enumerate(outcomes, 1):
            run_once(task_id="t-1", ledger=ledger,
                     runner=_runner({("t-1", attempt): _ok(
                         "t-1", attempt, outcome=outcome,
                         evidence_name="", evidence_sha256="")}),
                     registration_id="reg", registration_sha256=REG_SHA,
                     evaluator=EVAL, policy=policy)
        with pytest.raises(FactoryError, match="exceeded max_attempts"):
            run_once(task_id="t-1", ledger=ledger, runner=_runner({}),
                     registration_id="reg", registration_sha256=REG_SHA,
                     evaluator=EVAL, policy=policy)

    def test_previous_attempt_records_survive_a_retry(self, tmp_path):
        ledger = _ledger(tmp_path)
        policy = RetryPolicy(max_attempts=3)
        for attempt in (1, 2):
            run_once(task_id="t-1", ledger=ledger,
                     runner=_runner({("t-1", attempt): self._infra("t-1", attempt)}),
                     registration_id="reg", registration_sha256=REG_SHA,
                     evaluator=EVAL, policy=policy)
        before = [r["record_digest"] for r in ledger.records()]
        run_once(task_id="t-1", ledger=ledger,
                 runner=_runner({("t-1", 3): self._infra("t-1", 3)}),
                 registration_id="reg", registration_sha256=REG_SHA,
                 evaluator=EVAL, policy=policy)
        after = [r["record_digest"] for r in ledger.records()]
        assert after[:2] == before
        assert len(after) == 3
        assert ledger.verify()[0] is True

    def test_a_deterministic_build_failure_is_not_selected_again(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.append({
            "task_id": "t-1", "outcome": "build_failure",
            "outcome_class": "infrastructure",
            "reason_code": "PROJECT_PIN_CONFLICT",
            "reason": "PROJECT_PIN_CONFLICT: two pins disagree",
        })
        plan = plan_run(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            policy=RetryPolicy(max_attempts=3))
        assert plan.runnable == ("t-2", "t-3")
        assert plan.skipped_deterministic == ("t-1",)
        assert plan.deterministic_reasons["t-1"] == "PROJECT_PIN_CONFLICT"

    def test_a_transient_failure_stays_runnable(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.append({
            "task_id": "t-1", "outcome": "build_failure",
            "outcome_class": "infrastructure", "reason_code": "BUILD_TIMEOUT",
            "reason": "timed out",
        })
        plan = plan_run(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert plan.runnable == ("t-1", "t-2", "t-3")
        assert plan.skipped_deterministic == ()

    def test_an_older_record_without_a_reason_code_is_not_guessed(self, tmp_path):
        ledger = _ledger(tmp_path)
        ledger.append({
            "task_id": "t-1", "outcome": "build_failure",
            "outcome_class": "infrastructure", "reason": "unclassified",
        })
        plan = plan_run(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert "t-1" in plan.runnable

    def test_the_retry_decision_never_reads_a_production_ledger(self, tmp_path):
        ledger = _ledger(tmp_path)
        assert str(ledger.path).startswith(str(tmp_path))
        plan = plan_run(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA)
        assert len(plan.runnable) == 3
        assert not ledger.path.exists()
