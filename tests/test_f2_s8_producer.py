"""Synthetic tests for the guest runner and the canonical production wiring.

No task, VM, disk, network, credential or model is touched.  The transport and
the base/gold executions are in-test callables; JUnit artifacts are written by
the test.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from qwen_train.f2_s8_factory import AppendOnlyLedger
from qwen_train.f2_s8_producer import (
    base_fails_gold_passes,
    build_canonical_verifiers,
    canonical_report,
    compose_execution_result,
    produce_registered_task,
)
from qwen_train.f2_s8_runner import (
    RUNNER_SCHEMA,
    GuestRunner,
    RunnerError,
    RunnerNotConfigured,
    RunOutcome,
    publish_atomic,
    verify_receipt,
)
from qwen_train.f2_s8_session import (
    REQUIRED_GATES,
    FakeVMAdapter,
    GateNotSatisfied,
    build_task_spec,
)

class _StubReceipt:
    """Minimal receipt stand-in for composition tests (payload only)."""

    def __init__(self, *, failure_class: str) -> None:
        self.payload = {"failure_class": failure_class, "junit": None}


REG_SHA = "a" * 64
EVAL = {
    "evaluator_id": "f2_pytest_junit_evaluator",
    "version": "1.0.0",
    "implementation_digest": "b" * 64,
    "procedure_id": "f2_pytest_junit_identity_v1",
    "protocol_version": "f2_experiment_j_v1",
}
ALL_GATES = {name: True for name in REQUIRED_GATES}


def _spec(**over):
    kw = dict(
        task_id="o__a-1", source="swe-bench-live", repo="o/a", base_commit="f" * 40,
        test_cmd="pytest -q", fail_to_pass=["t/test_x.py::test_a"],
        pass_to_pass=["t/test_x.py::test_b"], registration_id="reg",
        registration_sha256=REG_SHA, evaluator=EVAL,
    )
    kw.update(over)
    return build_task_spec(**kw)


_JUNIT_BASE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="pytest" tests="2" failures="1">\n'
    '<testcase classname="t.test_x" name="test_a" time="0.1">'
    '<failure message="boom">trace</failure></testcase>\n'
    '<testcase classname="t.test_x" name="test_b" time="0.1"/>\n'
    "</testsuite></testsuites>\n"
)
_JUNIT_GOLD = _JUNIT_BASE.replace(
    '<testcase classname="t.test_x" name="test_a" time="0.1">'
    '<failure message="boom">trace</failure></testcase>',
    '<testcase classname="t.test_x" name="test_a" time="0.1"/>',
).replace('failures="1"', 'failures="0"')


def _publish_junit(root: Path, spec, name: str, xml: str) -> Path:
    target = Path(root) / str(spec.payload["evidence_dir"]) / name
    publish_atomic(target, xml.encode("utf-8"))
    return target


class TestAtomicPublication:
    def test_publishes_bytes_and_removes_the_temp_file(self, tmp_path):
        target = tmp_path / "sub" / "a.bin"
        digest = publish_atomic(target, b"hello")
        assert digest == hashlib.sha256(b"hello").hexdigest()
        assert target.read_bytes() == b"hello"
        assert not (tmp_path / "sub" / "a.bin.part").exists()


class TestGuestRunner:
    def test_no_transport_means_no_execution(self):
        with pytest.raises(RunnerNotConfigured):
            GuestRunner(transport=None).run(_spec())

    def test_valid_run_produces_a_verifiable_receipt(self, tmp_path):
        spec = _spec()
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=1, stdout=b"x"),
                             output_root=tmp_path)
        _publish_junit(tmp_path, spec, f"{spec.task_id}.junit.xml", _JUNIT_BASE)
        receipt = runner.run(spec)
        assert receipt.payload["runner_schema"] == RUNNER_SCHEMA
        assert len(receipt.receipt_digest) == 64
        ok, reasons = verify_receipt(receipt, spec)
        assert ok, reasons

    def test_missing_junit_is_an_infrastructure_failure(self, tmp_path):
        spec = _spec()
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0),
                             output_root=tmp_path)
        receipt = runner.run(spec)
        assert receipt.payload["failure_class"] == "infrastructure_failure"
        assert receipt.infrastructure is True

    def test_timeout_from_the_transport_is_infrastructure(self, tmp_path):
        spec = _spec()
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=-1, failure_class="timeout"),
                             output_root=tmp_path)
        receipt = runner.run(spec)
        assert receipt.payload["failure_class"] == "timeout" and receipt.infrastructure

    def test_output_truncation_is_flagged(self, tmp_path):
        spec = _spec(max_output_bytes=4)
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0, stdout=b"0123456789"),
                             output_root=tmp_path)
        receipt = runner.run(spec)
        assert receipt.payload["output_truncated"] is True
        assert receipt.payload["stdout_bytes"] == 4

    def test_unknown_failure_class_is_refused(self, tmp_path):
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0, failure_class="weird"),
                             output_root=tmp_path)
        with pytest.raises(RunnerError, match="unknown failure_class"):
            runner.run(_spec())

    @pytest.mark.parametrize("mutate,reason", [
        (lambda p: p.update(task_id="other"), "task identity mismatch"),
        (lambda p: p.update(spec_digest="0" * 64), "spec digest mismatch"),
        (lambda p: p.update(registration_sha256="9" * 64), "registration digest mismatch"),
        (lambda p: p.update(evaluator={}), "evaluator identity mismatch"),
        (lambda p: p.update(runner_schema="other"), "wrong runner schema"),
    ])
    def test_receipt_binding_failures_are_detected(self, tmp_path, mutate, reason):
        spec = _spec()
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0), output_root=tmp_path)
        _publish_junit(tmp_path, spec, f"{spec.task_id}.junit.xml", _JUNIT_BASE)
        receipt = runner.run(spec)
        payload = receipt.to_dict()
        mutate(payload)
        ok, reasons = verify_receipt(payload, spec)
        assert ok is False and reason in reasons

    def test_tampered_receipt_digest_is_detected(self, tmp_path):
        spec = _spec()
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0), output_root=tmp_path)
        _publish_junit(tmp_path, spec, f"{spec.task_id}.junit.xml", _JUNIT_BASE)
        payload = runner.run(spec).to_dict()
        payload["exit_status"] = 0
        payload["failure_class"] = ""
        payload["junit"] = None
        ok, reasons = verify_receipt(payload, spec)
        assert ok is False and "receipt digest does not match its content" in reasons

    def test_empty_junit_is_not_acceptable_evidence(self, tmp_path):
        spec = _spec()
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0), output_root=tmp_path)
        _publish_junit(tmp_path, spec, f"{spec.task_id}.junit.xml", "")
        ok, reasons = verify_receipt(runner.run(spec), spec, min_junit_bytes=1)
        assert ok is False and "JUnit artifact is empty" in reasons


class TestCanonicalEvaluator:
    def test_base_fails_and_gold_passes_through_the_real_evaluator(self, tmp_path):
        spec = _spec()
        base = _publish_junit(tmp_path, spec, "base.junit.xml", _JUNIT_BASE)
        gold = _publish_junit(tmp_path, spec, "gold.junit.xml", _JUNIT_GOLD)
        base_report = canonical_report(junit_path=base, spec=spec, out_path=tmp_path / "b.json")
        gold_report = canonical_report(junit_path=gold, spec=spec, out_path=tmp_path / "g.json")
        assert base_report["declared_result"] == "fail"
        assert gold_report["declared_result"] == "pass"
        assert base_fails_gold_passes(base_report=base_report, gold_report=gold_report) is True

    def test_missing_node_is_not_a_pass(self, tmp_path):
        spec = _spec()
        xml = _JUNIT_GOLD.replace('name="test_a"', 'name="test_other"')
        gold = _publish_junit(tmp_path, spec, "gold2.junit.xml", xml)
        report = canonical_report(junit_path=gold, spec=spec, out_path=tmp_path / "g2.json")
        assert report["declared_result"] == "fail"


class TestComposition:
    def _phase(self, tmp_path, spec, xml):
        """Publish the runner's expected artifact name and take a receipt."""
        path = _publish_junit(tmp_path, spec, f"{spec.task_id}.junit.xml", xml)
        runner = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0), output_root=tmp_path)
        return runner.run(spec), path

    def test_both_good_composes_the_admitted_outcome(self, tmp_path):
        spec = _spec()
        base_r, base_p = self._phase(tmp_path, spec, _JUNIT_BASE)
        base_rep = canonical_report(junit_path=base_p, spec=spec, out_path=tmp_path / "b.json")
        gold_r, gold_p = self._phase(tmp_path, spec, _JUNIT_GOLD)
        gold_rep = canonical_report(junit_path=gold_p, spec=spec, out_path=tmp_path / "g.json")
        result = compose_execution_result(spec=spec, base_receipt=base_r, gold_receipt=gold_r,
                                          base_report=base_rep, gold_report=gold_rep)
        assert result.outcome == "base_fail_gold_pass"
        assert len(result.evidence_sha256) == 64

    @pytest.mark.parametrize("base, gold, expected", [
        ("pass", "pass", "base_pass"),
        ("fail", "fail", "gold_fail"),
    ])
    def test_contract_violations_are_scientific_not_admitted(self, tmp_path, base, gold, expected):
        spec = _spec()
        ok_receipt = _StubReceipt(failure_class="")
        result = compose_execution_result(
            spec=spec, base_receipt=ok_receipt, gold_receipt=ok_receipt,
            base_report={"declared_result": base}, gold_report={"declared_result": gold},
        )
        assert result.outcome == expected

    def test_missing_receipts_are_infrastructure_not_a_scientific_verdict(self):
        spec = _spec()
        result = compose_execution_result(
            spec=spec, base_receipt=None, gold_receipt=None,
            base_report={"declared_result": "fail"}, gold_report={"declared_result": "pass"},
        )
        assert result.outcome == "infrastructure_failure"

    def test_timeout_short_circuits_to_infrastructure(self):
        spec = _spec()
        result = compose_execution_result(
            spec=spec,
            base_receipt=_StubReceipt(failure_class="timeout"),
            gold_receipt=_StubReceipt(failure_class=""),
            base_report=None, gold_report=None,
        )
        assert result.outcome == "timeout"


class TestProduceRegisteredTask:
    def test_gates_are_checked_before_any_execution(self, tmp_path):
        calls = []

        def execute(spec, phase):
            calls.append(phase)
            raise AssertionError("must not execute before gates pass")

        with pytest.raises(GateNotSatisfied):
            produce_registered_task(
                spec=_spec(), gates={}, adapter=FakeVMAdapter(), execute=execute,
                ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), artifact_root=tmp_path,
            )
        assert calls == []

    def test_admission_is_blocked_until_evidence_and_governance_pass(self, tmp_path):
        spec = _spec()

        def execute(s, phase):
            xml = _JUNIT_BASE if phase == "base" else _JUNIT_GOLD
            _publish_junit(tmp_path, spec, f"{spec.task_id}.junit.xml", xml)
            return GuestRunner(transport=lambda sp, a: RunOutcome(exit_status=0),
                               output_root=tmp_path).run(spec)

        res = produce_registered_task(
            spec=spec, gates=ALL_GATES, adapter=FakeVMAdapter(), execute=execute,
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), artifact_root=tmp_path,
            authorized_evaluators=None, expected_registration_sha256=REG_SHA, ts="T",
        )
        # the canonical evidence verifier has no evidence records here, and the
        # governed-pair verifier has no store/registry, so admission must fail closed
        assert res.record["admitted"] is False
        assert res.state in {"evidence_missing_or_invalid", "scientific_failure", "infrastructure_failure"}

    def test_bad_receipt_stops_the_pipeline(self, tmp_path):
        spec = _spec()
        bad = GuestRunner(transport=lambda s, a: RunOutcome(exit_status=0),
                          output_root=tmp_path).run(spec)  # no JUnit -> infrastructure
        with pytest.raises(Exception):
            produce_registered_task(
                spec=spec, gates=ALL_GATES, adapter=FakeVMAdapter(),
                execute=lambda s, phase: bad if phase == "base" else bad,
                ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), artifact_root=tmp_path,
                expected_registration_sha256=REG_SHA,
            )

    def test_canonical_verifiers_fail_closed_when_governance_inputs_are_absent(self, tmp_path):
        evaluate, verify_evidence, verify_pair = build_canonical_verifiers(
            artifact_root=tmp_path, authorized_evaluators=None,
        )
        spec = _spec()
        from qwen_train.f2_s8_factory import ExecutionResult

        result = ExecutionResult(task_id=spec.task_id, outcome="base_fail_gold_pass", attempt=1,
                                 attempt_id="a#1", evidence_name="x.junit.xml",
                                 evidence_sha256="c" * 64, evaluator=dict(EVAL))
        assert verify_pair(spec, result) is False
        assert verify_evidence(spec, result) is False
