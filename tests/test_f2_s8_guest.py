"""Tests for the S8 guest-side executor (runs the protocol; no real task).

The command runner is injected, so no registered task, VM, network or evaluator
is touched.  The default runner is never invoked here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from qwen_train.f2_s8_guest import (
    CommandResult,
    GuestExecutorError,
    execute_in_guest,
    load_spec,
)
from qwen_train.f2_s8_runner import publish_atomic
from qwen_train.f2_s8_session import build_task_spec
from qwen_train.f2_s8_transport import read_result, stage_request

EVAL = {
    "evaluator_id": "f2_pytest_junit_evaluator", "version": "1.0.0",
    "implementation_digest": "a" * 64, "procedure_id": "p", "protocol_version": "f2_experiment_j_v1",
}
JUNIT = (
    "<testsuites><testsuite name='pytest' tests='1'>"
    "<testcase classname='t.test_x' name='test_a' time='0.1'/>"
    "</testsuite></testsuites>"
)


def _spec(**over):
    kw = dict(
        task_id="o__a-1", source="swe-bench-live", repo="o/a", base_commit="f" * 40,
        test_cmd="pytest -q", fail_to_pass=["t/test_x.py::test_a"],
        pass_to_pass=["t/test_x.py::test_b"], registration_id="reg",
        registration_sha256="c" * 64, evaluator=EVAL,
    )
    kw.update(over)
    return build_task_spec(**kw)


def _write_spec(path: Path, spec) -> None:
    publish_atomic(path, json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":")).encode())


def _request(tmp: Path, spec, *, task_id=None, job_id="job-1") -> Path:
    return stage_request(
        channel_dir=tmp, task_id=task_id or spec.task_id, job_id=job_id,
        source=str(spec.payload["source"]), provenance={"producer": "test"},
    )


def _ok_run(junit_text: str = JUNIT):
    def run(command: str, *, cwd: str, timeout_seconds: int) -> CommandResult:
        target = Path(re.search(r"--junitxml=(\S+)", command).group(1))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(junit_text, encoding="utf-8")
        return CommandResult(exit_status=0, stdout=b"ok", stderr=b"")
    return run


class TestExecuteInGuest:
    def test_produces_a_transport_only_result(self, tmp_path):
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec)
        result_path = tmp_path / "result.rec"
        junit_dir = tmp_path / "junit"

        payload = execute_in_guest(
            spec_path=spec_path, request_path=request_path, result_path=result_path,
            junit_dir=junit_dir, run_command=_ok_run(), clock=lambda: "T",
        )
        assert payload["payload_kind"] == "evidence"
        assert payload["status"] == "attempted"          # lifecycle only, never a verdict
        assert "declared_result" not in payload
        verdict = read_result(result_path, expected_task_id=spec.task_id, expected_job_id="job-1")
        assert verdict.ok is True
        assert verdict.payload["artifact"]["size_bytes"] == len(JUNIT.encode())

    def test_missing_request_is_refused(self, tmp_path):
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        with pytest.raises(GuestExecutorError, match="not readable|rejected|No such|does not exist"):
            execute_in_guest(
                spec_path=spec_path, request_path=tmp_path / "absent.rec",
                result_path=tmp_path / "r.rec", junit_dir=tmp_path / "j",
                run_command=_ok_run(),
            )

    def test_request_for_a_different_task_is_refused(self, tmp_path):
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec, task_id="other__task-9")
        with pytest.raises(GuestExecutorError, match="rejected"):
            execute_in_guest(
                spec_path=spec_path, request_path=request_path,
                result_path=tmp_path / "r.rec", junit_dir=tmp_path / "j",
                run_command=_ok_run(),
            )

    def test_tampered_spec_digest_is_refused(self, tmp_path):
        spec = _spec()
        payload = spec.to_dict()
        payload["spec_digest"] = "0" * 64
        spec_path = tmp_path / "spec.json"
        publish_atomic(spec_path, json.dumps(payload).encode())
        with pytest.raises(GuestExecutorError, match="digest"):
            load_spec(spec_path)

    def test_absent_junit_is_reported_not_passed(self, tmp_path):
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec)

        def no_junit(command, *, cwd, timeout_seconds):
            return CommandResult(exit_status=0, stdout=b"", stderr=b"")

        payload = execute_in_guest(
            spec_path=spec_path, request_path=request_path,
            result_path=tmp_path / "r.rec", junit_dir=tmp_path / "j",
            run_command=no_junit, clock=lambda: "T",
        )
        assert payload["artifact"]["size_bytes"] == 0
        assert payload["provenance"]["junit_present"] is False

    def test_timeout_is_recorded_in_provenance(self, tmp_path):
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec)

        def timeout_run(command, *, cwd, timeout_seconds):
            return CommandResult(exit_status=124, timed_out=True)

        payload = execute_in_guest(
            spec_path=spec_path, request_path=request_path,
            result_path=tmp_path / "r.rec", junit_dir=tmp_path / "j",
            run_command=timeout_run, clock=lambda: "T",
        )
        assert payload["provenance"]["timed_out"] is True
