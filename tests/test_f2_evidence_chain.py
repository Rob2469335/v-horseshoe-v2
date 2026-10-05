"""End-to-end evidence-chain integration for the authoritative F2 evaluator.

These tests close the loop the repository previously could not close:

    retained pytest/JUnit evidence
        -> qwen_train.f2_evaluator.produce_report   (the PRODUCER)
        -> canonical deterministic report bytes
        -> f2_governance registered protocol re-derives the verdict
        -> ExecutionBundle verifies under the frozen governance chain

They also pin the worker's producer seam, which previously required an
out-of-band report generator that did not exist.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train import f2_evaluator as ev
from qwen_train import f2_evidence as evi
from qwen_train import f2_governance as gov
from qwen_train.f2_governance import (
    ROLE_RUN_LOG,
    ROLE_TEST_OUTPUT,
    ArtifactRef,
    EvaluatorAuthorization,
    EvaluatorRegistry,
    ExecutionBundle,
    RetentionPolicy,
    TrustedArtifactStore,
    build_execution_identity,
)

JUNIT_PASS = (
    b'<?xml version="1.0" encoding="utf-8"?><testsuites>'
    b'<testcase classname="pkg.mod" name="test_alpha"/>'
    b'<testcase classname="pkg.mod" name="test_beta"/>'
    b"</testsuites>"
)
JUNIT_FAIL = (
    b'<?xml version="1.0" encoding="utf-8"?><testsuites>'
    b'<testcase classname="pkg.mod" name="test_alpha"/>'
    b'<testcase classname="pkg.mod" name="test_beta"><failure message="m"/></testcase>'
    b"</testsuites>"
)
F2P = ["pkg/mod.py::test_alpha", "pkg/mod.py::test_beta"]
TASK = "inst-1"
REPO = "o/r"
BASE = "c0ffee"


def _auth() -> EvaluatorAuthorization:
    return EvaluatorAuthorization(
        evaluator_id=ev.EVALUATOR_ID,
        version=ev.EVALUATOR_VERSION,
        implementation_digest=ev.implementation_digest(),
        procedure_id=ev.PROCEDURE_ID,
        protocol_version=ev.PROTOCOL_VERSION,
    )


def _store(root: Path) -> TrustedArtifactStore:
    return TrustedArtifactStore(
        root=root, retention=RetentionPolicy("f2_evidence_retention", 90, True)
    )


def _write(root: Path, name: str, data: bytes, role: str) -> ArtifactRef:
    (root / name).write_bytes(data)
    return ArtifactRef(
        name=name, digest=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data), role=role,
    )


def _report_bytes(tmp_path: Path, state: str, junit_bytes: bytes) -> bytes:
    junit = tmp_path / f"{state}-junit.xml"
    junit.write_bytes(junit_bytes)
    out = tmp_path / f"{state}-outcome.json"
    ev.produce_report(
        junit_path=junit,
        fail_to_pass=F2P,
        out_path=out,
        instance_id=TASK,
        repository=REPO,
        base_commit=BASE,
        execution_state_identity=state,
    )
    return out.read_bytes()


def _bundle(root: Path, state: str, declared: str, report: bytes,
             implementation: bytes | None = None) -> ExecutionBundle:
    out = _write(root, f"{state}-outcome.json", report, ROLE_TEST_OUTPUT)
    log = _write(root, f"{state}-run.log", f"{state} run log\n".encode(), ROLE_RUN_LOG)
    ident = build_execution_identity(
        instance_id=TASK,
        repository=REPO,
        base_commit=BASE,
        execution_state_identity=state,
        execution_state_digest=hashlib.sha256(state.encode()).hexdigest(),
        evaluator=_auth(),
        environment_identity="test-env",
        test_command="pytest -q",
        test_output=out,
        run_log=log,
    )
    return ExecutionBundle(
        identity=ident,
        declared_result=declared,
        result_protocol_id=ev.RESULT_PROTOCOL_ID,
        test_output=out,
        run_log=log,
        started_at="2026-10-04T00:00:00Z",
        finished_at="2026-10-04T00:01:00Z",
        implementation_artifact=(
            _write(root, f"{state}-impl.py", implementation,
                   gov.ROLE_EVALUATOR_IMPLEMENTATION)
            if implementation is not None else None
        ),
    )


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    d = tmp_path / "store"
    d.mkdir()
    return d


class TestProducerToVerifierChain:
    def test_gold_chain_verifies_pass(self, root):
        report = _report_bytes(root, "gold", JUNIT_PASS)
        store = _store(root)
        b = _bundle(root, "gold", "pass", report)
        derived, why = gov.derive_result(b, store)
        assert derived == "pass", why

    def test_base_chain_verifies_fail(self, root):
        report = _report_bytes(root, "base", JUNIT_FAIL)
        store = _store(root)
        b = _bundle(root, "base", "fail", report)
        derived, why = gov.derive_result(b, store)
        assert derived == "fail", why

    def test_declared_pass_but_evidence_fails_is_rejected(self, root):
        """A lying producer is caught: the derived result wins over the claim."""
        report = _report_bytes(root, "gold", JUNIT_FAIL)
        store = _store(root)
        b = _bundle(root, "gold", "pass", report)
        derived, why = gov.derive_result(b, store)
        assert derived == "fail"
        assert "not passed" in why

    def test_full_bundle_verification_end_to_end(self, root):
        report = _report_bytes(root, "gold", JUNIT_PASS)
        store = _store(root)
        b = _bundle(root, "gold", "pass", report)
        v = gov.verify_execution_bundle(b, store=store, registry=EvaluatorRegistry([_auth()]))
        assert v.state == evi.STATE_VERIFIED, f"{v.state}: {v.detail}"
        assert v.integrity_ok is True
        assert v.provenance_ok is True
        # scientifically_sufficient is a SEPARATE axis from verification and is
        # deliberately not asserted here: proving an outcome is
        # scientifically sufficient is a stronger claim than proving its
        # evidence is intact and authorised.
        assert v._proof is not None

    def test_governed_pair_base_fail_gold_pass(self, root):
        base = _bundle(root, "base", "fail", _report_bytes(root, "base", JUNIT_FAIL))
        gold = _bundle(root, "gold", "pass", _report_bytes(root, "gold", JUNIT_PASS))
        store = _store(root)
        v = gov.verify_governed_pair(
            base, gold, store=store, registry=EvaluatorRegistry([_auth()]),
            task_id=TASK, repository=REPO, base_commit=BASE,
        )
        assert v.state == evi.STATE_VERIFIED, f"{v.state}: {v.detail}"

    def test_governed_pair_rejects_gold_that_does_not_pass(self, root):
        base = _bundle(root, "base", "fail", _report_bytes(root, "base", JUNIT_FAIL))
        gold = _bundle(root, "gold", "pass", _report_bytes(root, "gold", JUNIT_FAIL))
        store = _store(root)
        v = gov.verify_governed_pair(
            base, gold, store=store, registry=EvaluatorRegistry([_auth()]),
            task_id=TASK, repository=REPO, base_commit=BASE,
        )
        assert v.state != evi.STATE_VERIFIED

    def test_verification_fails_closed_without_authorization(self, root):
        report = _report_bytes(root, "gold", JUNIT_PASS)
        store = _store(root)
        b = _bundle(root, "gold", "pass", report)
        v = gov.verify_execution_bundle(b, store=store, registry=EvaluatorRegistry([]))
        assert v.state != evi.STATE_VERIFIED

    def test_verification_fails_closed_on_tampered_artifact(self, root):
        report = _report_bytes(root, "gold", JUNIT_PASS)
        b = _bundle(root, "gold", "pass", report)
        store = _store(root)
        (root / "gold-outcome.json").write_bytes(b'{"schema":"forged"}')
        v = gov.verify_execution_bundle(b, store=store, registry=EvaluatorRegistry([_auth()]))
        assert v.state != evi.STATE_VERIFIED

    def test_verification_fails_closed_on_missing_artifact(self, root):
        report = _report_bytes(root, "gold", JUNIT_PASS)
        b = _bundle(root, "gold", "pass", report)
        store = _store(root)
        (root / "gold-run.log").unlink()
        v = gov.verify_execution_bundle(b, store=store, registry=EvaluatorRegistry([_auth()]))
        assert v.state != evi.STATE_VERIFIED

    def test_evaluator_bytes_provable_when_retained(self, root):
        report = _report_bytes(root, "gold", JUNIT_PASS)
        b = _bundle(root, "gold", "pass", report,
                    implementation=Path(ev.__file__).read_bytes())
        assert gov.evaluator_bytes_proven(b, _store(root)) is True

    def test_evaluator_bytes_not_provable_when_absent(self, root):
        report = _report_bytes(root, "gold", JUNIT_PASS)
        b = _bundle(root, "gold", "pass", report)
        assert gov.evaluator_bytes_proven(b, _store(root)) is False

    def test_result_protocol_is_registered(self):
        assert ev.RESULT_PROTOCOL_ID in gov.registered_result_protocols()

    def test_authorization_record_matches_the_evaluator(self):
        a = _auth()
        assert a.evaluator_id == ev.EVALUATOR_ID
        assert a.version == ev.EVALUATOR_VERSION
        assert a.implementation_digest == ev.implementation_digest()
        assert a.procedure_id == ev.PROCEDURE_ID
        assert a.protocol_version == "f2_experiment_j_v1"


class TestWorkerProducerSeam:
    def _env(self, tmp_path, junit: bytes | None, contract: dict | None):
        env = {}
        if junit is not None:
            j = tmp_path / "junit.xml"
            j.write_bytes(junit)
            env["SWARM_F2_JUNIT_EVIDENCE"] = str(j)
        if contract is not None:
            c = tmp_path / "contract.json"
            c.write_text(json.dumps(contract), encoding="utf-8")
            env["SWARM_F2_TASK_CONTRACT"] = str(c)
        return env

    def test_produces_report_from_evidence(self, tmp_path, monkeypatch):
        from qwen_train import f2_arm_worker as w

        out = tmp_path / "outcome.json"
        for k, v in self._env(tmp_path, JUNIT_PASS, {"fail_to_pass": F2P}).items():
            monkeypatch.setenv(k, v)
        assert w._produce_outcome_report_from_evidence(out) == out
        assert ev.parse_report(out.read_bytes())["declared_result"] == "pass"

    def test_failing_evidence_yields_fail_not_crash(self, tmp_path, monkeypatch):
        from qwen_train import f2_arm_worker as w

        out = tmp_path / "outcome.json"
        for k, v in self._env(tmp_path, JUNIT_FAIL, {"fail_to_pass": F2P}).items():
            monkeypatch.setenv(k, v)
        w._produce_outcome_report_from_evidence(out)
        assert ev.parse_report(out.read_bytes())["declared_result"] == "fail"

    def test_missing_inputs_names_them(self, tmp_path, monkeypatch):
        from qwen_train import f2_arm_worker as w

        monkeypatch.delenv("SWARM_F2_JUNIT_EVIDENCE", raising=False)
        monkeypatch.delenv("SWARM_F2_TASK_CONTRACT", raising=False)
        with pytest.raises(w.FreezeVerificationError) as ei:
            w._produce_outcome_report_from_evidence(tmp_path / "o.json")
        assert "SWARM_F2_JUNIT_EVIDENCE" in str(ei.value)
        assert "SWARM_F2_TASK_CONTRACT" in str(ei.value)

    def test_malformed_evidence_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train import f2_arm_worker as w

        for k, v in self._env(tmp_path, b"<testsuites><broken", {"fail_to_pass": F2P}).items():
            monkeypatch.setenv(k, v)
        with pytest.raises(w.FreezeVerificationError) as ei:
            w._produce_outcome_report_from_evidence(tmp_path / "o.json")
        assert "could not produce" in str(ei.value)
        assert not (tmp_path / "o.json").exists()

    def test_empty_contract_fails_closed(self, tmp_path, monkeypatch):
        from qwen_train import f2_arm_worker as w

        for k, v in self._env(tmp_path, JUNIT_PASS, {"fail_to_pass": []}).items():
            monkeypatch.setenv(k, v)
        with pytest.raises(w.FreezeVerificationError):
            w._produce_outcome_report_from_evidence(tmp_path / "o.json")
