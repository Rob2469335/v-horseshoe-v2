"""S8 host transport over the approved guest channel (PowerShell Direct).

Authority
---------
Operator decision **D5** (2026-10-10, recorded in ``WORK_LOG.md``) approves
PowerShell Direct + Guest Service Interface **on ``F2-S8-VM1`` only**, with the
operator-held credential ``C:\\Users\\rober\\F2_EVIDENCE\\s8_cred.xml``.  This
module implements the host side of that channel.  It **does not** open a session,
read the credential, start a VM, or execute a task by itself: a session is
injected, and the real :class:`PowerShellDirectSession` is constructed only by an
operator who has the authorization and a running guest.

What is real vs. injected
-------------------------
* **Real and tested here:** request staging, VM/arm/rollout/task identity
  binding, transfer, result-envelope validation, replay detection, artifact
  digest+size verification, JUnit materialisation, bounded failure semantics.
* **Injected (the external seam):** the :class:`PSSession` implementation.  The
  default :class:`PowerShellDirectSession` shells out to ``pwsh`` using
  ``New-PSSession -VMName``; it is **NOT guest-verified** — no live guest was
  used.  Tests inject a simulated session and are labelled as simulations.

Security gate
-------------
This transport proves transport integrity and identity binding only.  It is
**not** an isolation attestation.  No committed authority establishes that a Q9
attestation of ``F2-Isolation-VM`` qualifies ``F2-S8-VM1``; see
``docs/EXPERIMENT_J_F2_S8_VM_AND_CHANNEL_RECONCILIATION.md`` §7.  Isolation
assurance for ``F2-S8-VM1`` remains ``REQUIRES AUTHORIZATION``.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from qwen_train.f2_s8_job import derive_job_id
from qwen_train.f2_s8_runner import RunOutcome, RunnerError, publish_atomic
from qwen_train.f2_s8_session import TaskSpec
from qwen_train.f2_s8_transport import read_result, stage_request

__all__ = [
    "TransportError",
    "SessionRun",
    "PSSession",
    "PowerShellDirectSession",
    "PowerShellDirectTransport",
    "host_vm_identity",
]

EXECUTE_PHASE = "EXECUTE_S8"


class TransportError(RunnerError):
    """The host transport contract was violated; fail closed."""


@dataclass(frozen=True)
class SessionRun:
    """Raw outcome of one bounded command run in the guest.  Never a verdict."""

    exit_status: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False


class PSSession(Protocol):
    """The injected guest-session seam.

    Implementations move files to/from the guest and run one bounded command.
    The transport never inspects guest internals beyond these three operations.
    """

    def put(self, local: Path, remote: str) -> None: ...

    def get(self, remote: str, local: Path) -> None: ...

    def run(self, command: str) -> SessionRun: ...


def host_vm_identity(vm_name: str, *, pwsh: str = "pwsh") -> tuple[str, str]:
    """Read the VM's ``(name, id)`` on the host.  Fail closed on anything else.

    This is the identity anchor: a transport bound to ``F2-S8-VM1`` must refuse
    to run if the named VM resolves to a different id.
    """
    script = (
        f"$v = Get-VM -Name '{vm_name}' -ErrorAction Stop; "
        "@{ name = $v.Name; id = $v.Id.ToString() } | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise TransportError(f"could not read host VM identity: {exc}") from exc
    if proc.returncode != 0:
        raise TransportError(f"host VM identity lookup failed: {proc.stderr.strip()}")
    try:
        payload = json.loads(proc.stdout)
        return str(payload["name"]), str(payload["id"])
    except Exception as exc:  # noqa: BLE001
        raise TransportError(f"host VM identity was not readable JSON: {exc}") from exc


class PowerShellDirectSession:
    """Real PowerShell Direct session driver (NOT guest-verified).

    Uses ``New-PSSession -VMName`` over the Hyper-V VMBus.  A credential is never
    read or stored by this class; when ``-Credential`` is required the operator
    must configure it out of band.  Every operation is a bounded ``pwsh`` call.
    """

    def __init__(self, *, vm_name: str, pwsh: str = "pwsh", timeout_seconds: int = 300) -> None:
        self.vm_name = vm_name
        self.pwsh = pwsh
        self.timeout_seconds = int(timeout_seconds)

    def _script(self, body: str) -> str:
        return (
            f"$ErrorActionPreference='Stop'; $s = New-PSSession -VMName '{self.vm_name}'; "
            f"try {{ {body} }} finally {{ Remove-PSSession -Session $s }}"
        )

    def _run_script(self, body: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.pwsh, "-NoProfile", "-NonInteractive", "-Command", self._script(body)],
            capture_output=True, timeout=self.timeout_seconds,
        )

    def put(self, local: Path, remote: str) -> None:
        proc = self._run_script(
            f"Copy-Item -ToSession $s -LiteralPath '{local}' -Destination '{remote}' -Force"
        )
        if proc.returncode != 0:
            raise TransportError(f"guest put failed: {proc.stderr.decode(errors='replace')}")

    def get(self, remote: str, local: Path) -> None:
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        proc = self._run_script(
            f"Copy-Item -FromSession $s -LiteralPath '{remote}' -Destination '{local}' -Force"
        )
        if proc.returncode != 0:
            raise TransportError(f"guest get failed: {proc.stderr.decode(errors='replace')}")

    def run(self, command: str) -> SessionRun:
        proc = self._run_script(f"Invoke-Command -Session $s -ScriptBlock {{ {command} }}")
        return SessionRun(exit_status=int(proc.returncode), stdout=proc.stdout, stderr=proc.stderr)


class PowerShellDirectTransport:
    """Host-side S8 transport bound to one VM, arm and rollout identity."""

    def __init__(
        self,
        *,
        vm_name: str,
        vm_id: str,
        session: PSSession,
        channel_dir: Path | str,
        output_root: Path | str,
        arm_id: str,
        rollout_id: str,
        guest_dir: str = "C:/f2_s8",
        vm_identity_provider: Callable[[str], tuple[str, str]] | None = None,
        seen_job_ids: set[str] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        for label, value in (
            ("vm_name", vm_name), ("vm_id", vm_id), ("arm_id", arm_id),
            ("rollout_id", rollout_id),
        ):
            if not str(value or "").strip():
                raise TransportError(f"{label} is required")
        if ":" not in guest_dir:
            raise TransportError("guest_dir must be a drive-qualified guest path")
        self.vm_name = vm_name
        self.vm_id = vm_id
        self.session = session
        self.channel_dir = Path(channel_dir)
        self.output_root = Path(output_root)
        self.arm_id = arm_id
        self.rollout_id = rollout_id
        self.guest_dir = guest_dir.rstrip("/")
        self.vm_identity_provider = vm_identity_provider or host_vm_identity
        self.seen_job_ids = seen_job_ids
        self.clock = clock or (lambda: "")

    def _verify_vm_identity(self) -> None:
        name, vid = self.vm_identity_provider(self.vm_name)
        if name != self.vm_name or vid != self.vm_id:
            raise TransportError(
                f"refusing to run: VM identity mismatch (name={name!r} id={vid!r} "
                f"!= bound name={self.vm_name!r} id={self.vm_id!r})"
            )

    def _job_id(self, spec: TaskSpec) -> str:
        return derive_job_id(
            task_id=spec.task_id,
            source=str(spec.payload["source"]),
            population_artifact_sha256=spec.registration_sha256,
            phase=EXECUTE_PHASE,
        )

    def __call__(self, spec: TaskSpec, attempt: int) -> RunOutcome:
        self._verify_vm_identity()
        job_id = self._job_id(spec)
        source = str(spec.payload["source"])
        evidence_dir = str(spec.payload["evidence_dir"])

        # 1. Stage the request envelope and the spec on the host.
        request_path = stage_request(
            channel_dir=self.channel_dir,
            task_id=spec.task_id,
            job_id=job_id,
            source=source,
            provenance={
                "producer": "f2_s8_podirect",
                "vm_name": self.vm_name,
                "vm_id": self.vm_id,
                "arm_id": self.arm_id,
                "rollout_id": self.rollout_id,
                "attempt": int(attempt),
            },
        )
        spec_path = self.channel_dir / "requests" / f"{job_id}.spec.json"
        publish_atomic(
            spec_path,
            json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

        # 2. Push into the guest and run the guest executor.
        guest_spec = f"{self.guest_dir}/spec.json"
        guest_request = f"{self.guest_dir}/request.rec"
        guest_result = f"{self.guest_dir}/result.rec"
        guest_junit_dir = f"{self.guest_dir}/junit"
        guest_junit = f"{guest_junit_dir}/{spec.task_id}.junit.xml"
        self.session.put(spec_path, guest_spec)
        self.session.put(request_path, guest_request)
        command = (
            "python -m qwen_train.f2_s8_guest "
            f"--spec '{guest_spec}' --request '{guest_request}' "
            f"--result '{guest_result}' --junit-dir '{guest_junit_dir}'"
        )
        run = self.session.run(command)
        if not isinstance(run, SessionRun):
            raise TransportError("session.run must return a SessionRun")

        # 3. Retrieve the result envelope and the retained artifact.
        result_local = self.channel_dir / "results" / f"{job_id}.rec"
        junit_local = self.output_root / evidence_dir / f"{spec.task_id}.junit.xml"
        self.session.get(guest_result, result_local)
        self.session.get(guest_junit, junit_local)

        # 4. Validate the result envelope and its artifact.
        verdict = read_result(
            result_local,
            expected_task_id=spec.task_id,
            expected_job_id=job_id,
            expected_source=source,
            seen_job_ids=self.seen_job_ids,
        )
        if not verdict.ok:
            raise TransportError(
                "guest result rejected: " + ", ".join(verdict.reason_codes)
            )
        artifact = verdict.payload.get("artifact") or {}
        if not junit_local.is_file():
            raise TransportError("guest reported an artifact but none was retrieved")
        junit_bytes = junit_local.read_bytes()
        if hashlib.sha256(junit_bytes).hexdigest() != str(artifact.get("sha256")):
            raise TransportError("retrieved JUnit digest does not match the result envelope")
        if len(junit_bytes) != int(artifact.get("size_bytes") or -1):
            raise TransportError("retrieved JUnit size does not match the result envelope")

        now = self.clock()
        return RunOutcome(
            exit_status=int(run.exit_status),
            stdout=run.stdout or b"",
            stderr=run.stderr or b"",
            started_at=now,
            finished_at=now,
        )
