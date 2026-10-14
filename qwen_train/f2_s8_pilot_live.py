"""Run the F2 S8 feasibility pilot through the governed pipeline in the guest.

This is the operator-side entry point for the five authorized tasks. It wires
the two things every earlier piece assumed and that no test can supply: a real
PowerShell Direct channel, and the operator-provisioned governance inputs.

What actually happens, per task:

* the spec is staged host-side and PUT into the guest, where
  ``f2_s8_guest`` re-derives it through ``build_task_spec`` and refuses it if
  ``spec_digest`` does not match;
* each side runs the DECLARED test command in its OWN tree, in its OWN
  environment, with ``--junitxml`` appended by the authoritative
  ``f2_evaluator.augment_test_command``;
* the transport-only result envelope is read back and the artifact's digest
  re-checked against the retrieved bytes;
* the JUnit goes to ``guest_evidence_from_junit`` -> ``assemble_governed_bundle``
  -> ``produce_governed_task``, where admission is decided by
  ``f2_evidence.verify_task_evidence`` and ``f2_governance.verify_governed_pair``.

Nothing here can declare a task passed. The guest reports bytes; the evaluator
derives the verdict; the governance verifier re-derives base-FAIL/gold-PASS from
the retained artifacts.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Sequence

from qwen_train.f2_s8_compose import load_governance_from_env
from qwen_train.f2_s8_factory import AppendOnlyLedger
from qwen_train.f2_s8_pilot import (
    GuestChannel,
    PilotTaskResult,
    build_spec_for_task,
    pilot_totals,
    run_pilot_task,
    stage_spec_for_guest,
)

#: The eight authorization gates. Every one must be supplied by the caller --
#: this module never defaults a gate to True, because a gate that silently
#: passes is not a gate.
REQUIRED_GATES = (
    "authority_current", "d4_budget_approved", "d4_storage_cap_approved",
    "approved_subnet", "d5_credential_available", "evaluator_identity_named",
    "isolation_verified", "pilot_authorized",
)

TOOLING_PYTHON = r"C:\f2_tooling\python\python.exe"
TOOLING_ROOT = r"C:\f2_tooling"
POSIX_ROOT = r"C:\posix"
UV_ROOT = r"C:\f2_tooling"
GUEST_DIR = "C:/f2_s8"


class PsDirectChannel(GuestChannel):
    """The four channel operations over the D5-approved PowerShell Direct path.

    Each call opens and closes its own session: the host driver is a short-lived
    process, so there is no long-lived run to hold one open across, and the
    session cost (~1.7 s) is negligible next to a multi-minute test run.
    Credential bytes are passed to ``Import-Clixml`` and never logged, printed
    or transmitted.
    """

    def __init__(self, *, vm_name: str, credential_path: str, pwsh: str = "pwsh"):
        self.vm_name = vm_name
        self.credential_path = credential_path
        self.pwsh = pwsh

    def _run_pwsh(self, script: str, *, timeout: int) -> tuple[int, str, str]:
        import subprocess

        proc = subprocess.run(
            [self.pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=timeout,
        )
        return (
            proc.returncode,
            proc.stdout.decode(errors="replace"),
            proc.stderr.decode(errors="replace"),
        )

    def _session_script(self, body: str) -> str:
        return (
            "$ErrorActionPreference='Stop'; "
            f"$cred = Import-Clixml -LiteralPath '{self.credential_path}'; "
            f"$s = New-PSSession -VMName '{self.vm_name}' -Credential $cred; "
            f"try {{ {body} }} finally {{ Remove-PSSession -Session $s; $cred=$null }}"
        )

    def put(self, local: Path, remote: str) -> None:
        script = self._session_script(
            f"Copy-Item -ToSession $s -LiteralPath '{local}' "
            f"-Destination '{remote}' -Force"
        )
        code, out, err = self._run_pwsh(script, timeout=600)
        if code != 0:
            raise RuntimeError(f"channel put failed: {err.strip()[:500]}")

    def get(self, remote: str, local: Path) -> None:
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        script = self._session_script(
            f"Copy-Item -FromSession $s -LiteralPath '{remote}' "
            f"-Destination '{local}' -Force"
        )
        code, out, err = self._run_pwsh(script, timeout=600)
        if code != 0:
            raise RuntimeError(f"channel get failed: {err.strip()[:500]}")

    def run(self, command: str, *, timeout: int = 5400) -> tuple[int, str, str]:
        # The body must be executed INSIDE the guest via Invoke-Command. The
        # session object lives in this host process, so a bare statement list
        # would run locally -- opening a PSSession and then never using it,
        # which silently executed every "guest" command on the host and made
        # host paths look like guest paths.
        #
        # The command travels as base64: interpolating it produced
        # PowerShell-invalid escaping (\" is not an escape in PowerShell), and a
        # quoting bug in the channel is indistinguishable from a failed task.
        # Base64 is alphanumeric plus "+/=", inert inside a single-quoted
        # literal, so the payload cannot be reinterpreted.
        token = base64.b64encode(command.encode("utf-8")).decode("ascii")
        inner = (
            "$cmd = [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{token}')); "
            "$ErrorActionPreference='Continue'; $out=''; $code=0; "
            "try { $out = (Invoke-Expression $cmd 2>&1 | Out-String); "
            "$code = $LASTEXITCODE } catch { $out = ($_ | Out-String); $code = 1 }; "
            "[pscustomobject]@{ code=[int]$code; out=[string]$out } "
            "| ConvertTo-Json -Compress"
        )
        script = self._session_script(
            f"Invoke-Command -Session $s -ScriptBlock {{ {inner} }}"
        )
        code, out, err = self._run_pwsh(script, timeout=timeout)
        if code != 0:
            return code, out, err
        try:
            payload = json.loads(out.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001 - fall back to reporting raw output
            return 1, out, err or "guest did not return a result envelope"
        return int(payload.get("code", 1)), str(payload.get("out", "")), err


def build_specs(manifest: dict[str, Any], inputs_dir: Path,
                registration_sha256: str, registration_id: str) -> list:
    specs = []
    for task_id in manifest:
        entry = manifest[task_id]
        if entry.get("status") != "built":
            continue
        rec = json.loads((inputs_dir / f"{task_id}.json").read_text(encoding="utf-8"))
        specs.append(build_spec_for_task(
            task_id=task_id, source="swe-bench-live", repo=rec["repo"],
            base_commit=rec["base_commit"], test_cmd=rec["test_cmds"][0],
            fail_to_pass=rec["FAIL_TO_PASS"], pass_to_pass=rec["PASS_TO_PASS"],
            registration_id=registration_id,
            registration_sha256=registration_sha256,
            timeout_seconds=3600,
        ))
    return specs


def per_task_env(task_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """The workdir, PATH prefix and env one task's two sides need."""
    entry = manifest[task_id]
    ws = entry["workspace"]
    uses_uv = bool(entry.get("uses_uv"))
    if uses_uv:
        prefix = f"{POSIX_ROOT};{UV_ROOT}"
        env = {"PYTHONPATH": TOOLING_ROOT, "UV_OFFLINE": "1"}
    else:
        prefix = f"{ws}\\.venv\\Scripts;{POSIX_ROOT};{UV_ROOT}"
        env = {"PYTHONPATH": TOOLING_ROOT}
    return {
        "workdirs": {"base": str(Path(ws) / "repo"), "gold": str(Path(ws) / "repo_gold")},
        "python_exe": TOOLING_PYTHON,
        "path_prefix": prefix,
        "env_extra": env,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the five-task S8 feasibility pilot")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--registration-id", required=True)
    ap.add_argument("--registration-sha256", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--staging", required=True, help="host-side staging directory")
    ap.add_argument("--out", required=True, help="directory for the run's results")
    ap.add_argument("--vm", default="F2-S8-VM1")
    ap.add_argument("--credential", default=r"C:\Users\rober\F2_EVIDENCE\s8_cred.xml")
    ap.add_argument("--gates", required=True, help="JSON object of gate_name -> bool")
    ap.add_argument("--nonce", default="")
    ap.add_argument("--only", nargs="*", help="restrict to these task ids")
    args = ap.parse_args(argv)

    gates = json.loads(Path(args.gates).read_text(encoding="utf-8"))
    missing = [g for g in REQUIRED_GATES if g not in gates]
    if missing:
        raise SystemExit(f"REFUSING: gates not supplied at all: {missing}")
    false_gates = [g for g in REQUIRED_GATES if not gates.get(g)]
    if false_gates:
        raise SystemExit(f"REFUSING: gates not satisfied: {false_gates}")

    # Fail closed before touching the guest: the governed path needs a real
    # trusted store and a real authorized evaluator, none of which this module
    # invents.
    inputs = load_governance_from_env()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    specs = build_specs(manifest, Path(args.inputs), args.registration_sha256,
                        args.registration_id)
    if args.only:
        specs = [s for s in specs if s.task_id in set(args.only)]

    staging = Path(args.staging)
    staging.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = AppendOnlyLedger(args.ledger)
    channel = PsDirectChannel(vm_name=args.vm, credential_path=args.credential)

    print(f"pilot: {len(specs)} tasks, ledger={args.ledger}", flush=True)
    results: list[PilotTaskResult] = []
    for spec in specs:
        t0 = time.time()
        env = per_task_env(spec.task_id, manifest)
        stage_spec_for_guest(channel, spec, host_dir=staging, guest_dir=GUEST_DIR)
        try:
            result = run_pilot_task(
                spec=spec, channel=channel, inputs=inputs, ledger=ledger,
                gates=gates, guest_dir=GUEST_DIR, host_dir=str(staging),
                registration_sha256=args.registration_sha256,
                job_nonce=args.nonce or spec.task_id,
                timeout_seconds=3600, **env,
            )
        except Exception as exc:  # noqa: BLE001 - a channel fault, not a verdict
            print(f"  {spec.task_id}: CHANNEL/DRIVER FAULT {type(exc).__name__}: {exc}",
                  flush=True)
            (out_dir / f"{spec.task_id}.fault.txt").write_text(
                f"{type(exc).__name__}: {exc}", encoding="utf-8")
            continue
        results.append(result)
        print(f"  {spec.task_id}: admitted={result.admitted} outcome={result.outcome} "
              f"failure={result.failure_class or '-'} ({int(time.time()-t0)}s)",
              flush=True)

    totals = pilot_totals(results)
    payload = {
        "tasks": [r.as_dict() for r in results],
        "totals": totals,
        "gates": gates,
        "registration_id": args.registration_id,
        "registration_sha256": args.registration_sha256,
        "ledger": str(args.ledger),
        "environment_identity": inputs.environment_identity,
        "evaluator": {
            "evaluator_id": inputs.evaluator.evaluator_id,
            "version": inputs.evaluator.version,
            "implementation_digest": inputs.evaluator.implementation_digest,
            "procedure_id": inputs.evaluator.procedure_id,
        },
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out_path = out_dir / "pilot_results.json"
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(totals, indent=1, sort_keys=True), flush=True)
    print(f"results: {out_path}", flush=True)
    print(f"ledger sha256: {hashlib.sha256(Path(args.ledger).read_bytes()).hexdigest()}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
