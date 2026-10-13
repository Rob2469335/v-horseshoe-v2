"""Tests for the S8 host transport over the approved channel.

The guest session is SIMULATED: a local directory stands in for the guest and a
real invocation of the guest executor runs against it.  This proves the transport
protocol, identity binding, integrity checks and fail-closed paths -- it does NOT
prove the real PowerShell Direct path, which is NOT ESTABLISHED (no live guest was
used).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from qwen_train.f2_s8_guest import CommandResult, execute_in_guest
from qwen_train.f2_s8_job import derive_job_id
from qwen_train.f2_s8_podirect import (
    PowerShellDirectTransport,
    SessionRun,
    TransportError,
)
from qwen_train.f2_s8_session import build_task_spec

VM_ID = "c5d294f5-5515-46b3-bc80-ddc70dd94fed"
VM_NAME = "F2-S8-VM1"
EVAL = {
    "evaluator_id": "f2_pytest_junit_evaluator", "version": "1.0.0",
    "implementation_digest": "a" * 64, "procedure_id": "p", "protocol_version": "f2_experiment_j_v1",
}
JUNIT = (
    "<testsuites><testsuite name='pytest' tests='1'>"
    "<testcase classname='t.test_x' name='test_a' time='0.1'/>"
    "</testsuite></testsuites>"
)


def _spec():
    return build_task_spec(
        task_id="o__a-1", source="swe-bench-live", repo="o/a", base_commit="f" * 40,
        test_cmd="pytest -q", fail_to_pass=["t/test_x.py::test_a"],
        pass_to_pass=["t/test_x.py::test_b"], registration_id="reg",
        registration_sha256="c" * 64, evaluator=EVAL,
    )


def _ok_run(command: str, *, cwd: str, timeout_seconds: int) -> CommandResult:
    target = Path(re.search(r"--junitxml=(\S+)", command).group(1))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(JUNIT, encoding="utf-8")
    return CommandResult(exit_status=0, stdout=b"guest-ran", stderr=b"")


class FakeGuestSession:
    """A simulated guest: maps the guest path prefix onto a local directory.

    ``run`` executes the REAL guest executor against the mapped files, so the
    guest-side protocol is genuinely exercised; only the transport (PowerShell
    Direct) is simulated.
    """

    def __init__(self, root: Path, *, write_result: bool = True,
                 tamper_junit: bool = False, run_command=_ok_run) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.write_result = write_result
        self.tamper_junit = tamper_junit
        self.run_command = run_command

    def _map(self, remote: str) -> Path:
        parts = remote.replace("\\", "/").split("/")
        idx = parts.index("f2_s8")
        return self.root.joinpath(*parts[idx + 1:])

    def put(self, local: Path, remote: str) -> None:
        dst = self._map(remote)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dst)

    def get(self, remote: str, local: Path) -> None:
        src = self._map(remote)
        if not src.is_file():
            raise TransportError(f"guest file missing: {remote}")
        data = src.read_bytes()
        if self.tamper_junit and remote.endswith(".junit.xml"):
            data += b"tamper"
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(data)

    def run(self, command: str) -> SessionRun:
        args = dict(re.findall(r"--([a-z-]+) '([^']*)'", command))
        if self.write_result:
            execute_in_guest(
                spec_path=self._map(args["spec"]),
                request_path=self._map(args["request"]),
                result_path=self._map(args["result"]),
                junit_dir=self._map(args["junit-dir"]),
                run_command=self.run_command,
                clock=lambda: "T",
            )
        return SessionRun(exit_status=0, stdout=b"guest", stderr=b"")


def _transport(tmp_path, guest, *, vm_id=VM_ID, provider_vm_id=None, seen_job_ids=None):
    return PowerShellDirectTransport(
        vm_name=VM_NAME, vm_id=vm_id, session=guest,
        channel_dir=tmp_path / "channel", output_root=tmp_path / "out",
        arm_id="arm-1", rollout_id="roll-1",
        vm_identity_provider=lambda name: (name, provider_vm_id or vm_id),
        seen_job_ids=seen_job_ids, clock=lambda: "T",
    )


class TestTransportHappyPath:
    def test_executes_and_materialises_the_retained_artifact(self, tmp_path):
        spec = _spec()
        guest = FakeGuestSession(tmp_path / "guest")
        out = _transport(tmp_path, guest)(spec, 1)
        assert out.exit_status == 0
        assert out.stdout == b"guest"
        junit = tmp_path / "out" / "evidence" / f"{spec.task_id}.junit.xml"
        assert junit.read_bytes() == JUNIT.encode()

    def test_request_and_spec_were_staged(self, tmp_path):
        spec = _spec()
        guest = FakeGuestSession(tmp_path / "guest")
        _transport(tmp_path, guest)(spec, 1)
        assert (tmp_path / "channel" / "requests").glob("*.rec")
        assert (tmp_path / "channel" / "requests").glob("*.spec.json")


class TestFailClosed:
    def test_vm_identity_mismatch_is_refused(self, tmp_path):
        spec = _spec()
        guest = FakeGuestSession(tmp_path / "guest")
        transport = _transport(tmp_path, guest, provider_vm_id="0" * 8)
        with pytest.raises(TransportError, match="identity mismatch"):
            transport(spec, 1)

    def test_artifact_digest_mismatch_is_refused(self, tmp_path):
        spec = _spec()
        guest = FakeGuestSession(tmp_path / "guest", tamper_junit=True)
        with pytest.raises(TransportError, match="digest does not match"):
            _transport(tmp_path, guest)(spec, 1)

    def test_missing_result_is_refused(self, tmp_path):
        spec = _spec()
        guest = FakeGuestSession(tmp_path / "guest", write_result=False)
        with pytest.raises(TransportError, match="guest file missing"):
            _transport(tmp_path, guest)(spec, 1)

    def test_replayed_result_is_refused(self, tmp_path):
        spec = _spec()
        job_id = derive_job_id(
            task_id=spec.task_id, source="swe-bench-live",
            population_artifact_sha256=spec.registration_sha256, phase="EXECUTE_S8",
        )
        guest = FakeGuestSession(tmp_path / "guest")
        with pytest.raises(TransportError, match="rejected"):
            _transport(tmp_path, guest, seen_job_ids={job_id})(spec, 1)

    def test_missing_identity_configuration_is_refused(self, tmp_path):
        guest = FakeGuestSession(tmp_path / "guest")
        with pytest.raises(TransportError, match="arm_id is required"):
            PowerShellDirectTransport(
                vm_name=VM_NAME, vm_id=VM_ID, session=guest,
                channel_dir=tmp_path / "c", output_root=tmp_path / "o",
                arm_id="", rollout_id="roll-1",
            )

    def test_guest_dir_must_be_drive_qualified(self, tmp_path):
        guest = FakeGuestSession(tmp_path / "guest")
        with pytest.raises(TransportError, match="drive-qualified"):
            PowerShellDirectTransport(
                vm_name=VM_NAME, vm_id=VM_ID, session=guest,
                channel_dir=tmp_path / "c", output_root=tmp_path / "o",
                arm_id="arm", rollout_id="roll", guest_dir="/f2_s8",
            )


JUNIT_BASE = (
    "<testsuites><testsuite name='pytest' tests='2' failures='1'>"
    "<testcase classname='t.test_x' name='test_a' time='0.1'>"
    "<failure message='boom'>trace</failure></testcase>"
    "<testcase classname='t.test_x' name='test_b' time='0.1'/>"
    "</testsuite></testsuites>"
)
JUNIT_GOLD = JUNIT_BASE.replace(
    "<testcase classname='t.test_x' name='test_a' time='0.1'>"
    "<failure message='boom'>trace</failure></testcase>",
    "<testcase classname='t.test_x' name='test_a' time='0.1'/>",
).replace("failures='1'", "failures='0'")


class TestEndToEndHostPath:
    """transport -> runner -> authoritative evaluator -> governed admission.

    The guest is SIMULATED (a local directory + a real guest-executor run).  This
    exercises the whole host path; it does not prove the real PowerShell Direct
    path (NOT ESTABLISHED).
    """

    def _side_evidence(self, tmp_path, spec, side: str, junit_text: str):
        from qwen_train.f2_s8_compose import guest_evidence_from_junit
        from qwen_train.f2_s8_runner import GuestRunner, verify_receipt

        def run_command(command, *, cwd, timeout_seconds):
            target = Path(re.search(r"--junitxml=(\S+)", command).group(1))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(junit_text, encoding="utf-8")
            return CommandResult(exit_status=0, stdout=b"ran", stderr=b"")

        guest = FakeGuestSession(tmp_path / f"guest-{side}", run_command=run_command)
        transport = PowerShellDirectTransport(
            vm_name=VM_NAME, vm_id=VM_ID, session=guest,
            channel_dir=tmp_path / f"channel-{side}", output_root=tmp_path / f"out-{side}",
            arm_id="arm-1", rollout_id="roll-1",
            vm_identity_provider=lambda name: (name, VM_ID), clock=lambda: "T",
        )
        runner = GuestRunner(transport=transport, output_root=tmp_path / f"out-{side}")
        receipt = runner.run(spec, attempt=1)
        ok, reasons = verify_receipt(receipt, spec)
        assert ok, reasons
        junit = tmp_path / f"out-{side}" / "evidence" / f"{spec.task_id}.junit.xml"
        return guest_evidence_from_junit(
            spec=spec, side=side, junit_bytes=junit.read_bytes(),
            run_log=b"host-run", started_at="t0", finished_at="t1",
        )

    def test_reaches_admission_through_the_real_verifiers(self, tmp_path):
        import hashlib

        from qwen_train import f2_evaluator as evaluator
        from qwen_train import f2_governance as governance
        from qwen_train.f2_s8_compose import GovernanceInputs, produce_governed_task
        from qwen_train.f2_s8_factory import AppendOnlyLedger
        from qwen_train.f2_s8_session import REQUIRED_GATES, FakeVMAdapter

        spec = _spec()
        impl = Path(evaluator.__file__).resolve().read_bytes()
        auth = governance.EvaluatorAuthorization(
            evaluator_id=EVAL["evaluator_id"], version=EVAL["version"],
            implementation_digest=hashlib.sha256(impl).hexdigest(),
            procedure_id=EVAL["procedure_id"], protocol_version=EVAL["protocol_version"],
        )
        inputs = GovernanceInputs(
            store=governance.TrustedArtifactStore(
                tmp_path / "store", governance.RetentionPolicy("r", 30, True)
            ),
            registry=governance.EvaluatorRegistry([auth]), evaluator=auth,
            environment_identity="e2e", base_state_digest="a" * 64, gold_state_digest="b" * 64,
            implementation_name="f2_evaluator.py", implementation_bytes=impl,
        )
        evidence = {
            "base": self._side_evidence(tmp_path, spec, "base", JUNIT_BASE),
            "gold": self._side_evidence(tmp_path, spec, "gold", JUNIT_GOLD),
        }
        res = produce_governed_task(
            spec=spec, gates={n: True for n in REQUIRED_GATES}, adapter=FakeVMAdapter(),
            ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"), inputs=inputs,
            execute=lambda _s, side: evidence[side],
            expected_registration_sha256="c" * 64, ts="T",
        )
        assert res.state == "admitted"
        assert res.record["admitted"] is True
