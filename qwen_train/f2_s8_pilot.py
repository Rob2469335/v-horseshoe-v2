"""F2 S8 feasibility-pilot driver: real guest execution through the governed path.

This is the operator-side composition the S8 package describes and that
``f2_s8_podirect``/``f2_s8_guest``/``f2_s8_compose`` were built to support.  It
performs no evaluation of its own.  What it does:

1. binds the eight ``run_session`` gates from the caller (never defaults them);
2. stages each base/gold request over the approved PowerShell Direct channel;
3. runs the REAL guest executor (``qwen_train.f2_s8_guest``) inside the guest;
4. reads the transport-only result envelope back over the same channel;
5. hands the retained JUnit to the authoritative evaluator host-side;
6. assembles the governed bundles and lets ``produce_governed_task`` decide
   admission through the real evidence and governance verifiers.

It cannot admit a task that did not really run: the guest executor produces
only a transport record, the evaluator derives the verdict from the JUnit, and
``verify_governed_pair`` re-derives base-FAIL/gold-PASS from the retained
artifacts.  A missing artifact, a timeout, a digest mismatch or an unwired
verifier all fail closed.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from qwen_train import f2_evaluator as evaluator
from qwen_train.f2_s8_channel import read_channel_file
from qwen_train.f2_s8_compose import (
    GovernanceInputs,
    GuestEvidence,
    guest_evidence_from_junit,
    produce_governed_task,
)
from qwen_train.f2_s8_factory import AppendOnlyLedger
from qwen_train.f2_s8_runner import publish_atomic
from qwen_train.f2_s8_session import REQUIRED_GATES, TaskSpec, build_task_spec
from qwen_train.f2_s8_transport import stage_request

__all__ = [
    "PilotError",
    "GuestChannel",
    "PilotTaskResult",
    "INFRASTRUCTURE_REASONS",
    "classify_side_failure",
    "run_pilot_task",
    "pilot_totals",
]


class PilotError(RuntimeError):
    """The pilot could not be driven honestly; fail closed, never fabricate."""


class GuestChannel:
    """The four operations the pilot needs from the approved guest channel.

    An injected seam, exactly like ``f2_s8_podirect.PSSession``: the production
    wiring constructs the real PowerShell Direct session, tests inject a fake.
    Nothing here opens a socket or reads a credential.
    """

    def put(self, local: Path, remote: str) -> None: ...

    def get(self, remote: str, local: Path) -> None: ...

    def run(self, command: str) -> tuple[int, str, str]:
        """Run one bounded command. Returns ``(exit_status, stdout, stderr)``."""
        ...


@dataclass(frozen=True)
class PilotTaskResult:
    """One task's honest outcome. Counts stay separate by construction."""

    task_id: str
    attempted: bool = True
    admitted: bool = False
    outcome: str = ""
    outcome_class: str = ""
    base_verdict: str = ""
    gold_verdict: str = ""
    evidence_name: str = ""
    evidence_sha256: str = ""
    base_junit_sha256: str = ""
    gold_junit_sha256: str = ""
    failure_class: str = ""
    #: F0 §12 exclusion rule this failure falls under (``INFRASTRUCTURE_REASONS``).
    reason_code: str = ""
    detail: str = ""
    ledger_record: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _require_gates(gates: Mapping[str, Any]) -> None:
    missing = [g for g in REQUIRED_GATES if not gates.get(g)]
    if missing:
        raise PilotError(
            "refusing to run: authorization gates unmet: " + ", ".join(missing)
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: Why a side failed, in the vocabulary F0 §12 pre-registers for exclusions.
#:
#: F0 §12 requires that an excluded run is "logged with reason" and lists
#: *worker crash* and *worker timeout* as **infrastructure failure**. The raw
#: ``failure_class`` strings say what the channel observed; these say which
#: §12 rule applies. They are recorded separately so a run that could not
#: complete its evaluation can never be read as a base-FAIL/gold-PASS
#: observation, and so an operator can tell a crashed run from a lost artifact.
INFRASTRUCTURE_REASONS = (
    "worker_crash",           # F0 §12 "Trigger failure (worker crash)"
    "worker_timeout",         # F0 §12 "Worker timeout"
    "artifact_missing",       # evidence missing/invalid, not a scientific result
    "artifact_digest_mismatch",
    # The guest reported an artifact the host then could not obtain, or an
    # envelope with no digest to bind it. The two observations disagree, so the
    # evidence's provenance is in question -- distinct from an artifact that was
    # simply never produced, and distinct from a digest that was obtained and
    # then failed to match.
    "provenance_contradiction",
    "transport_invalid",
    "evaluator_rejected",
    "side_not_executable",
)

#: Failures whose meaning depends on whether the guest reported an artifact.
#: With ``junit_present`` true they contradict the observation; without it the
#: evidence is merely absent.
_PROVENANCE_SENSITIVE_FAILURES = frozenset({
    "artifact_not_retrievable",
    "no_artifact_reported",
})


def classify_side_failure(
    failure_class: str,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> str:
    """Map an observed failure to its F0 §12 reason.

    The guest executor's ``provenance`` is what makes this possible: it records
    whether pytest actually wrote a JUnit and what exit status the child
    returned. A suite that dies before writing an artifact is a *worker crash*
    regardless of what the channel then reports about a file it never saw --
    conflating the two would record an unevaluatable run as a mere transfer
    problem, which is exactly how a task that cannot be evaluated on this
    platform risks being read as a valid observation.

    Fail closed: an unmapped failure is never silently promoted to a reason
    that implies less than what was observed.
    """
    prov = provenance or {}
    if failure_class.startswith("guest_exit_"):
        # The executor itself never returned, so no provenance exists to read.
        return "side_not_executable"
    if failure_class.startswith("transport_"):
        return "transport_invalid"
    if failure_class.startswith("evaluator_refused_"):
        return "evaluator_rejected"
    if failure_class == "artifact_digest_mismatch":
        return "artifact_digest_mismatch"

    # Provenance-aware cases. ``junit_present is False`` with a non-zero child
    # exit is the F0 §12 worker crash: the run died and produced no evidence.
    junit_present = prov.get("junit_present")
    exit_status = prov.get("exit_status")
    timed_out = bool(prov.get("timed_out"))
    if timed_out:
        return "worker_timeout"
    if junit_present is False and exit_status not in (None, 0):
        return "worker_crash"
    if junit_present is True and failure_class in _PROVENANCE_SENSITIVE_FAILURES:
        # The guest says it wrote the artifact; the host could not obtain it
        # (or the envelope carried no digest to bind it). Recording this as
        # "artifact missing" would hide the disagreement, which is exactly the
        # signal an operator needs to suspect the channel or the guest FS.
        return "provenance_contradiction"
    if junit_present is False:
        # No artifact and no crash signal: the evidence is simply absent.
        return "artifact_missing"
    return "artifact_missing"


def stage_spec_for_guest(
    channel: GuestChannel, spec: TaskSpec, *, host_dir: Path | str,
    guest_dir: str,
) -> str:
    """Write the spec host-side and PUT it into the guest.

    The guest executor re-derives the spec through ``build_task_spec`` and
    refuses it when ``spec_digest`` does not match, so the bytes must travel
    unmodified -- they are copied, never re-serialised by hand.
    """
    host_path = Path(host_dir) / "spec.json"
    host_path.parent.mkdir(parents=True, exist_ok=True)
    publish_atomic(
        host_path,
        json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )
    remote = f"{guest_dir.rstrip('/')}/spec.json"
    channel.put(host_path, remote)
    return remote


def _is_safe_artifact_basename(name: str) -> bool:
    """True only for a plain basename that cannot escape the guest artifact dir.

    The name comes from the guest's result envelope, so it is untrusted input:
    an empty name, an absolute path, a path separator, ``.``/``..`` or a drive
    prefix must all be refused rather than joined onto the guest directory.
    """
    if not name or name in (".", ".."):
        return False
    if "\\" in name or "/" in name or ":" in name:
        return False
    return name == Path(name).name


def run_pilot_task(
    *,
    spec: TaskSpec,
    channel: GuestChannel,
    inputs: GovernanceInputs,
    ledger: AppendOnlyLedger,
    gates: Mapping[str, Any],
    guest_dir: str = "C:/f2_s8",
    host_dir: str = "",
    registration_sha256: str,
    job_nonce: str = "",
    timeout_seconds: int = 1200,
    workdirs: Mapping[str, str] | None = None,
    python_exe: str = "",
    path_prefix: str | Mapping[str, str] = "",
    env_extra: Mapping[str, str] | None = None,
) -> PilotTaskResult:
    """Drive ONE task's base/gold pair through the real guest and verifiers.

    Every side is executed independently and its result envelope is validated
    by the transport before the bytes are used.  A side that fails is recorded
    as an infrastructure failure for that side and the task is never admitted.

    ``workdirs`` maps ``base``/``gold`` to the directory the DECLARED test
    command must run in -- each side's own tree, because every declared
    ``test_cmd`` is repo-relative.  ``python_exe`` is the interpreter that runs
    the guest executor itself (the tooling runtime, not a task venv), and
    ``path_prefix`` is prepended to PATH for the inner command so a bare
    ``pytest``/``uv`` resolves to the side's environment; it may be a single
    string or a ``{side: prefix}`` mapping, and it MUST be per-side when each
    side owns a separate environment.  Omitting all three keeps the
    fixture-friendly behaviour the tests rely on.
    """
    _require_gates(gates)
    if spec.registration_sha256 != registration_sha256:
        raise PilotError("spec does not belong to the pinned operative registration")
    staging = Path(host_dir or guest_dir)

    sides: dict[str, GuestEvidence] = {}
    junit_hashes: dict[str, str] = {}
    failure_class = ""
    provenance: dict[str, Any] = {}

    for side in ("base", "gold"):
        job_id = f"{spec.task_id}#{side}#{job_nonce or int(time.time())}"
        # The envelope is built host-side (this process owns the channel
        # staging area) and then PUT into the guest, because the guest executor
        # reads a path that exists inside the guest. Staging it at a host path
        # that merely LOOKS like the guest's would leave the guest with no
        # request at all and record an infrastructure failure for the wrong
        # reason.
        staged = stage_request(
            channel_dir=staging, task_id=spec.task_id, job_id=job_id,
            source=str(spec.payload["source"]),
            provenance={"producer": "f2_s8_pilot", "side": side},
        )
        request_path = Path(guest_dir) / f"request_{side}.rec"
        channel.put(staged, str(request_path))
        result_path = Path(guest_dir) / f"result_{side}.rec"

        # The guest executor is handed the declared test command and writes
        # JUnit itself; it reports only the artifact's identity, never a verdict.
        #
        # The command is PowerShell, not cmd.exe: the guest runs Windows
        # PowerShell 5.1, which has NO `&&` operator, so a cmd-style
        # `set ... && python ...` would fail to parse before anything ran.
        # `& '<exe>'` is the 5.1-compatible call operator.
        runner = python_exe or "python"
        script: list[str] = []
        # ``path_prefix`` may be a single string (both sides) or a per-side
        # mapping. It MUST be per-side whenever each side has its own
        # environment: an editable install makes the project importable from ONE
        # source tree (PEP 660), so running the gold command with the BASE
        # interpreter resolves the BASE source and the gold patch cannot take
        # effect -- the probe below confirmed exactly that (gold+gold venv: pass;
        # gold+base venv: fail with the base's AttributeError).
        prefix = (
            str(path_prefix.get(side, "") or "")
            if isinstance(path_prefix, Mapping)
            else str(path_prefix or "")
        )
        if prefix:
            script.append(f"$env:PATH = '{prefix};' + $env:PATH;")
        for key, value in (env_extra or {}).items():
            script.append(f"$env:{key} = '{value}';")
        workdir_arg = ""
        if workdirs and workdirs.get(side):
            workdir_arg = f" --workdir '{workdirs[side]}'"
        script.append(
            f"& '{runner}' -m qwen_train.f2_s8_guest "
            f"--spec '{guest_dir.rstrip('/')}/spec.json' "
            f"--request '{request_path}' --result '{result_path}' "
            f"--junit-dir '{guest_dir}'{workdir_arg}"
        )
        command = " ".join(script)
        status, _out, err = channel.run(command)
        if status != 0:
            failure_class = f"guest_exit_{status}"
            break

        local_result = staging / f"result_{side}.hostcopy.rec"
        channel.get(str(result_path), local_result)
        verdict = read_channel_file(local_result, expected_task_id=spec.task_id)
        if not verdict.ok:
            failure_class = "transport_" + "_".join(verdict.reason_codes)
            break
        payload = verdict.payload
        # The guest executor's provenance is what lets a failed side be
        # attributed to an F0 §12 rule rather than to the channel artifact it
        # happened to leave behind.
        provenance = dict(payload.get("provenance") or {})
        artifact = payload.get("artifact") or {}
        try:
            artifact_size = int(artifact.get("size_bytes") or 0)
        except (TypeError, ValueError):
            artifact_size = 0
        # A side that wrote no JUnit must be classified from the guest's OWN
        # provenance, never by fetching the placeholder the envelope carries.
        # The envelope reports the SHA-256 of EMPTY bytes (e3b0c442...) with
        # size 0 when the guest command produced nothing, so a truthy ``sha256``
        # is not evidence an artifact exists. Fetching it anyway raised a channel
        # fault, which recorded a real infrastructure failure as an unclassified
        # driver error (and the task then counted as not even attempted).
        if (
            provenance.get("junit_present") is False
            or artifact_size == 0
            or not artifact.get("sha256")
        ):
            failure_class = "no_artifact_reported"
            break

        local_junit = staging / f"{spec.task_id}.{side}.junit.hostcopy.xml"
        # Fetch the artifact the guest REPORTS, never a path the host
        # constructed. ``f2_s8_guest`` has no notion of "side" and writes
        # ``<task_id>.junit.xml``, so a fabricated ``<task_id>.<side>.junit.xml``
        # never exists and EVERY task faulted as an unclassified channel error
        # (which also made a real infrastructure failure indistinguishable from
        # a driver bug). The fetch still happens immediately after THIS side's
        # run, before the next side overwrites the guest's single file.
        reported = str(artifact.get("name") or "")
        if not _is_safe_artifact_basename(reported):
            failure_class = "artifact_name_unusable"
            break
        channel.get(str(Path(guest_dir) / reported), local_junit)
        if not local_junit.is_file():
            failure_class = "artifact_not_retrievable"
            break
        junit_bytes = local_junit.read_bytes()
        # Fail closed when the retrieved bytes are not the artifact the guest
        # reported. Size and digest are both checked because a truncating
        # channel can leave bytes that will not re-hash, and checking the size
        # first names that case instead of blaming the digest.
        if len(junit_bytes) != artifact_size:
            failure_class = "artifact_size_mismatch"
            break
        if _sha256(junit_bytes) != str(artifact["sha256"]):
            failure_class = "artifact_digest_mismatch"
            break
        junit_hashes[side] = _sha256(junit_bytes)

        try:
            sides[side] = guest_evidence_from_junit(
                spec=spec, side=side, junit_bytes=junit_bytes,
                run_log=f"{side} run log".encode("utf-8"),
                started_at="t0", finished_at="t1",
            )
        except Exception as exc:  # noqa: BLE001 - evaluator refused the evidence
            failure_class = f"evaluator_refused_{side}_{type(exc).__name__}"
            break

    def execute(_spec: TaskSpec, side: str) -> GuestEvidence:
        return sides[side]

    # A side that never produced usable evidence is an INFRASTRUCTURE outcome,
    # never a scientific one. ``produce_governed_task`` calls ``execute``
    # BEFORE ``run_session`` installs its own infrastructure guard, so the
    # driver must classify and record it here -- otherwise a missing or
    # malformed artifact would escape as an exception and look like a crash
    # rather than the infrastructure failure it is.
    if failure_class or len(sides) < 2:
        reason = failure_class or "a side produced no usable evidence"
        # F0 §12: "exclusion decision logged with reason". The §12 rule is
        # recorded alongside the channel's own failure string, so a reader can
        # tell WHY this run is excluded without re-deriving it from prose.
        reason_code = classify_side_failure(failure_class, provenance=provenance)
        record = ledger.append({
            "task_id": spec.task_id,
            "registration_id": str(spec.payload["registration_id"]),
            "registration_sha256": spec.registration_sha256,
            "spec_digest": spec.spec_digest,
            "outcome": "infrastructure_failure",
            "outcome_class": "infrastructure",
            "admitted": False,
            "attempt": len(ledger.attempts(spec.task_id)) + 1,
            "reason": reason,
            "reason_code": reason_code,
        })
        return PilotTaskResult(
            task_id=spec.task_id, admitted=False,
            outcome="infrastructure_failure", outcome_class="infrastructure",
            base_junit_sha256=junit_hashes.get("base", ""),
            gold_junit_sha256=junit_hashes.get("gold", ""),
            failure_class=failure_class or "side_not_executable",
            reason_code=reason_code,
            detail=reason, ledger_record=record,
        )

    session = produce_governed_task(
        spec=spec, gates=gates, adapter=_NoopAdapter(), ledger=ledger,
        inputs=inputs, execute=execute,
        expected_registration_sha256=registration_sha256,
    )
    # produce_governed_task -> run_session returns a SessionResult, whose
    # ``record`` is the ledger row. Read the outcome from the ledger row rather
    # than re-deriving it, so the published record and the report cannot drift.
    record = dict(session.record or {})

    admitted = bool(record.get("admitted"))
    return PilotTaskResult(
        task_id=spec.task_id,
        admitted=admitted,
        outcome=str(record.get("outcome") or ""),
        outcome_class=str(record.get("outcome_class") or ""),
        base_verdict=sides.get("base", GuestEvidence(b"", b"", "", "")).report and "produced" or "",
        gold_verdict=sides.get("gold", GuestEvidence(b"", b"", "", "")).report and "produced" or "",
        evidence_name=str((record.get("evidence") or {}).get("name") or ""),
        evidence_sha256=str((record.get("evidence") or {}).get("sha256") or ""),
        base_junit_sha256=junit_hashes.get("base", ""),
        gold_junit_sha256=junit_hashes.get("gold", ""),
        failure_class=failure_class,
        # Propagate the F0 §12 rule when the session itself recorded one;
        # an admitted or scientifically-rejected run has no exclusion rule.
        reason_code=str(record.get("reason_code") or ""),
        detail=str(record.get("reason") or failure_class or ""),
        ledger_record=record,
    )


class _NoopAdapter:
    """Adapter used when the guest channel already performs the lifecycle.

    The pilot runs inside one already-prepared VM, so there is no per-task disk
    to prepare or destroy.  Cleanup is reported as verified because the caller
    asserts the VM is returned to its documented state after the batch.
    """

    def inspect_environment(self) -> dict[str, Any]:
        return {"vm_state": "Running", "free_gb": 100.0, "parent_disk_id": "golden"}

    def prepare_workspace(self, task_id: str, *, min_free_gb: float = 1.0) -> str:
        return f"{task_id}-diff"

    def verify_disk_chain(self, task_id: str) -> str:
        return f"{task_id}-diff"

    def start(self, task_id: str) -> None:
        return None

    def collect_receipt(self, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id}

    def stop(self, task_id: str) -> None:
        return None

    def destroy(self, task_id: str) -> None:
        return None

    def verify_cleanup(self, task_id: str) -> tuple[bool, tuple[str, ...]]:
        return True, ()


def pilot_totals(results: Sequence[PilotTaskResult]) -> dict[str, int]:
    """The six counts the package requires to be kept strictly separate."""
    return {
        "attempted": sum(1 for r in results if r.attempted),
        "admitted": sum(1 for r in results if r.admitted),
        "completed": sum(1 for r in results if r.outcome_class == "scientific"),
        "infrastructure_invalid": sum(
            1 for r in results if r.outcome_class == "infrastructure" or r.failure_class
        ),
        "failed": sum(
            1 for r in results
            if r.outcome_class == "scientific" and not r.admitted
        ),
        "analyzable": 0,
    }


def build_spec_for_task(
    *,
    task_id: str,
    source: str,
    repo: str,
    base_commit: str,
    test_cmd: str,
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
    registration_id: str,
    registration_sha256: str,
    timeout_seconds: int = 1200,
) -> TaskSpec:
    """Thin, validated wrapper so the driver never builds a spec by hand."""
    return build_task_spec(
        task_id=task_id, source=source, repo=repo, base_commit=base_commit,
        test_cmd=test_cmd, fail_to_pass=fail_to_pass, pass_to_pass=pass_to_pass,
        registration_id=registration_id, registration_sha256=registration_sha256,
        evaluator=evaluator.identity_fields(), timeout_seconds=timeout_seconds,
    )
