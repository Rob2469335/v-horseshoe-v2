"""Tests for the S8 production batch driver (resume, retry, idempotency)."""
from __future__ import annotations

import json

import pytest

from qwen_train.f2_s8_batch import drive_batch, reconcile_counts
from qwen_train.f2_s8_factory import (
    AppendOnlyLedger,
    RegistrationMismatch,
    RetryPolicy,
)

REG_SHA = "c" * 64
TASKS = [f"t-{i}" for i in range(5)]


def _admit_process(ledger: AppendOnlyLedger):
    """A per-task callable that appends an admitted record for a task."""

    def process(task_id: str, attempt: int) -> dict:
        return ledger.append({
            "task_id": task_id,
            "registration_id": "reg",
            "registration_sha256": REG_SHA,
            "outcome": "base_fail_gold_pass",
            "outcome_class": "scientific",
            "admitted": True,
            "attempt": attempt,
        })

    return process


class TestDriveBatch:
    def test_drives_all_runnable_tasks(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        report = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=_admit_process(ledger),
        )
        assert report.selected == tuple(TASKS)
        assert report.counts["admitted"] == 5
        assert report.counts["completed"] == 5

    def test_re_invocation_is_idempotent(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        first = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=_admit_process(ledger),
        )
        assert len(first.selected) == 5
        second = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=lambda *_: pytest.fail("must not re-run admitted tasks"),
        )
        assert second.selected == ()
        assert set(second.skipped_admitted) == set(TASKS)

    def test_infrastructure_exception_is_recorded_and_batch_continues(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")

        def flaky(task_id: str, attempt: int) -> dict:
            if task_id == "t-2":
                raise RuntimeError("simulated transport failure")
            return _admit_process(ledger)(task_id, attempt)

        report = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=flaky,
        )
        assert report.counts["admitted"] == 4
        summary = next(r for r in report.results if r["task_id"] == "t-2")
        assert summary["outcome"] == "infrastructure_failure"
        assert summary["admitted"] is False
        assert "RuntimeError" in summary["error"]
        # the infrastructure failure is not a scientific negative
        assert ledger.terminal_scientific("t-2") is None

    def test_infrastructure_failure_is_retried_until_cap(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        attempts: list[str] = []

        def always_infra(task_id: str, attempt: int) -> dict:
            attempts.append(task_id)
            raise RuntimeError("down")

        policy = RetryPolicy(max_attempts=2, max_tasks_per_run=1)
        for _ in range(3):
            drive_batch(
                ordered_task_ids=["t-0"], ledger=ledger, registration_id="reg",
                registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
                process_task=always_infra, policy=policy,
            )
        # two attempts allowed, then the cap is exhausted and nothing is selected
        assert attempts == ["t-0", "t-0"]
        final = drive_batch(
            ordered_task_ids=["t-0"], ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=always_infra, policy=policy,
        )
        assert final.selected == ()
        assert final.skipped_exhausted == ("t-0",)

    def test_registration_mismatch_blocks_before_any_task(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        with pytest.raises(RegistrationMismatch):
            drive_batch(
                ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
                registration_sha256=REG_SHA, expected_registration_sha256="9" * 64,
                process_task=lambda *_: pytest.fail("must not run"),
            )

    def test_resume_selects_only_unfinished_work(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        # pre-seed: t-0 admitted, t-1 infra, t-2 attempted only
        ledger.append({"task_id": "t-0", "outcome": "base_fail_gold_pass",
                       "outcome_class": "scientific", "admitted": True, "attempt": 1})
        ledger.append({"task_id": "t-1", "outcome": "infrastructure_failure",
                       "outcome_class": "infrastructure", "admitted": False, "attempt": 1})
        ledger.append({"task_id": "t-2", "outcome": "timeout",
                       "outcome_class": "infrastructure", "admitted": False, "attempt": 1})
        report = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=_admit_process(ledger),
        )
        # t-2 and t-3 were never admitted; t-1 had one infra attempt (cap 3) so it
        # is retried; t-0 is skipped as admitted.
        assert "t-0" not in report.selected
        assert set(report.selected) == {"t-1", "t-2", "t-3", "t-4"}
        assert report.skipped_admitted == ("t-0",)

    def test_stop_after_bounds_a_run(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        report = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=_admit_process(ledger), stop_after=2,
        )
        assert report.selected[:2] == ("t-0", "t-1")
        assert len(report.results) == 2
        assert "stop_after" in report.stopped_reason

    def test_progress_is_written_and_verifiable(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        progress = tmp_path / "progress.jsonl"
        drive_batch(
            ordered_task_ids=TASKS[:2], ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=_admit_process(ledger), progress_path=progress,
        )
        lines = [json.loads(x) for x in progress.read_text().splitlines()]
        assert [x["task_id"] for x in lines] == ["t-0", "t-1"]
        assert lines[-1]["admitted_total"] == 2

    def test_reconcile_counts_are_derived_from_the_ledger(self, tmp_path):
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=_admit_process(ledger),
        )
        counts = reconcile_counts(ledger, TASKS)
        assert counts == {
            "registered": 5, "attempted": 5, "completed": 5,
            "admitted": 5, "infrastructure_only": 0,
        }
