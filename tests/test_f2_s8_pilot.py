"""Tests for the S8 feasibility-pilot driver.

The guest channel is injected, so no VM, credential, network or subprocess is
touched.  What is REAL here: the evaluator, the evidence verifier, the
governance verifier and the append-only ledger.  A task is admitted only when
genuine evaluator reports for both sides survive all three.

The properties under test are the honesty ones the pilot depends on: a side
that did not run is never admitted, a digest mismatch is never admitted, the
gates are checked before anything executes, and the six counts never merge.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from qwen_train import f2_evaluator as evaluator
from qwen_train import f2_governance as governance
from qwen_train.f2_s8_compose import GovernanceInputs
from qwen_train.f2_s8_factory import AppendOnlyLedger
from qwen_train.f2_s8_pilot import (
    GuestChannel,
    PilotError,
    build_spec_for_task,
    pilot_totals,
    run_pilot_task,
)
from qwen_train.f2_s8_record import encode_record
from qwen_train.f2_s8_session import REQUIRED_GATES
from qwen_train.f2_s8_transport import build_result_payload

REG_SHA = "c" * 64
GATES = {name: True for name in REQUIRED_GATES}

JUNIT_BASE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="pytest" tests="2" failures="1">\n'
    '<testcase classname="t.test_x" name="test_a" time="0.1">'
    '<failure message="boom">trace</failure></testcase>\n'
    '<testcase classname="t.test_x" name="test_b" time="0.1"/>\n'
    "</testsuite></testsuites>\n"
)
JUNIT_GOLD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuites><testsuite name="pytest" tests="2" failures="0">\n'
    '<testcase classname="t.test_x" name="test_a" time="0.1"/>\n'
    '<testcase classname="t.test_x" name="test_b" time="0.1"/>\n'
    "</testsuite></testsuites>\n"
)


def _spec(task_id="o__a-1"):
    return build_spec_for_task(
        task_id=task_id, source="swe-bench-live", repo="o/a", base_commit="f" * 40,
        test_cmd="pytest -q", fail_to_pass=["t/test_x.py::test_a"],
        pass_to_pass=["t/test_x.py::test_b"], registration_id="reg",
        registration_sha256=REG_SHA,
    )


def _inputs(root: Path) -> GovernanceInputs:
    impl = Path(evaluator.__file__).resolve().read_bytes()
    auth = governance.EvaluatorAuthorization(
        evaluator_id=evaluator.EVALUATOR_ID, version=evaluator.EVALUATOR_VERSION,
        implementation_digest=hashlib.sha256(impl).hexdigest(),
        procedure_id=evaluator.PROCEDURE_ID,
        protocol_version=evaluator.PROTOCOL_VERSION,
    )
    return GovernanceInputs(
        store=governance.TrustedArtifactStore(root / "store", governance.RetentionPolicy("r", 30, True)),
        registry=governance.EvaluatorRegistry([auth]),
        evaluator=auth,
        environment_identity="s8-guest-env",
        base_state_digest="a" * 64,
        gold_state_digest="b" * 64,
        implementation_name="f2_evaluator.py",
        implementation_bytes=impl,
    )


class FakeChannel(GuestChannel):
    """Serves real JUnit bytes and a REAL transport envelope.

    The driver validates the result envelope through
    ``f2_s8_channel.read_channel_file``, which refuses anything that is not a
    well-formed ``F2S8REC1`` record.  A fixture that wrote raw XML there would
    only prove the transport rejects junk, so this channel builds genuine
    envelopes with the same ``build_result_payload``/``encode_record`` the real
    guest executor uses.
    """

    def __init__(self, *, base=JUNIT_BASE, gold=JUNIT_GOLD, fail_at=None,
                 task_id="o__a-1"):
        self.base, self.gold, self.fail_at = base, gold, fail_at
        self.task_id = task_id
        self.commands: list[str] = []

    def put(self, local, remote):  # pragma: no cover - not used by the driver
        return None

    def get(self, remote, local):
        local = Path(local)
        remote_name = Path(str(remote)).name
        local.parent.mkdir(parents=True, exist_ok=True)
        # Dispatch on the BASENAME only. Detecting the side as a substring of
        # the full path is wrong: pytest names this test's tmp_path
        # "test_base_fail_gold_pass_is_ad0", so "base" appears in EVERY path
        # beneath it and the gold side silently received the base artifact.
        side = "gold" if ".gold." in remote_name or remote_name.endswith("_gold.rec") else "base"
        if remote_name.endswith(".rec"):
            if self.fail_at == "get_missing":
                return None
            xml = self.base if side == "base" else self.gold
            data = xml.encode("utf-8")
            payload = build_result_payload(
                task_id=self.task_id, job_id=f"{self.task_id}#{side}",
                source="swe-bench-live",
                provenance={"producer": "fake_guest", "side": side},
                artifact_name=f"{self.task_id}.{side}.junit.xml",
                artifact_sha256=hashlib.sha256(data).hexdigest(),
                artifact_size=len(data), created_at="t0",
            )
            local.write_bytes(encode_record(payload))
        else:
            if self.fail_at == "get_missing":
                return None
            # Write BYTES, not text: Path.write_text translates \n to \r\n on
            # Windows, so the retrieved artifact would not hash to the digest
            # the envelope reported. The driver re-hashes the retrieved bytes,
            # which is exactly the check that caught this.
            local.write_bytes((self.base if side == "base" else self.gold).encode("utf-8"))

    def run(self, command):
        self.commands.append(command)
        if self.fail_at == "run":
            return 1, "", "boom"
        return 0, "", ""


def _write_spec_and_request(root: Path, spec, channel: FakeChannel):
    """Stage the spec + a transport-valid request the guest executor expects."""
    from qwen_train.f2_s8_runner import publish_atomic

    spec_path = root / "spec.json"
    publish_atomic(spec_path, json.dumps(spec.to_dict(), sort_keys=True,
                                        separators=(",", ":")).encode())
    return spec_path


class TestHonestAdmission:
    def test_base_fail_gold_pass_is_admitted(self, tmp_path):
        spec = _spec()
        channel = FakeChannel()
        ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
        result = run_pilot_task(
            spec=spec, channel=channel, inputs=_inputs(tmp_path), ledger=ledger,
            gates=GATES, guest_dir=str(tmp_path), registration_sha256=REG_SHA,
            job_nonce="t1",
        )
        assert result.admitted is True
        assert result.outcome == "base_fail_gold_pass"
        assert result.outcome_class == "scientific"
        assert len(result.base_junit_sha256) == 64
        assert len(result.gold_junit_sha256) == 64
        assert result.base_junit_sha256 != result.gold_junit_sha256
        ok, why = ledger.verify()
        assert ok, why

    def test_gold_that_still_fails_is_not_admitted(self, tmp_path):
        spec = _spec()
        channel = FakeChannel(gold=JUNIT_BASE)  # gold did not fix the test
        result = run_pilot_task(
            spec=spec, channel=channel, inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t2",
        )
        assert result.admitted is False
        assert result.outcome == "gold_fail"
        assert result.outcome_class == "scientific"

    def test_base_that_already_passes_is_not_admitted(self, tmp_path):
        spec = _spec()
        channel = FakeChannel(base=JUNIT_GOLD)
        result = run_pilot_task(
            spec=spec, channel=channel, inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t3",
        )
        assert result.admitted is False
        assert result.outcome == "base_pass"

    def test_malformed_junit_is_never_admitted(self, tmp_path):
        spec = _spec()
        channel = FakeChannel(base="not xml at all", gold=JUNIT_GOLD)
        result = run_pilot_task(
            spec=spec, channel=channel, inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t4",
        )
        assert result.admitted is False
        assert result.failure_class.startswith("evaluator_refused_base")
        assert result.outcome_class == "infrastructure"

    def test_declared_test_must_actually_be_in_the_junit(self, tmp_path):
        """A JUnit that never ran the FAIL_TO_PASS node cannot pass: the node
        reads ``missing``, not ``passed``."""
        spec = _spec()
        other = (
            '<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="1">'
            '<testcase classname="t.test_y" name="test_z" time="0.1"/>'
            "</testsuite></testsuites>"
        )
        channel = FakeChannel(base=other, gold=other)
        result = run_pilot_task(
            spec=spec, channel=channel, inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t5",
        )
        assert result.admitted is False


class TestFailClosed:
    def test_unmet_gate_refuses_before_any_execution(self, tmp_path):
        spec = _spec()
        channel = FakeChannel()
        with pytest.raises(PilotError, match="gates unmet"):
            run_pilot_task(
                spec=spec, channel=channel, inputs=_inputs(tmp_path),
                ledger=AppendOnlyLedger(tmp_path / "l.jsonl"),
                gates={**GATES, "pilot_authorized": False},
                guest_dir=str(tmp_path), registration_sha256=REG_SHA,
            )
        assert channel.commands == []

    def test_wrong_registration_is_refused(self, tmp_path):
        with pytest.raises(PilotError, match="pinned operative registration"):
            run_pilot_task(
                spec=_spec(), channel=FakeChannel(), inputs=_inputs(tmp_path),
                ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
                guest_dir=str(tmp_path), registration_sha256="d" * 64,
            )

    def test_guest_failure_is_infrastructure_not_scientific(self, tmp_path):
        result = run_pilot_task(
            spec=_spec(), channel=FakeChannel(fail_at="run"), inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t6",
        )
        assert result.admitted is False
        assert result.outcome_class == "infrastructure"
        assert result.failure_class == "guest_exit_1"

    def test_unauthorized_evaluator_never_admits(self, tmp_path):
        impl = Path(evaluator.__file__).resolve().read_bytes()
        # The assembly side uses the genuine authorized evaluator, but the
        # registry is provisioned with a DIFFERENT implementation digest: the
        # governance registry must reject the run's evaluator identity. Setting
        # inputs.evaluator to the rogue too would only prove the run agrees with
        # itself, which is exactly the substitution this check exists to catch.
        rogue_registry = governance.EvaluatorAuthorization(
            evaluator_id=evaluator.EVALUATOR_ID, version=evaluator.EVALUATOR_VERSION,
            implementation_digest="e" * 64, procedure_id=evaluator.PROCEDURE_ID,
            protocol_version=evaluator.PROTOCOL_VERSION,
        )
        genuine = governance.EvaluatorAuthorization(
            evaluator_id=evaluator.EVALUATOR_ID, version=evaluator.EVALUATOR_VERSION,
            implementation_digest=hashlib.sha256(impl).hexdigest(),
            procedure_id=evaluator.PROCEDURE_ID,
            protocol_version=evaluator.PROTOCOL_VERSION,
        )
        inputs = GovernanceInputs(
            store=governance.TrustedArtifactStore(
                tmp_path / "s", governance.RetentionPolicy("r", 30, True)
            ),
            registry=governance.EvaluatorRegistry([rogue_registry]),
            evaluator=genuine, environment_identity="e",
            base_state_digest="a" * 64, gold_state_digest="b" * 64,
            implementation_name="f2_evaluator.py", implementation_bytes=impl,
        )
        result = run_pilot_task(
            spec=_spec(), channel=FakeChannel(), inputs=inputs,
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t7",
        )
        assert result.admitted is False
        assert result.outcome == "test_not_run"
        assert result.outcome_class == "scientific"

    def test_tampered_artifact_is_refused(self, tmp_path):
        """The artifact digest is re-checked against the retrieved bytes; a
        mismatch stops the task before any verdict is derived."""
        class Tampering(FakeChannel):
            def get(self, remote, local):
                super().get(remote, local)
                if "junit" in str(remote):
                    Path(local).write_text(JUNIT_GOLD, encoding="utf-8")

        result = run_pilot_task(
            spec=_spec(), channel=Tampering(base=JUNIT_BASE), inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t8",
        )
        assert result.admitted is False
        assert result.failure_class in ("artifact_digest_mismatch", "no_artifact_reported")


class TestCountsStaySeparate:
    def test_totals_never_merge(self, tmp_path):
        from qwen_train.f2_s8_pilot import PilotTaskResult

        results = [
            PilotTaskResult(task_id="a", admitted=True, outcome="base_fail_gold_pass",
                            outcome_class="scientific"),
            PilotTaskResult(task_id="b", admitted=False, outcome="gold_fail",
                            outcome_class="scientific"),
            PilotTaskResult(task_id="c", admitted=False, outcome="infrastructure_failure",
                            outcome_class="infrastructure", failure_class="guest_exit_1"),
            PilotTaskResult(task_id="d", admitted=False, outcome="",
                            outcome_class="", failure_class="transport_rejected"),
        ]
        totals = pilot_totals(results)
        assert totals == {
            "attempted": 4, "admitted": 1, "completed": 2,
            "infrastructure_invalid": 2, "failed": 1, "analyzable": 0,
        }

    def test_analyzable_is_always_zero(self, tmp_path):
        from qwen_train.f2_s8_pilot import PilotTaskResult
        results = [PilotTaskResult(task_id="a", admitted=True,
                                   outcome="base_fail_gold_pass",
                                   outcome_class="scientific")]
        assert pilot_totals(results)["analyzable"] == 0

    def test_a_ledger_record_is_retained_per_task(self, tmp_path):
        spec = _spec()
        ledger = AppendOnlyLedger(tmp_path / "l.jsonl")
        result = run_pilot_task(
            spec=spec, channel=FakeChannel(), inputs=_inputs(tmp_path), ledger=ledger,
            gates=GATES, guest_dir=str(tmp_path), registration_sha256=REG_SHA,
            job_nonce="t9",
        )
        records = ledger.records()
        assert len(records) == 1
        assert records[0]["task_id"] == spec.task_id
        assert records[0]["admitted"] is True
        assert result.ledger_record["record_digest"] == records[0]["record_digest"]


class TestSpecValidation:
    def test_spec_carries_the_evaluator_identity(self):
        spec = _spec()
        assert spec.payload["evaluator"]["evaluator_id"] == evaluator.EVALUATOR_ID
        assert spec.payload["evaluator"]["implementation_digest"] == evaluator.implementation_digest()

    def test_spec_refuses_a_junitxml_carrying_command(self):
        from qwen_train.f2_s8_session import SessionError
        with pytest.raises(SessionError, match="junitxml"):
            build_spec_for_task(
                task_id="x", source="s", repo="r", base_commit="f" * 40,
                test_cmd="pytest --junitxml=foo.xml", fail_to_pass=["a::b"],
                pass_to_pass=[], registration_id="reg", registration_sha256=REG_SHA,
            )

    def test_spec_refuses_an_empty_fail_to_pass(self):
        from qwen_train.f2_s8_session import SessionError
        with pytest.raises(SessionError, match="FAIL_TO_PASS"):
            build_spec_for_task(
                task_id="x", source="s", repo="r", base_commit="f" * 40,
                test_cmd="pytest -q", fail_to_pass=[], pass_to_pass=[],
                registration_id="reg", registration_sha256=REG_SHA,
            )
