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


class TestWorkingDirectory:
    """Regression for the defect that made real-task execution impossible:
    every declared ``test_cmd`` in the pinned population is REPO-RELATIVE, but
    the executor ran it with ``cwd=junit_dir``.  ``pytest -rA`` at an empty
    junit directory collects nothing, writes a JUnit with zero tests, and the
    FAIL_TO_PASS nodes are then ``missing`` -- so a correct environment would
    have been recorded as a scientific failure.  The command must run at the
    repository root; ``--junitxml`` is absolute, so the artifact still lands in
    ``junit_dir``."""

    def test_command_runs_at_the_supplied_workdir(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tests").mkdir()
        (repo / "tests" / "test_x.py").write_text(
            "def test_a():\n    assert True\n", encoding="utf-8"
        )
        junit_dir = tmp_path / "junit"
        spec = _spec(test_cmd="pytest -q tests/test_x.py")
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec)

        seen: dict[str, str] = {}

        def run(command, *, cwd, timeout_seconds):
            seen["cwd"] = cwd
            seen["command"] = command
            target = Path(re.search(r"--junitxml=(\S+)", command).group(1))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(JUNIT, encoding="utf-8")
            return CommandResult(exit_status=0, stdout=b"", stderr=b"")

        execute_in_guest(
            spec_path=spec_path, request_path=request_path,
            result_path=tmp_path / "r.rec", junit_dir=junit_dir,
            workdir=repo, run_command=run, clock=lambda: "T",
        )
        assert seen["cwd"] == str(repo)
        # The artifact is still written into junit_dir, by absolute path.
        assert str(junit_dir) in seen["command"]
        assert (junit_dir / f"{spec.task_id}.junit.xml").is_file()

    def test_default_workdir_is_the_junit_dir_for_backcompat(self, tmp_path):
        """No --workdir keeps the old behaviour, so existing fixtures and the
        disk-channel tests continue to pass unchanged."""
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec)
        junit_dir = tmp_path / "j"
        seen: dict[str, str] = {}

        def run(command, *, cwd, timeout_seconds):
            seen["cwd"] = cwd
            target = Path(re.search(r"--junitxml=(\S+)", command).group(1))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(JUNIT, encoding="utf-8")
            return CommandResult(exit_status=0, stdout=b"", stderr=b"")

        execute_in_guest(
            spec_path=spec_path, request_path=request_path,
            result_path=tmp_path / "r.rec", junit_dir=junit_dir,
            run_command=run, clock=lambda: "T",
        )
        assert seen["cwd"] == str(junit_dir)

    def test_absent_workdir_fails_closed(self, tmp_path):
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec)
        with pytest.raises(GuestExecutorError, match="working directory"):
            execute_in_guest(
                spec_path=spec_path, request_path=request_path,
                result_path=tmp_path / "r.rec", junit_dir=tmp_path / "j",
                workdir=tmp_path / "nope", run_command=_ok_run(),
            )


class TestArtifactIsPerSide:
    """REGRESSION (2026-10-15): both sides wrote ONE file, so a side that
    produced no JUnit re-reported the other side artifact.

    Observed live: hypothesis-4532 gold wrote no JUnit, the guest reported the
    BASE file, and the host recorded a scientific ``gold_fail`` -- a FALSE
    result. mypy gold then re-reported its base artifact too. The artifact must
    be named per side and any stale file must be cleared before the run.
    """

    def _prep(self, tmp_path, spec, *, side):
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = stage_request(
            channel_dir=tmp_path, task_id=spec.task_id,
            job_id=f"{spec.task_id}#{side}#1",
            source=str(spec.payload["source"]),
            provenance={"producer": "pilot", "side": side},
        )
        return spec_path, request_path

    def test_the_artifact_name_carries_the_side(self, tmp_path):
        spec = _spec()
        junit_dir = tmp_path / "junit"
        for side in ("base", "gold"):
            spec_path, request_path = self._prep(tmp_path, spec, side=side)
            payload = execute_in_guest(
                spec_path=spec_path, request_path=request_path,
                result_path=tmp_path / f"r_{side}.rec", junit_dir=junit_dir,
                run_command=_ok_run(), clock=lambda: "T",
            )
            assert payload["artifact"]["name"] == f"{spec.task_id}.{side}.junit.xml"

    def test_base_and_gold_write_distinct_files(self, tmp_path):
        spec = _spec()
        junit_dir = tmp_path / "junit"
        names = []
        for side in ("base", "gold"):
            spec_path, request_path = self._prep(tmp_path, spec, side=side)
            payload = execute_in_guest(
                spec_path=spec_path, request_path=request_path,
                result_path=tmp_path / f"r_{side}.rec", junit_dir=junit_dir,
                run_command=_ok_run(), clock=lambda: "T",
            )
            names.append(payload["artifact"]["name"])
        assert names[0] != names[1]
        assert sorted(p.name for p in junit_dir.glob("*.junit.xml")) == sorted(names)

    def test_a_stale_artifact_is_never_reported(self, tmp_path):
        """The exact false-``gold_fail`` mechanism: a pre-existing file must not
        be reported as this run's output when the command writes nothing."""
        spec = _spec()
        junit_dir = tmp_path / "junit"
        junit_dir.mkdir(parents=True)
        stale = junit_dir / f"{spec.task_id}.gold.junit.xml"
        stale.write_text("<testsuites/>", encoding="utf-8")

        def writes_nothing(command, *, cwd, timeout_seconds):
            return CommandResult(exit_status=2, stdout=b"", stderr=b"boom")

        spec_path, request_path = self._prep(tmp_path, spec, side="gold")
        payload = execute_in_guest(
            spec_path=spec_path, request_path=request_path,
            result_path=tmp_path / "r.rec", junit_dir=junit_dir,
            run_command=writes_nothing, clock=lambda: "T",
        )
        assert payload["provenance"]["junit_present"] is False
        assert payload["artifact"]["size_bytes"] == 0
        assert not stale.exists(), "the stale artifact must have been cleared"

    def test_a_side_less_request_keeps_the_historical_name(self, tmp_path):
        """A caller that carries no side is not guessed at."""
        spec = _spec()
        spec_path = tmp_path / "spec.json"
        _write_spec(spec_path, spec)
        request_path = _request(tmp_path, spec)
        payload = execute_in_guest(
            spec_path=spec_path, request_path=request_path,
            result_path=tmp_path / "r.rec", junit_dir=tmp_path / "junit",
            run_command=_ok_run(), clock=lambda: "T",
        )
        assert payload["artifact"]["name"] == f"{spec.task_id}.junit.xml"

    def test_bounded_runner_is_non_interactive_and_bounded(self, tmp_path, monkeypatch):
        import subprocess

        from qwen_train import f2_s8_guest as G

        captured = {}

        class _P:
            returncode = 0
            stdout = b"o"
            stderr = b"e"

        def fake_run(cmd, **kw):
            captured.update(kw)
            return _P()

        monkeypatch.setattr(G.subprocess, "run", fake_run)
        result = G._default_run_command("echo hi", cwd=str(tmp_path), timeout_seconds=7)
        assert result.exit_status == 0
        assert captured["stdin"] is subprocess.DEVNULL
        assert captured["timeout"] == 7
        assert captured["capture_output"] is True


class TestGuestExecutionSummaryIsEmitted:
    """The guest must ECHO the real test-command output (Decision 3 / audit D-02).

    The transport envelope is a record, not a log; without the echo the host's
    retained run log would carry empty output sections.
    """

    def _emit(self, capsys, *, stdout=b"", stderr=b"", exit_status=0, timed_out=False):
        import json as _json

        from qwen_train import f2_s8_guest as G

        G._emit_execution_summary(G.CommandResult(
            exit_status=exit_status, stdout=stdout, stderr=stderr, timed_out=timed_out))
        return _json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    def test_real_stdout_and_stderr_are_echoed(self, capsys):
        payload = self._emit(capsys, stdout=b"89 passed in 3.1s\n", stderr=b"warning: x\n")
        assert payload["guest_execution"]["stdout"] == "89 passed in 3.1s\n"
        assert payload["guest_execution"]["stderr"] == "warning: x\n"
        assert payload["guest_execution"]["stdout_truncated"] is False
        assert payload["guest_execution"]["stdout_total_bytes"] == 18

    def test_truncation_is_explicit_not_silent(self, capsys):
        from qwen_train import f2_s8_guest as G

        big = b"x" * (G.MAX_ECHO_BYTES + 500)
        payload = self._emit(capsys, stdout=big)
        exec_summary = payload["guest_execution"]
        assert exec_summary["stdout_truncated"] is True
        assert exec_summary["stdout_total_bytes"] == len(big)
        assert len(exec_summary["stdout"]) == G.MAX_ECHO_BYTES

    def test_two_streams_stay_inside_the_host_log_cap(self, capsys):
        """Both per-stream budgets, JSON-escaped, must fit the host run-log cap so
        the host never slices the summary object in half."""
        import json as _json

        from qwen_train import f2_s8_guest as G
        from qwen_train.f2_s8_pilot import MAX_LOG_BYTES

        worst = b"\x01" * G.MAX_ECHO_BYTES          # escapes to a 6-byte \\u0001
        self._emit(capsys, stdout=worst, stderr=worst)
        # Re-render the same object the way the host would embed it.
        rendered = _json.dumps({
            "stdout": worst[-G.MAX_ECHO_BYTES:].decode("utf-8", "replace"),
            "stderr": worst[-G.MAX_ECHO_BYTES:].decode("utf-8", "replace"),
        })
        assert len(rendered) < MAX_LOG_BYTES
