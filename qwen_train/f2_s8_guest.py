"""S8 guest-side executor: run one task request INSIDE the selected guest.

This is the counterpart to :mod:`qwen_train.f2_s8_podirect`.  It runs in the
guest, consumes a staged request (the frozen ``F2S8REC1`` envelope validated by
:mod:`qwen_train.f2_s8_channel`), runs the task's declared test command with a
JUnit capture, and publishes a transport-only result envelope carrying the
retained artifact's identity.

Trust boundary
--------------
The result envelope carries **no verdict** — only the artifact's name, digest
and size.  The verdict is derived later, on the host, by the authoritative
evaluator (:func:`qwen_train.f2_s8_compose.guest_evidence_from_junit`).  A guest
can therefore report *that it produced bytes*, never *that the task passed*.

Nothing here opens a network socket, reads a credential, or decides admission.
The command runner is injectable so the protocol is testable without executing a
real task; the default runner is a bounded local subprocess.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qwen_train.f2_evaluator import EvaluatorError, augment_test_command
from qwen_train.f2_s8_channel import read_channel_file
from qwen_train.f2_s8_record import encode_record
from qwen_train.f2_s8_runner import publish_atomic
from qwen_train.f2_s8_session import TaskSpec, build_task_spec
from qwen_train.f2_s8_transport import build_result_payload

__all__ = [
    "GUEST_SCHEMA",
    "GuestExecutorError",
    "CommandResult",
    "load_spec",
    "execute_in_guest",
    "main",
]

GUEST_SCHEMA = "f2_s8_guest_execution_v1"

#: Exit status the bounded runner reports for a timed-out command.
TIMEOUT_EXIT_STATUS = 124

#: Bound on the guest's echoed execution output PER STREAM (bytes). Two streams,
#: JSON-escaped at worst case (\\uXXXX = 6 bytes/char), must stay well inside the
#: host run-log cap (64 KiB) so the host never slices the summary object in half;
#: truncation is reported explicitly.
MAX_ECHO_BYTES = 4 * 1024


class GuestExecutorError(Exception):
    """The guest executor contract was violated."""


@dataclass(frozen=True)
class CommandResult:
    """Raw outcome of one bounded command run.  Never a task verdict."""

    exit_status: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_spec(path: Path | str) -> TaskSpec:
    """Reconstruct and re-validate a task specification from guest-delivered bytes.

    The bytes are untrusted input: they are rebuilt through the frozen
    ``build_task_spec`` validator and the declared ``spec_digest`` must match the
    rebuilt digest.  A spec that does not round-trip is refused.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise GuestExecutorError(f"spec is not readable JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise GuestExecutorError("spec is not a JSON object")
    spec = build_task_spec(
        task_id=str(payload.get("task_id") or ""),
        source=str(payload.get("source") or ""),
        repo=str(payload.get("repo") or ""),
        base_commit=str(payload.get("base_commit") or ""),
        test_cmd=str(payload.get("test_cmd") or ""),
        fail_to_pass=tuple(payload.get("fail_to_pass") or ()),
        pass_to_pass=tuple(payload.get("pass_to_pass") or ()),
        registration_id=str(payload.get("registration_id") or ""),
        registration_sha256=str(payload.get("registration_sha256") or ""),
        evaluator=dict(payload.get("evaluator") or {}),
        timeout_seconds=int(payload.get("timeout_seconds") or 3600),
        max_output_bytes=int(payload.get("max_output_bytes") or 64 * 1024 * 1024),
        evidence_dir=str(payload.get("evidence_dir") or "evidence"),
    )
    if spec.spec_digest != payload.get("spec_digest"):
        raise GuestExecutorError("spec digest does not match the delivered bytes")
    return spec


def _request_side(request: Mapping[str, Any], job_id: str) -> str:
    """The side this request is for, or ``""`` when it cannot be established.

    Taken from the request's own ``provenance.side`` first (that is where the
    pilot puts it), then from the ``<task_id>#<side>#<nonce>`` job id. An
    unresolvable side yields ``""`` -- never a guessed one -- so a caller that
    does not carry a side keeps the historical single-file behaviour.
    """
    provenance = request.get("provenance")
    if isinstance(provenance, Mapping):
        side = str(provenance.get("side") or "").strip().lower()
        if side in ("base", "gold"):
            return side
    parts = str(job_id).split("#")
    if len(parts) >= 2:
        side = parts[1].strip().lower()
        if side in ("base", "gold"):
            return side
    return ""


def _default_run_command(
    command: str, *, cwd: str, timeout_seconds: int
) -> CommandResult:
    """Run ``command`` once, bounded.  A timeout is reported, never a pass.

    ``stdin`` is ``DEVNULL``: without it, a test that reads stdin (or an
    accidental interactive prompt) blocks until the full timeout, which is
    indistinguishable from a hang and wastes the whole task budget.
    """
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True,
            timeout=timeout_seconds, stdin=subprocess.DEVNULL,
        )
        return CommandResult(
            exit_status=int(proc.returncode), stdout=proc.stdout, stderr=proc.stderr
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            exit_status=TIMEOUT_EXIT_STATUS,
            stdout=exc.stdout or b"",
            stderr=exc.stderr or b"",
            timed_out=True,
        )


def _emit_execution_summary(outcome: "CommandResult") -> None:
    """Echo a bounded JSON execution summary (with real stdout/stderr tails).

    The host reads this process's stdout through the guest channel and writes it
    into ``<task>.<side>.run.log``; a guest that stayed silent left the host log
    with empty output sections.
    """
    import json as _json

    def _tail(raw: bytes) -> str:
        return _stream(raw)[0]

    def _stream(raw: bytes) -> tuple[str, int, bool]:
        if not raw:
            return "", 0, False
        return (raw[-MAX_ECHO_BYTES:].decode("utf-8", "replace"),
                len(raw), len(raw) > MAX_ECHO_BYTES)

    out_txt, out_total, out_trunc = _stream(outcome.stdout)
    err_txt, err_total, err_trunc = _stream(outcome.stderr)
    print(_json.dumps({
        "guest_execution": {
            "exit_status": int(outcome.exit_status),
            "timed_out": bool(outcome.timed_out),
            "stdout": out_txt,
            "stdout_total_bytes": out_total,
            "stdout_truncated": out_trunc,
            "stderr": err_txt,
            "stderr_total_bytes": err_total,
            "stderr_truncated": err_trunc,
        }
    }, sort_keys=True), flush=True)


def execute_in_guest(
    *,
    spec_path: Path | str,
    request_path: Path | str,
    result_path: Path | str,
    junit_dir: Path | str,
    workdir: Path | str | None = None,
    run_command: Callable[..., CommandResult] | None = None,
    clock: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Consume one request, run the task, publish a transport-only result.

    Fails closed on a spec that does not round-trip, a request that is missing,
    malformed, replayed, or bound to a different task, and on a command that
    produces no JUnit artifact (the runner then records an infrastructure
    failure — a missing artifact is never a pass).

    ``workdir`` is the directory the declared test command runs in.  Every
    declared ``test_cmd`` in the pinned population is REPO-RELATIVE (node ids
    such as ``tests/test_x.py::test_y``, directories such as
    ``hypothesis-python/tests/cover/``), so it must run at the repository root.
    Running it at ``junit_dir`` resolved nothing and produced no JUnit at all.
    ``--junitxml`` is always an absolute path, so the artifact still lands in
    ``junit_dir`` whatever the working directory is.
    """
    spec = load_spec(spec_path)
    verdict = read_channel_file(request_path, expected_task_id=spec.task_id)
    if not verdict.ok:
        raise GuestExecutorError(
            "staged request rejected: " + ", ".join(verdict.reason_codes)
        )
    request = verdict.payload
    job_id = str(request["job_id"])
    source = str(request["source"])

    # The artifact MUST be per-side. Both sides share one guest directory, and
    # the result envelope reports whatever file is present, so a side-less name
    # meant a gold run that produced NO JUnit re-reported the BASE artifact --
    # the host then fetched it, the evaluator graded it, and a real
    # infrastructure failure was recorded as the scientific outcome
    # ``gold_fail`` (a false result, observed live on hypothesis-4532).
    side = _request_side(request, job_id)
    suffix = f".{side}" if side else ""
    junit_path = Path(junit_dir) / f"{spec.task_id}{suffix}.junit.xml"
    Path(junit_dir).mkdir(parents=True, exist_ok=True)
    # Remove any artifact left by an earlier run of this same side, so a command
    # that produces nothing cannot have the previous run's JUnit attributed to it.
    try:
        junit_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # noqa: BLE001 - fail closed, never continue blind
        raise GuestExecutorError(f"cannot clear stale artifact {junit_path}: {exc}") from exc
    run_cwd = Path(workdir) if workdir is not None else Path(junit_dir)
    if not run_cwd.is_dir():
        raise GuestExecutorError(f"working directory does not exist: {run_cwd}")
    try:
        command = augment_test_command(str(spec.payload["test_cmd"]), str(junit_path))
    except EvaluatorError as exc:
        raise GuestExecutorError(f"declared test command is unusable: {exc}") from exc

    run = run_command or _default_run_command
    outcome = run(
        command, cwd=str(run_cwd), timeout_seconds=int(spec.payload["timeout_seconds"])
    )
    if not isinstance(outcome, CommandResult):
        raise GuestExecutorError("run_command must return a CommandResult")

    # Emit the ACTUAL test-command output to this process's stdout so the host
    # channel captures it into the retained run log. The transport envelope is a
    # record, not a log; without this the captured stdout/stderr would be
    # discarded and the host-side log would carry empty output sections.
    _emit_execution_summary(outcome)

    junit_bytes = junit_path.read_bytes() if junit_path.is_file() else b""
    now = (clock or _utc_now)()
    payload = build_result_payload(
        task_id=spec.task_id,
        job_id=job_id,
        source=source,
        provenance={
            "producer": "f2_s8_guest",
            "spec_digest": spec.spec_digest,
            "exit_status": int(outcome.exit_status),
            "timed_out": bool(outcome.timed_out),
            "junit_present": bool(junit_bytes),
        },
        artifact_name=junit_path.name,
        artifact_sha256=hashlib.sha256(junit_bytes).hexdigest(),
        artifact_size=len(junit_bytes),
        created_at=now,
        note=f"guest command exit_status={int(outcome.exit_status)}",
    )
    publish_atomic(Path(result_path), encode_record(payload))
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="F2 S8 guest-side task executor")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--junit-dir", required=True)
    parser.add_argument(
        "--workdir", default=None,
        help="directory the declared test command runs in (the repository root). "
             "Defaults to --junit-dir for backwards compatibility with fixtures.",
    )
    args = parser.parse_args(argv)
    execute_in_guest(
        spec_path=args.spec,
        request_path=args.request,
        result_path=args.result,
        junit_dir=args.junit_dir,
        workdir=args.workdir,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
