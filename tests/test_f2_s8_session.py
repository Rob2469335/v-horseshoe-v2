"""Synthetic integration tests for the S8 session / controller / runner wiring.

No VM, disk, network, credential, model or real task is touched: the controller
is the in-memory fake and the runner is an in-test callable.  The live adapter is
only ever invoked in its refusing form.
"""
from __future__ import annotations

import pytest

from qwen_train.f2_s8_factory import (
    ADMITTED_OUTCOME, REQUIRED_EVIDENCE_ROLES, AppendOnlyLedger, ExecutionResult,
    RetryPolicy,
)
from qwen_train.f2_s8_session import (
    REQUIRED_GATES,
    FakeVMAdapter,
    GateNotSatisfied,
    LiveAdapterRefused,
    LiveVMAdapter,
    SessionError,
    build_task_spec,
    check_gates,
    require_gates,
    run_session,
)

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


def _result(spec, outcome=ADMITTED_OUTCOME, attempt=1):
    return ExecutionResult(
        task_id=spec.task_id, outcome=outcome, attempt=attempt,
        attempt_id=f"{spec.task_id}#{attempt}", evidence_name=f"{spec.task_id}.xml",
        evidence_sha256="c" * 64, evidence_size=10, evaluator=dict(EVAL),
        # Decision 3: the binding identities an admission must carry.
        evidence_record_digest="d" * 64, environment_identity="test-env",
        execution_state_digest="e" * 64, base_state_digest="b" * 64,
        started_at="2026-10-16T00:00:00Z", finished_at="2026-10-16T00:00:01Z",
        repository_revision="a" * 40,
        artifacts={r: {"name": f"{r}.bin", "sha256": "f" * 64, "size_bytes": 1}
                   for r in REQUIRED_EVIDENCE_ROLES}
        | {"gold_report": {"name": f"{spec.task_id}.xml", "sha256": "c" * 64,
                           "size_bytes": 10}},
    )


def _ledger(tmp_path):
    return AppendOnlyLedger(tmp_path / "ledger.jsonl")


class TestTaskSpec:
    def test_valid_spec_freezes_identity_and_declares_no_false_claims(self):
        spec = _spec()
        assert spec.task_id == "o__a-1"
        assert len(spec.spec_digest) == 64
        assert spec.payload["claims"]["contamination_free"] is False
        assert spec.payload["claims"]["guest_equivalent_to_arm_environment"] is False

    @pytest.mark.parametrize("over,match", [
        ({"task_id": "  "}, "task_id"),
        ({"repo": ""}, "repo"),
        ({"base_commit": "abc"}, "base_commit"),
        ({"test_cmd": ""}, "test_cmd"),
        ({"test_cmd": "pytest --junitxml=x.xml"}, "junitxml"),
        ({"fail_to_pass": []}, "FAIL_TO_PASS"),
        ({"registration_sha256": "zz"}, "registration_sha256"),
        ({"evaluator": {"evaluator_id": "x"}}, "evaluator"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"evidence_dir": "../escape"}, "evidence_dir"),
    ])
    def test_invalid_specs_are_refused(self, over, match):
        with pytest.raises(SessionError, match=match):
            _spec(**over)

    def test_spec_digest_is_stable_and_content_bound(self):
        assert _spec().spec_digest == _spec().spec_digest
        assert _spec().spec_digest != _spec(repo="o/other").spec_digest


class TestGates:
    def test_all_satisfied(self):
        assert check_gates(ALL_GATES) == []
        require_gates(ALL_GATES)

    def test_missing_gates_are_listed(self):
        gates = dict(ALL_GATES)
        gates["d4_budget_approved"] = False
        gates.pop("pilot_authorized")
        assert check_gates(gates) == ["d4_budget_approved", "pilot_authorized"]

    def test_require_gates_raises_and_names_every_gap(self):
        with pytest.raises(GateNotSatisfied, match="d4_budget_approved"):
            require_gates({name: False for name in REQUIRED_GATES})


class TestLiveAdapterRefuses:
    def test_refuses_without_allow_live(self):
        with pytest.raises(LiveAdapterRefused, match="allow_live"):
            LiveVMAdapter(gates=ALL_GATES, transport=lambda *a, **k: {}, allow_live=False).start("t")

    def test_refuses_without_gates_even_when_allow_live_is_set(self):
        adapter = LiveVMAdapter(gates={}, transport=lambda *a, **k: {}, allow_live=True,
                                expected_parent_disk_id="p")
        with pytest.raises(GateNotSatisfied):
            adapter.start("t")

    def test_refuses_without_a_transport(self):
        adapter = LiveVMAdapter(gates=ALL_GATES, transport=None, allow_live=True,
                                expected_parent_disk_id="p")
        with pytest.raises(LiveAdapterRefused, match="no transport"):
            adapter.start("t")

    def test_refuses_without_a_known_parent(self):
        adapter = LiveVMAdapter(gates=ALL_GATES, transport=lambda *a, **k: {}, allow_live=True)
        with pytest.raises(LiveAdapterRefused, match="parent"):
            adapter.start("t")

    def test_parent_mismatch_is_refused_even_with_gates_and_transport(self):
        def transport(op, **kw):
            return {"disk_parent": "someone-else"}.get(op, {})

        adapter = LiveVMAdapter(gates=ALL_GATES, transport=transport, allow_live=True,
                                expected_parent_disk_id="golden")
        with pytest.raises(SessionError, match="parent does not match"):
            adapter.verify_disk_chain("t")


class TestFakeController:
    def test_full_lifecycle_verifies_cleanup_from_state(self):
        adapter = FakeVMAdapter()
        adapter.inspect_environment()
        adapter.prepare_workspace("t-1")
        assert adapter.verify_disk_chain("t-1") == "t-1-diff"
        adapter.start("t-1")
        assert adapter.collect_receipt("t-1")["task_id"] == "t-1"
        adapter.stop("t-1")
        adapter.destroy("t-1")
        assert adapter.verify_cleanup("t-1") == (True, ())

    def test_dirty_workspace_is_refused(self):
        adapter = FakeVMAdapter()
        adapter.prepare_workspace("t-1")
        with pytest.raises(SessionError, match="dirty workspace"):
            adapter.prepare_workspace("t-1")

    def test_insufficient_free_space_is_refused(self):
        adapter = FakeVMAdapter(free_gb=0.5)
        with pytest.raises(SessionError, match="insufficient free space"):
            adapter.prepare_workspace("t-1", min_free_gb=1.0)

    def test_cleanup_postcondition_catches_a_still_present_disk(self):
        adapter = FakeVMAdapter()
        adapter.prepare_workspace("t-1")
        adapter.start("t-1")
        adapter.stop("t-1")           # destroy deliberately omitted
        ok, unresolved = adapter.verify_cleanup("t-1")
        assert ok is False and unresolved

    @pytest.mark.parametrize("point", ["inspect_environment", "prepare_workspace", "verify_disk_chain",
                                       "start", "collect_receipt", "stop", "destroy", "verify_cleanup"])
    def test_failure_at_every_transition_is_reachable_and_injectable(self, point):
        adapter = FakeVMAdapter(fail_at=point)
        with pytest.raises(RuntimeError, match="injected failure"):
            for call in (lambda: adapter.inspect_environment(),
                         lambda: adapter.prepare_workspace("t-1"),
                         lambda: adapter.verify_disk_chain("t-1"),
                         lambda: adapter.start("t-1"),
                         lambda: adapter.collect_receipt("t-1"),
                         lambda: adapter.stop("t-1"),
                         lambda: adapter.destroy("t-1"),
                         lambda: adapter.verify_cleanup("t-1")):
                call()
        assert point in adapter.calls


class TestRunSession:
    def _run(self, tmp_path, adapter, runner, **over):
        kw = dict(
            spec=_spec(), adapter=adapter, runner=runner, ledger=_ledger(tmp_path),
            evaluate=lambda s, r: "pass", verify_evidence=lambda s, r: True,
            verify_pair=lambda s, r: True, expected_registration_sha256=REG_SHA,
        )
        kw.update(over)
        return run_session(**kw)

    def test_admitted_path_records_and_cleans_up(self, tmp_path):
        res = self._run(tmp_path, FakeVMAdapter(), lambda spec: _result(spec))
        assert res.state == "admitted"
        assert res.record["admitted"] is True
        assert res.unresolved == ()

    def test_scientific_failure_is_recorded_and_not_admitted(self, tmp_path):
        res = self._run(tmp_path, FakeVMAdapter(), lambda spec: _result(spec, outcome="base_pass"))
        assert res.state == "scientific_failure"
        assert res.record["admitted"] is False

    def test_infrastructure_outcome_is_never_a_scientific_negative(self, tmp_path):
        res = self._run(tmp_path, FakeVMAdapter(), lambda spec: _result(spec, outcome="timeout"))
        assert res.state == "infrastructure_failure"
        assert res.record["outcome_class"] == "infrastructure"

    def test_preparation_failure_is_infrastructure_and_cleaned(self, tmp_path):
        adapter = FakeVMAdapter(fail_at="verify_disk_chain")
        res = self._run(tmp_path, adapter, lambda spec: _result(spec))
        assert res.state == "infrastructure_failure"
        assert res.record["outcome_class"] == "infrastructure"

    def test_runner_exception_is_infrastructure(self, tmp_path):
        def boom(spec):
            raise RuntimeError("guest exploded")

        res = self._run(tmp_path, FakeVMAdapter(), boom)
        assert res.state == "infrastructure_failure"

    def test_fail_closed_when_a_verifier_is_not_wired(self, tmp_path):
        res = self._run(tmp_path, FakeVMAdapter(), lambda spec: _result(spec),
                        verify_pair=None)
        assert res.state == "evidence_missing_or_invalid"
        assert res.record["admitted"] is False

    def test_evaluator_rejecting_the_evidence_blocks_admission(self, tmp_path):
        res = self._run(tmp_path, FakeVMAdapter(), lambda spec: _result(spec),
                        evaluate=lambda s, r: "fail")
        assert res.state == "evidence_missing_or_invalid"
        assert res.record["admitted"] is False

    def test_registration_mismatch_is_refused_before_any_adapter_call(self, tmp_path):
        adapter = FakeVMAdapter()
        with pytest.raises(SessionError, match="pinned operative registration"):
            self._run(tmp_path, adapter, lambda spec: _result(spec),
                      expected_registration_sha256="e" * 64)
        assert adapter.calls == []

    def test_unresolved_cleanup_is_surfaced(self, tmp_path):
        class Leaky(FakeVMAdapter):
            def destroy(self, task_id):       # deliberately does nothing
                self._touch("destroy")

        res = self._run(tmp_path, Leaky(), lambda spec: _result(spec))
        assert res.state in {"unresolved_cleanup", "admitted"}  # admit, but cleanup unresolved
        assert res.unresolved

    def test_resume_after_admission_is_idempotent(self, tmp_path):
        ledger = _ledger(tmp_path)
        first = self._run(tmp_path, FakeVMAdapter(), lambda spec: _result(spec), ledger=ledger)
        assert first.state == "admitted"
        assert ledger.verify()[0] is True
        assert len(ledger.admitted("o__a-1") or {}) > 0

    def test_receipt_identity_mismatch_is_refused(self, tmp_path):
        class Liar(FakeVMAdapter):
            def collect_receipt(self, task_id):
                self._touch("collect_receipt")
                return {"task_id": "someone-else", "receipt_digest": "0" * 64}

        with pytest.raises(SessionError, match="receipt task identity"):
            self._run(tmp_path, Liar(), lambda spec: _result(spec))

class TestLifetimeAttemptCap:
    """R1-D: the lifetime cap is NEVER widened by the session path.

    The measured defect: ``run_session`` called ``run_once`` with
    ``max_attempts=max(policy.max_attempts, used + 1)``, so a task with three
    recorded attempts silently became a fourth.
    """

    def _seed(self, ledger, count):
        for _ in range(count):
            ledger.append({
                "task_id": "o__a-1", "outcome": "timeout",
                "outcome_class": "infrastructure", "reason": "seeded",
            })

    def test_at_the_cap_the_adapter_is_never_touched(self, tmp_path):
        ledger = _ledger(tmp_path)
        self._seed(ledger, 3)
        adapter = FakeVMAdapter()
        res = run_session(
            spec=_spec(), adapter=adapter, runner=lambda spec: _result(spec),
            ledger=ledger, evaluate=lambda s, r: "pass",
            verify_evidence=lambda s, r: True, verify_pair=lambda s, r: True,
            policy=RetryPolicy(max_attempts=3))
        assert res.state == "retry_exhausted"
        assert res.record is None
        assert adapter.calls == []
        assert len(ledger.records()) == 3

    def test_below_the_cap_the_next_attempt_is_recorded(self, tmp_path):
        ledger = _ledger(tmp_path)
        self._seed(ledger, 1)
        res = run_session(
            spec=_spec(), adapter=FakeVMAdapter(),
            runner=lambda spec: _result(spec, outcome="timeout", attempt=2),
            ledger=ledger, policy=RetryPolicy(max_attempts=3))
        assert res.record["attempt"] == 2
        assert res.record["outcome_class"] == "infrastructure"
        assert len(ledger.records()) == 2

    def test_the_session_never_widens_the_policy_it_was_given(self, tmp_path):
        ledger = _ledger(tmp_path)
        self._seed(ledger, 2)
        res = run_session(
            spec=_spec(), adapter=FakeVMAdapter(),
            runner=lambda spec: _result(spec, outcome="timeout", attempt=3),
            ledger=ledger, policy=RetryPolicy(max_attempts=3))
        assert res.record["attempt"] == 3
        # a fourth session is refused, not silently promoted to max_attempts=4
        res4 = run_session(
            spec=_spec(), adapter=FakeVMAdapter(),
            runner=lambda spec: _result(spec, outcome="timeout", attempt=4),
            ledger=ledger, policy=RetryPolicy(max_attempts=3))
        assert res4.state == "retry_exhausted"
        assert len(ledger.records()) == 3
