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
    INFRASTRUCTURE_REASONS,
    GuestChannel,
    PilotError,
    build_spec_for_task,
    classify_side_failure,
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
                 task_id="o__a-1", artifact_name=None, artifact_sha256=None,
                 artifact_size=None):
        self.base, self.gold, self.fail_at = base, gold, fail_at
        self.task_id = task_id
        self.commands: list[str] = []
        self._side = "base"
        #: Every remote path the driver asked for, so a test can prove which
        #: filename it attempted (not merely that the run happened to succeed).
        self.requested: list[str] = []
        self.artifact_name = artifact_name
        self.artifact_sha256 = artifact_sha256
        self.artifact_size = artifact_size

    @property
    def _reported_name(self) -> str:
        """The name the guest reports. An explicit ``""`` must stay empty so the
        driver's refusal of an unusable name can be exercised."""
        if self.artifact_name is None:
            return f"{self.task_id}.junit.xml"
        return self.artifact_name

    def put(self, local, remote):  # pragma: no cover - not used by the driver
        return None

    def get(self, remote, local):
        local = Path(local)
        remote_name = Path(str(remote)).name
        self.requested.append(str(remote))
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
                provenance={"producer": "fake_guest", "side": side,
                            "junit_present": True, "exit_status": 1},
                # REAL guest behaviour: ``f2_s8_guest`` has no side argument and
                # names every artifact "<task_id>.junit.xml". A fixture that
                # reported "<task_id>.<side>.junit.xml" encoded the very
                # assumption that made the driver unretrievable against the real
                # executor, so it no longer does.
                artifact_name=self._reported_name,
                artifact_sha256=self.artifact_sha256
                or hashlib.sha256(data).hexdigest(),
                artifact_size=(self.artifact_size
                               if self.artifact_size is not None else len(data)),
                created_at="t0",
            )
            local.write_bytes(encode_record(payload))
        else:
            if self.fail_at == "get_missing":
                return None
            # Model the guest FILESYSTEM: only the reported name exists. A fetch
            # of any other path must fail the way Copy-Item does, so a driver
            # that fabricates a path is caught here rather than in the guest.
            if remote_name != self._reported_name:
                raise RuntimeError(
                    f"channel get failed: Cannot find path '{remote}' because it "
                    "does not exist."
                )
            # Write BYTES, not text: Path.write_text translates \n to \r\n on
            # Windows, so the retrieved artifact would not hash to the digest
            # the envelope reported. The driver re-hashes the retrieved bytes,
            # which is exactly the check that caught this.
            local.write_bytes((self.base if self._side == "base" else self.gold).encode("utf-8"))

    def run(self, command):
        self.commands.append(command)
        # The side is carried by the REQUEST the command names -- that is what
        # distinguishes the two runs when the artifact name does not.
        self._side = "gold" if "request_gold.rec" in command else "base"
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
        mismatch stops the task before any verdict is derived.

        The tamper preserves the byte LENGTH: a length change is caught by the
        (earlier) size check, so keeping the length equal is what isolates the
        digest check this test is about.
        """
        tampered = JUNIT_BASE.replace('message="boom"', 'message="BOOM"')
        assert len(tampered) == len(JUNIT_BASE)

        class Tampering(FakeChannel):
            def get(self, remote, local):
                super().get(remote, local)
                if str(remote).endswith(".junit.xml"):
                    # bytes, not text: write_text would translate \n -> \r\n and
                    # change the length, tripping the size check instead.
                    Path(local).write_bytes(tampered.encode("utf-8"))

        result = run_pilot_task(
            spec=_spec(), channel=Tampering(base=JUNIT_BASE), inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t8",
        )
        assert result.admitted is False
        assert result.failure_class == "artifact_digest_mismatch"


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


class CrashingChannel(FakeChannel):
    """Guest executor whose CHILD died before writing any JUnit.

    This is the shape `stanfordnlp__dspy-8739` produces on Windows: its suite
    calls ``os.kill(os.getpid(), signal.SIGINT)``, which CPython documents as
    ``TerminateProcess(self, 2)`` -- the interpreter dies mid-run, pytest never
    finalises, and no JUnit exists. The envelope is still valid, so the failure
    only surfaces as a missing artifact unless provenance is read.
    """

    def __init__(self, **kw):
        self.exit_status = kw.pop("exit_status", 2)
        super().__init__(**kw)

    def get(self, remote, local):
        if str(remote).endswith(".rec"):
            payload = build_result_payload(
                task_id=self.task_id, job_id=f"{self.task_id}#base",
                source="swe-bench-live",
                provenance={
                    "producer": "fake_guest",
                    "junit_present": False,
                    "junit_bytes": 0,
                    "exit_status": self.exit_status,
                    "timed_out": False,
                },
                artifact_name=f"{self.task_id}.base.junit.xml",
                artifact_sha256=hashlib.sha256(b"").hexdigest(),
                artifact_size=0, created_at="t0",
            )
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            Path(local).write_bytes(encode_record(payload))
            return None
        # No JUnit file was ever written: report it as absent.
        if Path(local).exists():
            Path(local).unlink()


class TestOutcomeTaxonomy:
    """The five outcome classes must stay distinguishable.

    F0 §12 pre-registers "Trigger failure (worker crash)" and "Worker timeout"
    as infrastructure failure and requires that an exclusion is "logged with
    reason". A task that cannot complete its evaluation must therefore never be
    representable as a valid base-FAIL/gold-PASS observation.
    """

    def test_worker_crash_is_classified_from_provenance(self):
        # junit_present False + non-zero child exit == F0 12 worker crash.
        assert classify_side_failure(
            "artifact_not_retrievable",
            provenance={"junit_present": False, "exit_status": 2, "timed_out": False},
        ) == "worker_crash"

    def test_worker_timeout_is_classified(self):
        # The executor process itself failing is its own rule: there is no
        # provenance to read, so the crash/timeout distinction cannot be made
        # and must not be guessed.
        assert classify_side_failure(
            "guest_exit_124",
            provenance={"junit_present": False, "exit_status": None, "timed_out": True},
        ) == "side_not_executable"
        # With provenance, a timeout the guest actually observed is a timeout.
        assert classify_side_failure(
            "artifact_not_retrievable",
            provenance={"junit_present": False, "exit_status": 124, "timed_out": True},
        ) == "worker_timeout"

    def test_clean_run_with_no_artifact_is_missing_not_crash(self):
        # No artifact and no crash signal: the evidence is absent, not a crash.
        assert classify_side_failure(
            "artifact_not_retrievable",
            provenance={"junit_present": False, "exit_status": 0, "timed_out": False},
        ) == "artifact_missing"

    def test_channel_and_evaluator_failures_keep_their_own_rules(self):
        assert classify_side_failure("transport_BAD_MAGIC") == "transport_invalid"
        assert classify_side_failure("evaluator_refused_base_ValueError") == "evaluator_rejected"
        assert classify_side_failure("artifact_digest_mismatch") == "artifact_digest_mismatch"
        assert classify_side_failure("guest_exit_1") == "side_not_executable"

    def test_every_returned_reason_is_in_the_frozen_vocabulary(self):
        samples = [
            classify_side_failure("artifact_not_retrievable",
                                  provenance={"junit_present": False, "exit_status": 2}),
            classify_side_failure("artifact_not_retrievable",
                                  provenance={"junit_present": False, "exit_status": 0}),
            classify_side_failure("transport_x"),
            classify_side_failure("evaluator_refused_y"),
            classify_side_failure("guest_exit_9"),
            classify_side_failure("artifact_digest_mismatch"),
            classify_side_failure("side_not_executable"),
        ]
        assert all(code in INFRASTRUCTURE_REASONS for code in samples), samples

    def test_a_crashed_side_is_recorded_as_infrastructure_with_a_reason(self, tmp_path):
        spec = _spec()
        ledger = AppendOnlyLedger(tmp_path / "ledger.jsonl")
        result = run_pilot_task(
            spec=spec, channel=CrashingChannel(), inputs=_inputs(tmp_path),
            ledger=ledger, gates=GATES, guest_dir=str(tmp_path),
            registration_sha256=REG_SHA, job_nonce="crash-1",
        )
        assert result.admitted is False
        assert result.outcome_class == "infrastructure"
        assert result.reason_code == "worker_crash"
        row = ledger.records()[0]
        assert row["outcome_class"] == "infrastructure"
        assert row["admitted"] is False
        assert row["reason_code"] == "worker_crash"
        # The exclusion decision carries the F0 12 rule, not just a channel string.
        assert row["reason"]

    def test_a_crashed_side_is_never_a_base_fail_gold_pass_observation(self, tmp_path):
        spec = _spec()
        result = run_pilot_task(
            spec=spec, channel=CrashingChannel(), inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="crash-2",
        )
        assert result.outcome == "infrastructure_failure"
        assert result.outcome != "base_fail_gold_pass"
        assert result.base_junit_sha256 == ""
        assert result.evidence_sha256 == ""

    def test_admitted_and_scientific_runs_carry_no_exclusion_reason(self, tmp_path):
        admitted = run_pilot_task(
            spec=_spec(), channel=FakeChannel(), inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "a.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="ok-1",
        )
        assert admitted.admitted is True
        assert admitted.reason_code == ""

        rejected = run_pilot_task(
            spec=_spec(), channel=FakeChannel(gold=JUNIT_BASE),
            inputs=_inputs(tmp_path), ledger=AppendOnlyLedger(tmp_path / "r.jsonl"),
            gates=GATES, guest_dir=str(tmp_path), registration_sha256=REG_SHA,
            job_nonce="rej-1",
        )
        assert rejected.outcome_class == "scientific"
        assert rejected.reason_code == ""


    def test_contradictory_provenance_is_named_not_hidden(self):
        """The guest reported an artifact the host could not obtain.

        Recording this as `artifact_missing` would hide the disagreement --
        the exact signal that suggests a broken channel or guest filesystem
        rather than a run that never produced evidence.
        """
        assert classify_side_failure(
            "artifact_not_retrievable",
            provenance={"junit_present": True, "exit_status": 0, "timed_out": False},
        ) == "provenance_contradiction"
        assert classify_side_failure(
            "no_artifact_reported",
            provenance={"junit_present": True, "exit_status": 0, "timed_out": False},
        ) == "provenance_contradiction"

    def test_absent_provenance_still_reads_as_missing_not_contradiction(self):
        """No provenance at all is absence of evidence, not a disagreement."""
        for failure in ("artifact_not_retrievable", "no_artifact_reported"):
            assert classify_side_failure(failure) == "artifact_missing"
            assert classify_side_failure(
                failure, provenance={"junit_present": False}
            ) == "artifact_missing"
            # Provenance present but silent on the question: same conclusion.
            assert classify_side_failure(
                failure, provenance={"exit_status": 1}
            ) == "artifact_missing"

    def test_contradiction_is_in_the_closed_vocabulary(self):
        assert "provenance_contradiction" in INFRASTRUCTURE_REASONS
        assert classify_side_failure(
            "no_artifact_reported", provenance={"junit_present": True}
        ) in INFRASTRUCTURE_REASONS

    def test_a_nonzero_exit_alongside_a_written_junit_is_not_a_crash(self):
        """pytest exits 1 on ordinary test failures, with JUnit written.

        Treating that as a worker crash would mislabel the commonest outcome
        in the whole pipeline as an infrastructure failure.
        """
        assert classify_side_failure(
            "artifact_not_retrievable",
            provenance={"junit_present": True, "exit_status": 1, "timed_out": False},
        ) == "provenance_contradiction"
        assert classify_side_failure(
            "artifact_not_retrievable",
            provenance={"junit_present": False, "exit_status": 1, "timed_out": False},
        ) == "worker_crash"


class TestBuildSpecsBindsTheRegisteredTestCmd:
    """REGRESSION (2026-10-15): ``test_cmds[0]`` diverged from the registration.

    The frozen registration stores ``test_cmd_sha256`` over the canonical
    command the source adapter derives from a raw row's ``test_cmds`` (the
    ``" && "`` chain), and the guest executes ``spec.payload["test_cmd"]``
    verbatim. Feeding only the first entry of a multi-entry list built specs
    whose command the registration never bound -- for one phase-2 candidate
    that first entry was ``cat tasks.py`` rather than pytest at all.
    """

    def test_multi_entry_test_cmds_build_the_joined_registered_command(self, tmp_path):
        from qwen_train.f2_s8_pilot_live import build_specs

        record = {
            "repo": "acme/widget",
            "base_commit": "0" * 40,
            "test_cmds": ["cat tasks.py", "python -m pytest ./tests -rA"],
            "FAIL_TO_PASS": ["tests/test_a.py::test_x"],
            "PASS_TO_PASS": ["tests/test_a.py::test_y"],
        }
        (tmp_path / "t1.json").write_text(json.dumps(record), encoding="utf-8")
        specs = build_specs({"t1": {"status": "built"}}, tmp_path, REG_SHA, "rid")
        assert len(specs) == 1
        assert (specs[0].payload["test_cmd"]
                == "cat tasks.py && python -m pytest ./tests -rA")


class TestRunManifestMustResolveToRunnableTasks:
    """REGRESSION (2026-10-15): a manifest of non-``built`` entries ran zero tasks.

    Reproduced live: the runbook passed the *dry-run result*
    (``pilot_build/dryrun/dryrun.json``, ``status="dryrun_complete"``) as
    ``--manifest``. ``build_specs`` filters on ``status == "built"``, so the run
    resolved to zero specs and ``main`` printed "pilot: 0 tasks", wrote a results
    file and exited 0 -- a vacuous run indistinguishable from a completed pilot.
    These tests pin both halves: the filter, and the refusal that replaces the
    silent success.
    """

    def _record(self):
        return {
            "repo": "acme/widget",
            "base_commit": "0" * 40,
            "test_cmds": ["pytest -q"],
            "FAIL_TO_PASS": ["tests/test_a.py::test_x"],
            "PASS_TO_PASS": ["tests/test_a.py::test_y"],
        }

    def test_non_built_entries_are_filtered_out(self, tmp_path):
        from qwen_train.f2_s8_pilot_live import build_specs

        (tmp_path / "t1.json").write_text(json.dumps(self._record()), encoding="utf-8")
        assert build_specs({"t1": {"status": "dryrun_complete"}}, tmp_path,
                           REG_SHA, "rid") == []
        assert len(build_specs({"t1": {"status": "built"}}, tmp_path,
                               REG_SHA, "rid")) == 1

    def test_main_refuses_a_zero_task_manifest(self, tmp_path, monkeypatch):
        from qwen_train import f2_s8_pilot_live as live

        monkeypatch.setattr(live, "load_governance_from_env", lambda *a, **k: object())
        gates = tmp_path / "gates.json"
        gates.write_text(json.dumps({n: True for n in live.REQUIRED_GATES}),
                         encoding="utf-8")
        manifest = tmp_path / "dryrun.json"
        manifest.write_text(json.dumps({"t1": {"status": "dryrun_complete"}}),
                            encoding="utf-8")
        inputs = tmp_path / "inputs"
        inputs.mkdir()

        with pytest.raises(SystemExit) as excinfo:
            live.main([
                "--manifest", str(manifest),
                "--inputs", str(inputs),
                "--registration-id", "rid",
                "--registration-sha256", REG_SHA,
                "--ledger", str(tmp_path / "ledger.jsonl"),
                "--staging", str(tmp_path / "staging"),
                "--out", str(tmp_path / "out"),
                "--gates", str(gates),
            ])
        message = str(excinfo.value)
        assert "zero runnable tasks" in message
        assert "dryrun_complete" in message


class TestArtifactRetrievalUsesTheReportedName:
    """REGRESSION (2026-10-15): the driver fabricated ``<task_id>.<side>.junit.xml``.

    ``f2_s8_guest`` has NO side argument and writes ``<task_id>.junit.xml``. The
    driver built a side-suffixed path instead, so ``channel.get`` was asked for a
    file that never existed and EVERY task faulted as an unclassified channel
    error -- which made a real infrastructure failure indistinguishable from a
    driver bug. A live guest run reproduced it exactly: the envelope reported
    ``junit_present: true`` with 1,447,929 bytes while the driver looked for the
    fabricated name. These tests pin the corrected contract.
    """

    def _run(self, tmp_path, channel, job_nonce="t"):
        return run_pilot_task(
            spec=_spec(), channel=channel, inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA,
            job_nonce=job_nonce,
        )

    def test_both_sides_fetch_the_reported_basename(self, tmp_path):
        channel = FakeChannel()
        result = self._run(tmp_path, channel)
        assert result.admitted is True, result.failure_class
        junit_requests = [r for r in channel.requested if r.endswith(".junit.xml")]
        assert len(junit_requests) == 2, channel.requested
        assert all(Path(r).name == f"{channel.task_id}.junit.xml"
                   for r in junit_requests), channel.requested

    def test_the_fabricated_side_specific_name_is_never_attempted(self, tmp_path):
        channel = FakeChannel()
        self._run(tmp_path, channel)
        fabricated = [r for r in channel.requested
                      if ".base.junit.xml" in r or ".gold.junit.xml" in r]
        assert fabricated == [], fabricated

    @pytest.mark.parametrize(
        "unsafe",
        ["", ".", "..", "a/b.xml", "a\\b.xml", "C:\\tmp\\x.xml", "/abs/x.xml"],
    )
    def test_an_unsafe_reported_name_is_refused_before_any_fetch(self, tmp_path, unsafe):
        channel = FakeChannel(artifact_name=unsafe)
        result = self._run(tmp_path, channel)
        assert result.admitted is False
        assert result.failure_class, "a refusal must name its reason"
        # Whatever rejects it (the transport validator or the driver guard),
        # the guest is never asked for a path built from an unsafe name.
        assert not [r for r in channel.requested if r.endswith(".junit.xml")]

    @pytest.mark.parametrize(
        "unsafe",
        ["", ".", "..", "a/b.xml", "a\\b.xml", "C:\\tmp\\x.xml", "/abs/x.xml"],
    )
    def test_safe_basename_guard_rejects_unsafe_names(self, unsafe):
        from qwen_train.f2_s8_pilot import _is_safe_artifact_basename

        assert _is_safe_artifact_basename(unsafe) is False

    def test_safe_basename_guard_accepts_the_real_guest_name(self):
        from qwen_train.f2_s8_pilot import _is_safe_artifact_basename

        assert _is_safe_artifact_basename("o__a-1.junit.xml") is True

    def test_a_size_mismatch_fails_closed(self, tmp_path):
        channel = FakeChannel(artifact_size=1)
        result = self._run(tmp_path, channel)
        assert result.admitted is False
        assert result.failure_class == "artifact_size_mismatch"

    def test_a_digest_mismatch_fails_closed(self, tmp_path):
        channel = FakeChannel(artifact_sha256="0" * 64)
        result = self._run(tmp_path, channel)
        assert result.admitted is False
        assert result.failure_class == "artifact_digest_mismatch"


class TestUvTasksResolveBuildRequirementsOffline:
    """REGRESSION (2026-10-15): ``uv run`` could not build offline.

    ``build-system.requires = ["hatchling"]`` (plus ``editables~=0.3`` for the
    editable install). With ``UV_OFFLINE=1`` uv resolves those ONLY from its
    cache or ``UV_FIND_LINKS``; the guest has no uv cache, so the uv tasks failed
    with "No solution found when resolving: hatchling". The pilot now points uv at
    the delivered wheelhouse.
    """

    def test_uv_tasks_are_pointed_at_the_offline_wheelhouse(self):
        from qwen_train.f2_s8_pilot_live import WHEELHOUSE, per_task_env

        env = per_task_env("t", {"t": {"workspace": r"C:\f2_pilot\t", "uses_uv": True}})
        assert env["env_extra"]["UV_OFFLINE"] == "1"
        assert env["env_extra"]["UV_FIND_LINKS"] == WHEELHOUSE

    def test_non_uv_tasks_do_not_get_uv_settings(self):
        from qwen_train.f2_s8_pilot_live import per_task_env

        env = per_task_env("t", {"t": {"workspace": r"C:\f2_pilot\t", "uses_uv": False}})
        assert "UV_FIND_LINKS" not in env["env_extra"]
        assert "UV_OFFLINE" not in env["env_extra"]


class TestPerSideEnvironment:
    """REGRESSION (2026-10-15): ONE PATH prefix for both sides made the gold run
    execute under the BASE interpreter.

    An editable install makes the project importable from ONE source tree (PEP
    660: "changes to the project python code in the local source tree become
    effective"), so the INTERPRETER selects which tree is imported. Measured
    in-guest: gold tree + gold venv -> node PASSES; gold tree + base venv -> node
    FAILS with the base AttributeError. Every declared test_cmd is repo-relative
    and resolves a bare ``pytest`` from PATH, so the prefix must be per side.
    """

    def test_per_task_env_gives_each_side_its_own_prefix(self):
        from qwen_train.f2_s8_pilot_live import per_task_env

        env = per_task_env("t", {"t": {"workspace": r"C:\f2_pilot\t", "uses_uv": False}})
        prefixes = env["path_prefix"]
        assert ".venv\\Scripts" in prefixes["base"]
        assert ".venv_gold" not in prefixes["base"]
        assert ".venv_gold\\Scripts" in prefixes["gold"]
        assert prefixes["base"] != prefixes["gold"]
        assert env["workdirs"]["base"].endswith("repo")
        assert env["workdirs"]["gold"].endswith("repo_gold")

    def test_the_gold_command_runs_with_the_gold_environment(self, tmp_path):
        channel = FakeChannel()
        run_pilot_task(
            spec=_spec(), channel=channel, inputs=_inputs(tmp_path),
            ledger=AppendOnlyLedger(tmp_path / "l.jsonl"), gates=GATES,
            guest_dir=str(tmp_path), registration_sha256=REG_SHA, job_nonce="t9",
            workdirs={"base": r"C:\ws\repo", "gold": r"C:\ws\repo_gold"},
            path_prefix={"base": r"C:\ws\.venv\Scripts",
                         "gold": r"C:\ws\.venv_gold\Scripts"},
        )
        base_cmd = next(c for c in channel.commands if "request_base.rec" in c)
        gold_cmd = next(c for c in channel.commands if "request_gold.rec" in c)
        assert r"C:\ws\.venv\Scripts" in base_cmd
        assert r"C:\ws\.venv_gold\Scripts" not in base_cmd
        assert r"C:\ws\.venv_gold\Scripts" in gold_cmd
        assert "--workdir 'C:\\ws\\repo_gold'" in gold_cmd


class TestLedgerRefusalIsAClassifiedOutcome:
    """REGRESSION (2026-10-15): a DESIGNED ledger refusal was reported as a
    CHANNEL/DRIVER FAULT and dropped from the counts entirely.

    The append-only guard refuses to re-score a task that already holds a
    terminal scientific record. That refusal is an outcome with a reason; it must
    be counted and named, not mistaken for a driver bug.
    """

    def test_a_refusal_is_recorded_not_reported_as_a_fault(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from qwen_train import f2_s8_pilot_live as live
        from qwen_train.f2_s8_factory import LedgerError

        stub_inputs = SimpleNamespace(
            environment_identity="test-env",
            evaluator=SimpleNamespace(evaluator_id="e", version="1",
                                      implementation_digest="0" * 64,
                                      procedure_id="p"),
        )
        monkeypatch.setattr(live, "load_governance_from_env",
                            lambda *a, **k: stub_inputs)
        monkeypatch.setattr(live, "PsDirectChannel", lambda **kw: object())
        monkeypatch.setattr(live, "stage_spec_for_guest", lambda *a, **k: "spec.json")

        def boom(**kw):
            raise LedgerError(
                "task 't1' already has a terminal scientific record; append-only "
                "history may not be rewritten"
            )

        monkeypatch.setattr(live, "run_pilot_task", boom)

        inputs = tmp_path / "inputs"
        inputs.mkdir()
        (inputs / "t1.json").write_text(json.dumps({
            "repo": "acme/widget", "base_commit": "0" * 40, "test_cmds": ["pytest -q"],
            "FAIL_TO_PASS": ["tests/test_a.py::test_x"],
            "PASS_TO_PASS": ["tests/test_a.py::test_y"],
        }), encoding="utf-8")
        manifest = tmp_path / "m.json"
        manifest.write_text(json.dumps(
            {"t1": {"status": "built", "workspace": r"C:\f2_pilot\t1"}}), encoding="utf-8")
        gates = tmp_path / "g.json"
        gates.write_text(json.dumps({n: True for n in live.REQUIRED_GATES}),
                         encoding="utf-8")
        out = tmp_path / "out"

        rc = live.main([
            "--manifest", str(manifest), "--inputs", str(inputs),
            "--registration-id", "rid", "--registration-sha256", REG_SHA,
            "--ledger", str(tmp_path / "l.jsonl"), "--staging", str(tmp_path / "s"),
            "--out", str(out), "--gates", str(gates),
        ])
        assert rc == 0
        payload = json.loads((out / "pilot_results.json").read_text(encoding="utf-8"))
        task = payload["tasks"][0]
        assert task["outcome"] == "ledger_refused"
        assert task["outcome_class"] == "infrastructure"
        assert task["admitted"] is False
        assert "terminal scientific record" in task["detail"]
        assert (out / "t1.refused.txt").is_file()
        assert not list(out.glob("*.fault.txt")), "a refusal is not a driver fault"
        assert payload["totals"]["infrastructure_invalid"] >= 0
