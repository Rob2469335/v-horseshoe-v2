"""F2 Orchestrator — Parent Arm Orchestration.

Implements the parent-side responsibilities of the F2 orchestrator design (§4.1):

    governed T creation (render_active_lessons)
    → freeze T
    → derive X (X arm) or build C0 (C0 arm)
    → persist the arm manifest (atomic)
    → establish fresh-child environment (SWARM_F2_* + repo root)
    → spawn fresh child (python -u f2_arm_worker.py)
    → collect child receipt
    → validate receipt (fail closed)
    → return receipt

Contract guarantees enforced here:
  - Ordering is ``render T -> freeze T -> derive X`` (design §4).  X is NEVER
    derived before T is frozen; ``derive_x_from_frozen`` operates only on a
    frozen ``FrozenArtifact`` (see f2_arm_primitives).
  - One fresh child process per arm (F0 §7; design §9).  No multi-arm reuse.
  - Child import contract is established by the WORKER itself
    (``sys.path.insert(0, REPO_ROOT)``); the parent never relies on PYTHONPATH,
    cwd, or editable-install state.
  - Fail-closed: missing/corrupt manifest, invalid X/C0, non-zero child exit,
    and invalid receipt all raise and never fall back to LIVE.
  - Delivery uses the authoritative ``get_delivery_artifact()`` inside the child
    worker; this module does not call ``render_active_lessons`` (that is the
    worker/gated runtime path).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime_v2.services.task_readiness import (
    ReadinessEvidence,
    ReadinessGateError,
    ReadinessVerdict,
    TaskReadiness,
    endpoint_measurable,
    evaluate_readiness,
    manifest_readiness_payload,
)

from runtime_v2.services.f2_freeze import (
    FrozenArtifact,
    FreezeVerificationError,
    LessonEntry,
    verify_manifest,
)
from runtime_v2.services.f2_freeze import persist_manifest

from qwen_train.f2_arm_primitives import (
    build_c0_artifact,
    derive_x_from_frozen,
    lesson_hash_of,
    new_arm_receipt,
    render_block_from_records,
    validate_arm_receipt,
)

# ---------------------------------------------------------------------------
# Arm execution seam
# ---------------------------------------------------------------------------

# Signature of a governed-T renderer. Returns (active_block, ordered_lessons).
# The orchestrator is intentionally agnostic about HOW the governed seam is
# reached: its default path calls LessonManager.render_active_lessons() and
# maps the returned block + sorted active lesson objects into frozen records.
ActiveBlockRenderer = Callable[[], tuple[str, tuple[LessonEntry, ...]]]

# Signature of an arm-execution seam invoked by the child worker (see
# f2_arm_worker.py).  In this first implementation step it is optional; when
# absent the child records execution as delegated.
ArmExecutor = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class ArmResult:
    receipt: dict[str, Any]
    child_exit_code: int
    child_stdout: str
    child_stderr: str
    manifest_path: Path
    readiness: dict[str, Any] | None = None
    readiness_path: Path | None = None


# ---------------------------------------------------------------------------
# Default governed-T renderer (uses the real managed seam)
# ---------------------------------------------------------------------------

async def _default_render_governed_t(
    task_context: str = "",
    max_chars: int = 700,
) -> tuple[str, tuple[LessonEntry, ...]]:
    """Render T through the governed active-lesson path and build frozen records.

    Calls ``LessonManager.render_active_lessons_with_records`` (the ONLY governed
    seam) and builds frozen records (position, id, hash, rule text) from the
    lessons that seam reports as actually delivered.

    The record set MUST come from the seam rather than a second independent
    selection.  A duplicate ``get_all()`` + sort here is only equivalent to the
    rendered block while both execute the same path; the moment selection
    changes (relevance ranking, admission, exclusion) the manifest would attest
    to lessons that were never delivered — silently corrupting F2 provenance.
    """
    from swarm_os.services.lesson_manager import get_lesson_manager

    manager = get_lesson_manager()
    active_block, delivered = await manager.render_active_lessons_with_records(
        task_context, max_chars=max_chars
    )
    records: list[LessonEntry] = []
    for pos, lesson in enumerate(delivered, start=1):
        rule = (lesson.rule or "").strip()
        if not rule:
            continue
        records.append(
            LessonEntry(
                lesson_id=lesson.id,
                lesson_hash=_sha256(rule),
                position=pos,
                rule_text=rule,
            )
        )
    return active_block, tuple(records)


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_f2_arm(
    *,
    arm: str,
    manifest_dir: Path,
    worker_script: Path,
    repo_root: Path,
    render_t: ActiveBlockRenderer,
    lesson_l_id: str,
    lesson_l_hash: str,
    task_id: str,
    git_sha: str = "",
    model_name: str = "",
    experiment_id: str = "experiment_j",
    protocol_version: str = "f2_v1",
    rollout_id: str | None = None,
    trajectory_run_id: str | None = None,
    system_prompt: str = "",
    timeout_s: int = 60,
    execute: bool = False,
    task_prompt: str = "",
    instance_id: str = "",
    agent_id: str = "coder",
    port: int = 8211,
    readiness: TaskReadiness | Mapping[str, Any] | None = None,
    readiness_evidence: ReadinessEvidence | Mapping[str, Any] | None = None,
    base_commit: str = "",
    workspace_root: Path | str | None = None,
) -> ArmResult:
    """Run a single F2 arm in a fresh child process.

    One arm per process (F0 §7; design §9).  Returns an ArmResult containing the
    validated receipt or raises on any fail-closed condition.

    A READINESS GATE runs first (R1-R8). F2 cannot render, freeze, or spawn an
    arm unless the task is mechanically READY; see ``_enforce_readiness``.

    R12: when ``execute`` is set the isolated task workspace is resolved HERE,
    fail-closed, and written explicitly into the child environment, rather than
    being left to ambient environment inheritance. The worker independently
    re-validates it (``resolve_required_workspace_root``), so the two checks are
    independent rather than duplicated trust.
    """
    if arm not in ("T", "X", "C0"):
        raise ValueError(f"invalid arm: {arm!r}")

    if execute:
        # Fail closed BEFORE any freeze/spawn work if the workspace is not
        # explicitly declared. No fallback to ``repo_root`` (the code root is a
        # git repository and would be reset as if it were the task workspace).
        from qwen_train.arm_workspace import resolve_required_workspace_root

        # An explicit argument wins; otherwise the ambient declaration is used.
        # Either way it goes through the SAME fail-closed validation, so there is
        # no path where an unvalidated value reaches the child environment.
        if workspace_root is not None:
            os.environ["SWARM_WORKSPACE_ROOT"] = str(workspace_root)
        resolved_workspace_root = resolve_required_workspace_root(code_root=repo_root)

    # -- 0. READINESS GATE (fail closed) -----------------------------------
    readiness_decl, readiness_verdict = _enforce_readiness(
        readiness=readiness,
        evidence=readiness_evidence,
        task_id=task_id,
        base_commit=base_commit,
    )
    # The gate's inputs are frozen INTO the manifest so the execution seam can
    # enforce the same gate from manifest-verified data (no caller trust).
    _ev_norm = (
        readiness_evidence
        if isinstance(readiness_evidence, ReadinessEvidence)
        else ReadinessEvidence.from_dict(readiness_evidence)
    )
    readiness_payload = manifest_readiness_payload(
        readiness_decl, _ev_norm, readiness_verdict
    )

    manifest_dir = Path(manifest_dir)
    worker_script = Path(worker_script)
    repo_root = Path(repo_root)

    # -- 1. render + freeze T first (or build C0) ---------------------------
    t: FrozenArtifact | None = None
    arm_artifact: FrozenArtifact
    if arm == "C0":
        arm_artifact = build_c0_artifact(
            task_id=task_id,
            git_sha=git_sha,
            model_name=model_name,
            experiment_id=experiment_id,
            protocol_version=protocol_version,
            task_readiness=readiness_payload,
        )
    else:
        active_block, ordered_lessons = render_t()
        t = _freeze_t(
            active_block,
            ordered_lessons,
            lesson_l_id=lesson_l_id,
            lesson_l_hash=lesson_l_hash,
            git_sha=git_sha,
            model_name=model_name,
            task_id=task_id,
            experiment_id=experiment_id,
            protocol_version=protocol_version,
            task_readiness=readiness_payload,
        )
        if arm == "T":
            arm_artifact = t
        elif arm == "X":
            arm_artifact = derive_x_from_frozen(t)  # AFTER T is frozen
        else:  # pragma: no cover - guarded above
            raise ValueError(arm)

    verify_manifest(arm_artifact)

    # -- 2. persist the arm manifest (atomic) -------------------------------
    manifest_path = persist_manifest(arm_artifact, manifest_dir)

    # -- 3. environment + spawn fresh child --------------------------------
    ro = rollout_id or uuid.uuid4().hex
    traj = trajectory_run_id or uuid.uuid4().hex

    # -- 2b. persist the readiness verdict alongside the arm manifest -------
    # Co-located provenance: the canonical TaskReadiness declaration + the
    # canonical ReadinessVerdict, bound to this arm + rollout. This is not a
    # competing representation; it records the existing gate's output so the
    # run can be audited against the task it claims.
    readiness_record = {
        **readiness_decl.to_dict(),
        "arm": arm,
        "rollout_id": ro,
        "readiness": readiness_verdict.to_dict(),
    }
    readiness_path = manifest_path.with_name(manifest_path.stem + "_readiness.json")
    readiness_path.write_text(
        json.dumps(readiness_record, indent=2, sort_keys=True), encoding="utf-8"
    )

    env = dict(os.environ)
    env["SWARM_F2_REPLAY"] = "1"
    env["SWARM_F2_MANIFEST_PATH"] = str(manifest_path)
    env["SWARM_F2_REPO_ROOT"] = str(repo_root)
    env["SWARM_F2_ROLLOUT_ID"] = ro
    env["SWARM_F2_TRAJECTORY_RUN_ID"] = traj
    if execute:
        # R12: the child receives the validated workspace EXPLICITLY, not by
        # ambient inheritance. The worker re-validates it independently, so a
        # mismatch between the two is a fail-closed error rather than a silent
        # divergence.
        env["SWARM_WORKSPACE_ROOT"] = str(resolved_workspace_root)

    cmd = [sys.executable, "-u", str(worker_script), "--manifest", str(manifest_path), "--arm", arm]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    # F2-OP-INFRA-004 §5 (production wiring): the orchestrator is the tracked
    # production caller, so it now carries the STRUCTURED execution seam. Before
    # this, the worker only executed when F2_ARM_EXEC_CMD was set, and nothing in
    # the repository ever set it, so every production arm stopped at "delegated".
    if execute:
        cmd += [
            "--execute",
            "--task-id", task_id or instance_id or "engineering-rehearsal",
            "--instance-id", instance_id,
            "--agent-id", agent_id,
            "--port", str(port),
        ]
        if task_prompt:
            cmd += ["--task-prompt", task_prompt]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(repo_root),
        env=env,
    )
    stdout, stderr = proc.communicate(timeout=timeout_s)
    out_text = stdout.decode("utf-8", errors="replace")
    err_text = stderr.decode("utf-8", errors="replace")

    # -- 4. collect + validate receipt --------------------------------------
    if proc.returncode != 0:
        raise RuntimeError(
            f"F2 arm worker exited {proc.returncode}: {err_text.strip() or out_text.strip()}"
        )

    receipt = _parse_last_json_line(out_text)
    validate_arm_receipt(receipt)

    if receipt.get("arm") != arm:
        raise FreezeVerificationError(
            f"arm receipt arm {receipt.get('arm')!r} != requested {arm!r}"
        )
    if receipt.get("ok") is False:
        raise FreezeVerificationError(f"worker reported failure: {receipt.get('error')}")
    if receipt.get("verification_result") != "verified":
        raise FreezeVerificationError(
            f"worker verification_result {receipt.get('verification_result')!r} != 'verified'"
        )

    return ArmResult(
        receipt=receipt,
        child_exit_code=proc.returncode,
        child_stdout=out_text,
        child_stderr=err_text,
        manifest_path=manifest_path,
        readiness=readiness_record,
        readiness_path=readiness_path,
    )


def _enforce_readiness(
    *,
    readiness: TaskReadiness | Mapping[str, Any] | None,
    evidence: ReadinessEvidence | Mapping[str, Any] | None,
    task_id: str,
    base_commit: str,
) -> tuple[TaskReadiness, ReadinessVerdict]:
    """F2 pre-flight readiness gate. Fail closed on any unmet condition.

    * no declaration / wrong type -> ``ReadinessGateError``
    * malformed declaration / relevant_file_set hash mismatch -> raises
      (``TaskReadiness.from_dict``)
    * task_id mismatch -> ``ReadinessGateError``
    * base_commit mismatch (when supplied) -> ``ReadinessGateError``
    * R8 is DERIVED from the declaration; a caller cannot assert it
    * any R1-R8 not explicitly ``True`` -> ``ReadinessGateError``
    """
    if readiness is None:
        raise ReadinessGateError("no task-readiness declaration: F2 cannot proceed")
    if isinstance(readiness, Mapping):
        readiness = TaskReadiness.from_dict(readiness)
    if not isinstance(readiness, TaskReadiness):
        raise ReadinessGateError(
            f"invalid readiness declaration type: {type(readiness).__name__}"
        )
    if task_id and readiness.task_id != task_id:
        raise ReadinessGateError(
            f"readiness task_id {readiness.task_id!r} != arm task_id {task_id!r}"
        )
    if base_commit and readiness.base_commit != base_commit:
        raise ReadinessGateError(
            f"readiness base_commit {readiness.base_commit!r} != arm base_commit {base_commit!r}"
        )
    ev = (
        evidence
        if isinstance(evidence, ReadinessEvidence)
        else ReadinessEvidence.from_dict(evidence)
    )
    # R8 (endpoint measurability) is a property of the declaration, not of the
    # caller's evidence: derive it so it cannot be claimed without a valid set.
    ev = replace(ev, R8_endpoint_measurable=endpoint_measurable(readiness))
    verdict = evaluate_readiness(ev)
    if not verdict.ready:
        raise ReadinessGateError(
            "task not READY (unmet: " + ", ".join(verdict.unmet) + ")"
        )
    return readiness, verdict


def _freeze_t(
    active_block: str,
    ordered_lessons: tuple[LessonEntry, ...],
    *,
    lesson_l_id: str,
    lesson_l_hash: str,
    git_sha: str,
    model_name: str,
    task_id: str,
    experiment_id: str,
    protocol_version: str,
    task_readiness: dict | None = None,
) -> FrozenArtifact:
    from runtime_v2.services.f2_freeze import freeze_artifact

    t = freeze_artifact(
        rendered_artifact=active_block,
        arm="T",
        ordered_lessons=ordered_lessons,
        lesson_l_id=lesson_l_id,
        lesson_l_hash=lesson_l_hash,
        git_sha=git_sha,
        model_name=model_name,
        task_id=task_id,
        experiment_id=experiment_id,
        protocol_version=protocol_version,
        task_readiness=task_readiness,
    )
    verify_manifest(t)
    return t


def _parse_last_json_line(text: str) -> dict[str, Any]:
    """Extract the worker's JSON receipt from stdout (last JSON object)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise RuntimeError(f"no JSON receipt in worker stdout: {text[:500]!r}")


async def async_default_governed_t(
    task_context: str = "",
    max_chars: int = 700,
) -> tuple[str, tuple[LessonEntry, ...]]:
    """Async wrapper so callers can await the governed render outside a runner."""
    return await _default_render_governed_t(task_context, max_chars=max_chars)


# Re-exported primitives so callers need only import this module.
# (Authorization §10 allows arm_seam/arm-evidence helpers in f2_arm_primitives.)
__all__ = [
    "run_f2_arm",
    "ArmResult",
    "render_block_from_records",
    "lesson_hash_of",
    "derive_x_from_frozen",
    "build_c0_artifact",
    "new_arm_receipt",
    "validate_arm_receipt",
]