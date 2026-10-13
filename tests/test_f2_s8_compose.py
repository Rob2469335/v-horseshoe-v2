"""Integration tests for the S8 production composition root.

These exercise the *real* evaluator, evidence and governance modules end to end.
Only the guest execution is simulated: the ``execute`` seam returns bytes
produced by the authoritative evaluator itself, so the evidence that flows
through the governed verifiers is genuine, not a stand-in for the contract.

They do NOT prove the real VM execution path: no VM, disk, network, credential
or subprocess is touched.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from qwen_train import f2_evaluator as evaluator
from qwen_train import f2_governance as governance
from qwen_train.f2_s8_compose import (
    GovernanceInputs,
    GuestEvidence,
    S8ComposeError,
    assemble_governed_bundle,
    load_governance_from_env,
    produce_governed_task,
)
from qwen_train.f2_s8_factory import AppendOnlyLedger
from qwen_train.f2_s8_session import REQUIRED_GATES, FakeVMAdapter, build_task_spec

EVAL = evaluator.identity_fields()
REG_SHA = "c" * 64

JUNIT_BASE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="pytest" tests="2" failures="1">\n'
    '<testcase classname="t.test_x" name="test_a" time="0.1">'
    '<failure message="boom">trace</failure></testcase>\n'
    '<testcase classname="t.test_x" name="test_b" time="0.1"/>\n'
    "</testsuite></testsuites>\n"
)
JUNIT_GOLD = JUNIT_BASE.replace(
    '<testcase classname="t.test_x" name="test_a" time="0.1">'
    '<failure message="boom">trace</failure></testcase>',
    '<testcase classname="t.test_x" name="test_a" time="0.1"/>',
).replace('failures="1"', 'failures="0"')


def _spec(**over):
    kw = dict(
        task_id="o__a-1", source="swe-bench-live", repo="o/a", base_commit="f" * 40,
        test_cmd="pytest -q", fail_to_pass=["t/test_x.py::test_a"],
        pass_to_pass=["t/test_x.py::test_b"], registration_id="reg",
        registration_sha256=REG_SHA, evaluator=EVAL,
    )
    kw.update(over)
    return build_task_spec(**kw)


def _impl_bytes() -> bytes:
    return Path(evaluator.__file__).resolve().read_bytes()


def _auth(impl: bytes | None = None) -> governance.EvaluatorAuthorization:
    return governance.EvaluatorAuthorization(
        evaluator_id=EVAL["evaluator_id"], version=EVAL["version"],
        implementation_digest=hashlib.sha256(impl if impl is not None else _impl_bytes()).hexdigest(),
        procedure_id=EVAL["procedure_id"], protocol_version=EVAL["protocol_version"],
    )


def _inputs(root: Path, *, auth=None) -> GovernanceInputs:
    impl = _impl_bytes()
    return GovernanceInputs(
        store=governance.TrustedArtifactStore(
            root / "store", governance.RetentionPolicy("r", 30, True)
        ),
        registry=governance.EvaluatorRegistry([auth or _auth(impl)]),
        evaluator=auth or _auth(impl),
        environment_identity="test-env",
        base_state_digest="a" * 64,
        gold_state_digest="b" * 64,
        implementation_name="f2_evaluator.py",
        implementation_bytes=impl,
    )


def _report_bytes(root: Path, spec, side: str, xml: str) -> bytes:
    junit = root / f"{side}.junit.xml"
    junit.write_text(xml, encoding="utf-8")
    out = root / f"{side}.report.json"
    evaluator.produce_report(
        junit_path=junit, fail_to_pass=list(spec.payload["fail_to_pass"]),
        pass_to_pass=list(spec.payload["pass_to_pass"]), out_path=out,
        instance_id=spec.task_id, repository=str(spec.payload["repo"]),
        base_commit=str(spec.payload["base_commit"]), execution_state_identity=side,
    )
    return out.read_bytes()


def _execute(root: Path, spec, *, base_xml=JUNIT_BASE, gold_xml=JUNIT_GOLD, fail_side=None):
    def execute(_spec, side):
        if side == fail_side:
            return GuestEvidence(report=b"", run_log=b"", started_at="t0",
                                 finished_at="t1", failure_class="timeout")
        xml = base_xml if side == "base" else gold_xml
        return GuestEvidence(
            report=_report_bytes(root, spec, side, xml),
            run_log=f"{side} log".encode("utf-8"),
            started_at="t0", finished_at="t1",
        )
    return execute


def _run(root, spec, inputs, execute, ts="T"):
    return produce_governed_task(
        spec=spec, gates={n: True for n in REQUIRED_GATES}, adapter=FakeVMAdapter(),
        ledger=AppendOnlyLedger(root / "ledger.jsonl"), inputs=inputs, execute=execute,
        expected_registration_sha256=REG_SHA, ts=ts,
    )


class TestFullPipeline:
    def test_base_fail_gold_pass_is_admitted_through_real_verifiers(self, tmp_path):
        spec = _spec()
        res = _run(tmp_path, spec, _inputs(tmp_path), _execute(tmp_path, spec))
        assert res.state == "admitted"
        assert res.outcome == "base_fail_gold_pass"
        assert res.record["admitted"] is True
        ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
        assert ledger.verify() == (True, "ledger chain intact")

    def test_gold_fail_is_scientific_not_admitted(self, tmp_path):
        spec = _spec()
        res = _run(
            tmp_path, spec, _inputs(tmp_path),
            _execute(tmp_path, spec, gold_xml=JUNIT_BASE),
        )
        assert res.record["admitted"] is False
        assert res.state == "scientific_failure"
        assert res.outcome == "gold_fail"

    def test_base_pass_is_scientific_not_admitted(self, tmp_path):
        spec = _spec()
        res = _run(
            tmp_path, spec, _inputs(tmp_path),
            _execute(tmp_path, spec, base_xml=JUNIT_GOLD),
        )
        assert res.record["admitted"] is False
        assert res.outcome == "base_pass"

    def test_gates_are_checked_before_any_execution(self, tmp_path):
        spec = _spec()
        calls = []

        def execute(_spec, side):
            calls.append(side)
            raise AssertionError("must not execute before gates pass")

        with pytest.raises(Exception):
            produce_governed_task(
                spec=spec, gates={}, adapter=FakeVMAdapter(),
                ledger=AppendOnlyLedger(tmp_path / "l.jsonl"),
                inputs=_inputs(tmp_path), execute=execute,
                expected_registration_sha256=REG_SHA,
            )
        assert calls == []

    def test_wrong_registration_is_refused(self, tmp_path):
        spec = _spec()
        with pytest.raises(Exception):
            produce_governed_task(
                spec=spec, gates={n: True for n in REQUIRED_GATES},
                adapter=FakeVMAdapter(), ledger=AppendOnlyLedger(tmp_path / "l.jsonl"),
                inputs=_inputs(tmp_path), execute=_execute(tmp_path, spec),
                expected_registration_sha256="9" * 64,
            )


class TestFailClosed:
    def test_unauthorized_evaluator_never_admits(self, tmp_path):
        spec = _spec()
        impl = _impl_bytes()
        # The assembly side uses the real authorization, but the registry is
        # provisioned with a DIFFERENT authorized implementation: the governance
        # registry must reject the run's evaluator identity.
        forged_registry_auth = governance.EvaluatorAuthorization(
            evaluator_id=EVAL["evaluator_id"], version=EVAL["version"],
            implementation_digest="e" * 64, procedure_id=EVAL["procedure_id"],
            protocol_version=EVAL["protocol_version"],
        )
        inputs = GovernanceInputs(
            store=governance.TrustedArtifactStore(
                tmp_path / "store", governance.RetentionPolicy("r", 30, True)
            ),
            registry=governance.EvaluatorRegistry([forged_registry_auth]),
            evaluator=_auth(impl),
            environment_identity="test-env",
            base_state_digest="a" * 64, gold_state_digest="b" * 64,
            implementation_name="f2_evaluator.py", implementation_bytes=impl,
        )
        res = _run(tmp_path, spec, inputs, _execute(tmp_path, spec))
        assert res.record["admitted"] is False

    def test_infrastructure_failure_side_is_not_assemblable(self, tmp_path):
        spec = _spec()
        with pytest.raises(S8ComposeError, match="infrastructure failure"):
            assemble_governed_bundle(
                spec=spec, side="base",
                guest=GuestEvidence(report=b"", run_log=b"", started_at="", finished_at="",
                                    failure_class="timeout"),
                inputs=_inputs(tmp_path),
            )

    def test_tampered_report_is_refused(self, tmp_path):
        spec = _spec()
        report = _report_bytes(tmp_path, spec, "base", JUNIT_BASE)
        tampered = report.replace(b'"fail"', b'"pass"')
        with pytest.raises(S8ComposeError, match="authentic evaluator report"):
            assemble_governed_bundle(
                spec=spec, side="base",
                guest=GuestEvidence(report=tampered, run_log=b"x", started_at="", finished_at=""),
                inputs=_inputs(tmp_path),
            )

    def test_identical_state_digests_are_refused(self, tmp_path):
        with pytest.raises(S8ComposeError, match="identical"):
            GovernanceInputs(
                store=governance.TrustedArtifactStore(
                    tmp_path / "s", governance.RetentionPolicy("r", 1, True)
                ),
                registry=governance.EvaluatorRegistry([_auth()]),
                evaluator=_auth(), environment_identity="e",
                base_state_digest="a" * 64, gold_state_digest="a" * 64,
            )


class TestEnvironmentLoader:
    def test_missing_inputs_fail_closed_and_are_named(self):
        with pytest.raises(S8ComposeError) as exc:
            load_governance_from_env({})
        assert "SWARM_S8_ARTIFACT_ROOT" in str(exc.value)

    def test_complete_env_builds_inputs(self, tmp_path):
        env = {
            "SWARM_S8_ARTIFACT_ROOT": str(tmp_path / "store"),
            "SWARM_S8_ARTIFACT_RETENTION_DAYS": "30",
            "SWARM_S8_EVALUATOR_ID": EVAL["evaluator_id"],
            "SWARM_S8_EVALUATOR_VERSION": EVAL["version"],
            "SWARM_S8_EVALUATOR_IMPL": str(Path(evaluator.__file__).resolve()),
            "SWARM_S8_EVALUATOR_PROCEDURE": EVAL["procedure_id"],
            "SWARM_S8_ENVIRONMENT_IDENTITY": "env-1",
            "SWARM_S8_BASE_STATE_DIGEST": "a" * 64,
            "SWARM_S8_GOLD_STATE_DIGEST": "b" * 64,
        }
        inputs = load_governance_from_env(env)
        assert inputs.evaluator.implementation_digest == evaluator.implementation_digest()
        assert inputs.store.root == tmp_path / "store"
