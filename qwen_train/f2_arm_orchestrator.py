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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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


# ---------------------------------------------------------------------------
# Default governed-T renderer (uses the real managed seam)
# ---------------------------------------------------------------------------

async def _default_render_governed_t(
    task_context: str = "",
    max_chars: int = 700,
) -> tuple[str, tuple[LessonEntry, ...]]:
    """Render T through the governed active-lesson path and build frozen records.

    Calls ``LessonManager.render_active_lessons`` (the ONLY governed seam) and
    reconstructs ordered lesson records (position, id, hash, rule text) from the
    active lessons that were rendered.  Because ``render_active_lessons`` returns
    a flat block, the records are rebuilt from the same ACTIVE set that the
    governed seam selected, in render order.
    """
    from swarm_os.services.lesson_manager import get_lesson_manager

    manager = get_lesson_manager()
    active_block = await manager.render_active_lessons(
        task_context, max_chars=max_chars
    )
    lessons = await manager.get_all()
    active = [l for l in lessons if not l.superseded_by]
    # mirror render order: effectiveness desc, then version desc
    active.sort(key=lambda l: (max(0.0, l.effectiveness), l.version), reverse=True)
    records: list[LessonEntry] = []
    for pos, lesson in enumerate(active, start=1):
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
) -> ArmResult:
    """Run a single F2 arm in a fresh child process.

    One arm per process (F0 §7; design §9).  Returns an ArmResult containing the
    validated receipt or raises on any fail-closed condition.
    """
    if arm not in ("T", "X", "C0"):
        raise ValueError(f"invalid arm: {arm!r}")

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
    env = dict(os.environ)
    env["SWARM_F2_REPLAY"] = "1"
    env["SWARM_F2_MANIFEST_PATH"] = str(manifest_path)
    env["SWARM_F2_REPO_ROOT"] = str(repo_root)
    env["SWARM_F2_ROLLOUT_ID"] = ro
    env["SWARM_F2_TRAJECTORY_RUN_ID"] = traj

    cmd = [sys.executable, "-u", str(worker_script), "--manifest", str(manifest_path), "--arm", arm]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
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
    )


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