"""Tests for the S8 production batch driver (resume, retry, idempotency)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train.f2_s8_batch import (
    BatchError,
    drive_batch,
    reconcile_admissions_across_chains,
    reconcile_counts,
)
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

    def test_terminal_scientific_task_is_never_processed(self, tmp_path):
        """REGRESSION: a terminally recorded task is skipped at selection time —
        never executed, never given a bogus infrastructure row."""
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        ledger.append({"task_id": "t-0", "outcome": "gold_fail",
                       "outcome_class": "scientific", "admitted": False, "attempt": 1})
        processed: list[str] = []

        def process(task_id: str, attempt: int) -> dict:
            processed.append(task_id)
            return _admit_process(ledger)(task_id, attempt)

        report = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=process,
        )
        assert "t-0" not in processed
        assert "t-0" not in report.selected
        assert report.skipped_terminal == ("t-0",)
        # no second record for t-0 and no driver-invented infrastructure row
        assert len(ledger.attempts("t-0")) == 1
        assert report.to_dict()["skipped_terminal"] == ["t-0"]

    def test_batch_cannot_produce_a_duplicate_terminal_record(self, tmp_path):
        """A plan-driven batch never hands the ledger a task it would refuse."""
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        ledger.append({"task_id": "t-1", "outcome": "test_not_run",
                       "outcome_class": "scientific", "admitted": False, "attempt": 1})
        report = drive_batch(
            ordered_task_ids=TASKS, ledger=ledger, registration_id="reg",
            registration_sha256=REG_SHA, expected_registration_sha256=REG_SHA,
            process_task=_admit_process(ledger),
        )
        assert "t-1" not in report.selected
        assert all(r["task_id"] != "t-1" for r in report.results)
        terminal_rows = [r for r in ledger.records() if r["task_id"] == "t-1"]
        assert len(terminal_rows) == 1  # the seeded record, untouched
        assert ledger.verify()[0] is True
        assert report.counts == {
            "registered": 5, "attempted": 5, "completed": 5,
            "admitted": 4, "infrastructure_only": 0,
        }

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


def _sci(task_id: str, outcome: str, admitted: bool) -> dict:
    return {
        "task_id": task_id, "registration_id": "reg", "registration_sha256": REG_SHA,
        "outcome": outcome, "outcome_class": "scientific", "admitted": admitted,
        "attempt": 1,
    }


def _infra(task_id: str, outcome: str = "infrastructure_failure") -> dict:
    return {
        "task_id": task_id, "registration_id": "reg", "registration_sha256": REG_SHA,
        "outcome": outcome, "outcome_class": "infrastructure", "admitted": False,
        "attempt": 1,
    }


def _chain(path, rows):
    ledger = AppendOnlyLedger(path)
    for row in rows:
        ledger.append(row)
    return ledger


class TestCrossChainAdmissionCrediting:
    """Owner rule (2026-10-15, option B): credit across registered chains, once.

    The operative chain stays the code-bound canonical count; a separately
    sanctioned corrective chain contributes its own admissions.  These tests pin
    the union rule, the double-count guard, the fail-closed verification and the
    equivalence with the canonical per-task ``admitted()`` predicate.
    """

    def test_disjoint_chains_credit_the_union_once(self, tmp_path):
        a = _chain(tmp_path / "a.jsonl", [_sci("t-0", "base_fail_gold_pass", True)])
        b = _chain(tmp_path / "b.jsonl", [_sci("t-3", "base_fail_gold_pass", True)])
        out = reconcile_admissions_across_chains([a, b], TASKS)
        assert out["operative_admitted"] == ["t-0"]
        assert out["credited_admitted"] == ["t-0", "t-3"]
        assert out["credited_count"] == 2
        assert out["credited_in_multiple_chains"] == []
        assert out["chains"][1]["admitted"] == ["t-3"]

    def test_a_task_admitted_in_both_chains_is_counted_once(self, tmp_path):
        a = _chain(tmp_path / "a.jsonl", [_sci("t-1", "base_fail_gold_pass", True)])
        b = _chain(tmp_path / "b.jsonl", [_sci("t-1", "base_fail_gold_pass", True)])
        out = reconcile_admissions_across_chains([a, b], TASKS)
        assert out["credited_count"] == 1
        assert out["credited_in_multiple_chains"] == ["t-1"]

    def test_chains_that_only_failed_or_ran_infrastructure_credit_nothing(self, tmp_path):
        a = _chain(tmp_path / "a.jsonl", [
            _infra("t-0"),
            _sci("t-1", "gold_fail", False),
            _sci("t-2", "test_not_run", False),
        ])
        out = reconcile_admissions_across_chains([a], TASKS)
        assert out["credited_admitted"] == []
        assert out["credited_count"] == 0

    def test_the_first_scientific_record_decides(self, tmp_path):
        # t-0: infrastructure first, then admitted -> credited.
        a = _chain(tmp_path / "a.jsonl", [
            _infra("t-0"),
            _sci("t-0", "base_fail_gold_pass", True),
        ])
        out = reconcile_admissions_across_chains([a], TASKS)
        assert out["credited_admitted"] == ["t-0"]

    def test_unregistered_admissions_are_reported_but_not_credited(self, tmp_path):
        a = _chain(tmp_path / "a.jsonl", [_sci("x-9", "base_fail_gold_pass", True)])
        out = reconcile_admissions_across_chains([a], TASKS)
        assert out["credited_admitted"] == []
        assert out["unregistered_admitted"] == ["x-9"]

    def test_tampered_chain_aborts_the_whole_reconciliation(self, tmp_path):
        a = _chain(tmp_path / "a.jsonl", [_sci("t-0", "base_fail_gold_pass", True)])
        b_path = tmp_path / "b.jsonl"
        _chain(b_path, [_sci("t-3", "base_fail_gold_pass", True)])
        raw = b_path.read_text(encoding="utf-8").replace("t-3", "t-4", 1)
        b_path.write_text(raw, encoding="utf-8")
        with pytest.raises(BatchError):
            reconcile_admissions_across_chains([a, b_path], TASKS)

    def test_no_chain_is_refused(self):
        with pytest.raises(BatchError):
            reconcile_admissions_across_chains([], TASKS)

    def test_credit_rule_matches_the_canonical_per_task_predicate(self, tmp_path):
        a = _chain(tmp_path / "a.jsonl", [
            _infra("t-0"), _sci("t-0", "base_fail_gold_pass", True),
            _sci("t-1", "gold_fail", False),
        ])
        b = _chain(tmp_path / "b.jsonl", [
            _sci("t-2", "base_fail_gold_pass", True),
            _sci("t-1", "base_fail_gold_pass", True),
        ])
        out = reconcile_admissions_across_chains([a, b], TASKS)
        canonical = sorted(
            t for t in TASKS if a.admitted(t) is not None or b.admitted(t) is not None
        )
        assert out["credited_admitted"] == canonical


def _prefix_digest(path, rows: int) -> str:
    """SHA-256 of the first ``rows`` lines of a ledger file (line endings kept)."""
    raw = Path(path).read_bytes()
    return hashlib.sha256(b"".join(raw.splitlines(keepends=True)[:rows])).hexdigest()


def _record_digest(path, index: int = 0) -> str:
    line = Path(path).read_text(encoding="utf-8").splitlines()[index]
    return json.loads(line)["record_digest"]


class TestCrossChainCorrectionBinding:
    """A correction row must BIND to a historical prefix of the chain it corrects.

    Verified on the real ledgers (2026-10-16): the corrected chain's recorded
    ``correction_of_chain_sha256`` is exactly the first-7-rows digest of the
    9-row operative chain.  Reconciliation refuses any correction whose binding is
    absent, malformed or unproven -- it never credits one on trust, and it never
    changes which ordinary admissions are credited.
    """

    def _correction(self, *, task_id, chain_name, chain_sha256, record_digest,
                    outcome="base_fail_gold_pass"):
        return {
            "task_id": task_id, "registration_id": "reg",
            "registration_sha256": REG_SHA, "outcome": outcome,
            "outcome_class": "scientific", "admitted": True, "attempt": 2,
            "correction_of_chain": chain_name,
            "correction_of_chain_sha256": chain_sha256,
            "correction_of_record_digest": record_digest,
        }

    def test_a_valid_historical_prefix_binding_is_accepted(self, tmp_path):
        a_path = tmp_path / "a.jsonl"
        a = _chain(a_path, [_sci("t-1", "gold_fail", False)])
        prefix = _prefix_digest(a_path, 1)               # the chain as it stood
        a.append(_sci("t-2", "base_fail_gold_pass", True))  # chain later grows
        b = _chain(tmp_path / "b.jsonl", [self._correction(
            task_id="t-1", chain_name=a_path.name, chain_sha256=prefix,
            record_digest=_record_digest(a_path, 0))])
        out = reconcile_admissions_across_chains([a, b], TASKS)
        assert out["corrections_verified"] == 1
        assert out["credited_admitted"] == ["t-1", "t-2"]

    def test_an_invalid_prefix_digest_is_refused(self, tmp_path):
        a_path = tmp_path / "a.jsonl"
        a = _chain(a_path, [_sci("t-1", "gold_fail", False)])
        b = _chain(tmp_path / "b.jsonl", [self._correction(
            task_id="t-1", chain_name=a_path.name, chain_sha256="0" * 64,
            record_digest=_record_digest(a_path, 0))])
        with pytest.raises(BatchError, match="not a historical prefix"):
            reconcile_admissions_across_chains([a, b], TASKS)

    def test_an_altered_prefix_hash_is_refused(self, tmp_path):
        a_path = tmp_path / "a.jsonl"
        a = _chain(a_path, [_sci("t-1", "gold_fail", False)])
        first = a_path.read_bytes().splitlines(keepends=True)[:1]
        altered = hashlib.sha256(b"".join(first).replace(b"t-1", b"t-1 ")).hexdigest()
        b = _chain(tmp_path / "b.jsonl", [self._correction(
            task_id="t-1", chain_name=a_path.name, chain_sha256=altered,
            record_digest=_record_digest(a_path, 0))])
        with pytest.raises(BatchError, match="not a historical prefix"):
            reconcile_admissions_across_chains([a, b], TASKS)

    def test_a_malformed_correction_reference_is_refused(self, tmp_path):
        a_path = tmp_path / "a.jsonl"
        a = _chain(a_path, [_sci("t-1", "gold_fail", False)])
        rec = self._correction(
            task_id="t-1", chain_name=a_path.name,
            chain_sha256=_prefix_digest(a_path, 1),
            record_digest=_record_digest(a_path, 0))
        del rec["correction_of_record_digest"]        # the three fields bind together
        b = _chain(tmp_path / "b.jsonl", [rec])
        with pytest.raises(BatchError, match="required together"):
            reconcile_admissions_across_chains([a, b], TASKS)

    def test_a_correction_naming_an_absent_record_is_refused(self, tmp_path):
        a_path = tmp_path / "a.jsonl"
        a = _chain(a_path, [_sci("t-1", "gold_fail", False)])
        b = _chain(tmp_path / "b.jsonl", [self._correction(
            task_id="t-1", chain_name=a_path.name,
            chain_sha256=_prefix_digest(a_path, 1), record_digest="f" * 64)])
        with pytest.raises(BatchError, match="does not exist"):
            reconcile_admissions_across_chains([a, b], TASKS)

    def test_a_correction_referencing_an_unrelated_chain_is_refused(self, tmp_path):
        a_path = tmp_path / "a.jsonl"
        a = _chain(a_path, [_sci("t-1", "gold_fail", False)])
        b = _chain(tmp_path / "b.jsonl", [self._correction(
            task_id="t-1", chain_name="not-here.jsonl", chain_sha256="a" * 64,
            record_digest=_record_digest(a_path, 0))])
        with pytest.raises(BatchError, match="not part of this reconciliation"):
            reconcile_admissions_across_chains([a, b], TASKS)

    def test_an_ordinary_record_is_not_a_correction(self, tmp_path):
        a = _chain(tmp_path / "a.jsonl", [_sci("t-0", "base_fail_gold_pass", True)])
        out = reconcile_admissions_across_chains([a], TASKS)
        assert out["corrections_verified"] == 0
        assert out["credited_admitted"] == ["t-0"]
