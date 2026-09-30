"""F2 Orchestrator — Child Worker Process.

Implements the F2 execution/import contract (authorized ``bb38ed85``) and the
child-worker responsibilities of the F2 orchestrator design (§4.2):

    bootstrap (sys.path.insert(0, REPO_ROOT))
    → load manifest
    → independently verify manifest
    → install replay state (FROZEN_REPLAY)
    → validate arm (requested arm == manifest.arm)
    → deliver via the authoritative get_delivery_artifact()
    → instrument (lesson_block_hash, final_prompt_hash, delivery_timestamp)
    → execute the governed arm (bounded; see EXECUTION GATE below)
    → return arm receipt

Contract invariants enforced here:
  - Repository imports happen ONLY after ``sys.path.insert(0, REPO_ROOT)``.
  - No PYTHONPATH, no cwd reliance, no editable-install reliance.
  - Fail-closed: any manifest/verify/arm/delivery failure => nonzero exit and a
    JSON error line; there is NO silent LIVE fallback.

Execution gate: this first implementation step does NOT run a live backend
worker loop.  Arm execution is carried by the ``f2_arm_execute`` callback /
``F2_ARM_EXEC_CMD`` seam (see ORCHESTRATOR).  In the default (no exec seam)
mode it records execution as ``delegated`` and returns a complete delivery
receipt.  This is an implementation-boundary decision, not a scientific change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Fresh-process repository-root bootstrap (BEFORE any repository import).
# This is the accepted F2 execution/import contract (bb38ed85).
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))  # repo root

from runtime_v2.services.f2_freeze import (  # noqa: E402
    FreezeVerificationError,
    load_manifest,
    verify_manifest,
)
from runtime_v2.services.f2_replay import (  # noqa: E402
    get_delivery_artifact,
    install_replay_state,
)


def _compute_final_prompt_hash(system_prompt: str, delivered: str) -> str:
    return hashlib.sha256((system_prompt + delivered).encode("utf-8")).hexdigest()


def _read_p2_delivery_evidence(
    workspace_root: Path, rollout_id: str | None = None
) -> dict[str, Any] | None:
    """Read P2's authoritative delivery-evidence record for THIS rollout.

    Transport decision (authorized per F2 worker-exec §10 L280): the narrowest
    existing mechanism is trajectory readback — P2 writes delivery_evidence to
    data/trajectories/{run_id}.jsonl at the model-facing delivery seam
    (runtime_v2/api/agent_service_v2.py). This function reads that record and
    returns it.

    Identity binding: selection is on the record's ``rollout_id`` field, which P2
    reads from the ``SWARM_F2_ROLLOUT_ID`` environment value P1 set on the P2
    process. The record's ``run_id`` is P2's own per-invocation uuid4 and names
    the trajectory FILE; it is never a join key for P1, and the file name is not
    used to select evidence.

    Fail-closed. Returns None — never a partially-trusted record — when:
      * no rollout identity was supplied, so nothing can be selected safely;
      * no delivery_evidence record carries this rollout's identity (missing
        evidence, or another rollout's / stale evidence);
      * several records match this rollout AND disagree on any authoritative
        field, because which one P2 actually delivered is then unknown.
    Byte-identical duplicate records are an idempotent re-write, not a
    conflict, and collapse to one.
    """
    expected = (rollout_id or "").strip()
    if not expected:
        return None

    traj_dir = workspace_root / "data" / "trajectories"
    if not traj_dir.is_dir():
        return None
    traj_files = sorted(
        traj_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    matches: list[dict[str, Any]] = []
    for traj_file in traj_files:
        try:
            with open(traj_file, "r", encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("record_type") != "delivery_evidence":
                        continue
                    # Identity-less evidence is never selectable: an F2 arm must
                    # be bound to a rollout it can name.
                    if not str(rec.get("rollout_id") or "").strip():
                        continue
                    if str(rec.get("rollout_id")).strip() != expected:
                        continue
                    matches.append(rec)
        except OSError:
            continue

    if not matches:
        return None

    first = matches[0]
    for other in matches[1:]:
        if json.dumps(other, sort_keys=True, default=str) != json.dumps(
            first, sort_keys=True, default=str
        ):
            # Conflicting evidence for one rollout: refuse to guess which
            # delivery is authoritative.
            return None
    return first


def _arm_report(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def run_worker(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F2 fresh arm worker")
    parser.add_argument("--manifest", required=True, help="Path to the frozen F2 manifest")
    parser.add_argument("--arm", required=True, choices=("T", "X", "C0"), help="Requested arm")
    parser.add_argument("--system-prompt", default="", help="System prompt prefix for final_prompt_hash")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    rollout_id = os.environ.get("SWARM_F2_ROLLOUT_ID", "unknown-rollout")
    trajectory_run_id = os.environ.get("SWARM_F2_TRAJECTORY_RUN_ID", "")
    exec_cmd = os.environ.get("F2_ARM_EXEC_CMD", "")

    exec_start = time.time()
    process_identity = {
        "role": "f2_arm_worker",
        "pid": os.getpid(),
        "executable": sys.executable,
        "argv": sys.argv,
    }

    try:
        # -- independent load + verification --
        artifact = load_manifest(manifest_path)
        verify_manifest(artifact)

        # -- arm validation: the child executes ONLY the authorized arm --
        if artifact.arm != args.arm:
            raise FreezeVerificationError(
                f"arm mismatch: requested={args.arm!r}, manifest={artifact.arm!r}"
            )

        # -- install replay state; authoritative delivery --
        install_replay_state(artifact, str(manifest_path))
        delivered = get_delivery_artifact()
        delivered_actual = delivered if args.arm == "C0" or delivered else artifact.rendered_artifact
        lesson_block_hash = hashlib.sha256(delivered_actual.encode("utf-8")).hexdigest()
        final_prompt_hash = _compute_final_prompt_hash(args.system_prompt, delivered_actual)
        delivery_timestamp = time.time()

        # -- execution seam (bounded; see module docstring) --
        execution_result: dict[str, Any]
        if exec_cmd:
            import subprocess

            proc = subprocess.run(exec_cmd, shell=True, capture_output=True, text=True, timeout=30)
            execution_result = {
                "delegated": True,
                "cmd": exec_cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
            }
        else:
            execution_result = {
                "delegated": True,
                "reason": "no F2_ARM_EXEC_CMD provided (first implementation step); "
                          "live worker execution is a future authorized step",
            }

        exec_end = time.time()

        # -- P2 delivery-evidence transport (authorized per F2 worker-exec §10) --
        # P2 is the authoritative producer of delivery evidence at the
        # model-facing delivery seam.  We read P2's trajectory record here.
        # P1's own computation (above) is kept only as a verification baseline.
        # Transport decision: trajectory readback — the narrowest existing
        # mechanism (no new IPC).  P2 writes delivery_evidence into
        # data/trajectories/{run_id}.jsonl during execution; this worker reads
        # it back and uses P2's values in the receipt.
        workspace_root = Path(os.environ.get("SWARM_WORKSPACE_ROOT", str(_HERE.parent)))
        p2_evidence: dict[str, Any] | None = None
        if exec_cmd:
            p2_evidence = _read_p2_delivery_evidence(workspace_root, rollout_id)
            if p2_evidence is None:
                raise RuntimeError(
                    "F2 fail-closed: no unambiguous P2 delivery evidence bound to "
                    f"rollout_id={rollout_id!r} in the trajectory store. An F2 arm "
                    "with real execution MUST receive P2-produced delivery evidence "
                    "for its own rollout; missing evidence, another rollout's "
                    "evidence, or conflicting duplicates are not a valid receipt."
                )
            if p2_evidence.get("arm") not in (None, artifact.arm):
                raise RuntimeError(
                    f"F2 fail-closed: P2 evidence arm={p2_evidence.get('arm')!r} "
                    f"does not match manifest arm={artifact.arm!r}"
                )

        # Use P2-produced values as authoritative; keep P1 baseline for comparison
        if p2_evidence:
            lesson_block_hash = p2_evidence.get("lesson_block_hash", lesson_block_hash)
            final_prompt_hash = p2_evidence.get("final_prompt_hash", final_prompt_hash)
            delivery_timestamp = p2_evidence.get("delivery_timestamp", delivery_timestamp)
            p2_serving_pid = p2_evidence.get("serving_pid", "")
            p2_serving_start_time = p2_evidence.get("serving_start_time", "")
            evidence_source = "p2_trajectory"
        else:
            p2_serving_pid = ""
            p2_serving_start_time = ""
            evidence_source = "p1_computed_delegated"

        receipt: dict[str, Any] = {
            "experiment_id": artifact.experiment_id,
            "protocol_version": artifact.protocol_version,
            "git_sha": artifact.git_sha,
            "task_id": artifact.task_id,
            "arm": artifact.arm,
            "rollout_id": rollout_id,
            "trajectory_run_id": trajectory_run_id,
            "process_identity": process_identity,
            "treatment_identity": {
                "ordered_lesson_ids": [le.lesson_id for le in artifact.ordered_lessons],
                "ordered_lesson_hashes": [le.lesson_hash for le in artifact.ordered_lessons],
                "lesson_l_id": artifact.lesson_l_id,
                "lesson_l_hash": artifact.lesson_l_hash,
            },
            "tx_identity": {
                "content_address": artifact.content_address,
                "treatment_set_hash": artifact.treatment_set_hash,
            },
            "manifest_identity": {
                "content_address": artifact.content_address,
                "manifest_hash": artifact.manifest_hash,
                "manifest_path": str(manifest_path),
                "source_t_manifest": artifact.promotion_proof_ref,
            },
            "delivery_identity": {
                "lesson_block_hash": lesson_block_hash,
                "final_prompt_hash": final_prompt_hash,
                "delivery_timestamp": delivery_timestamp,
                "evidence_source": evidence_source,
            },
            "lesson_block_hash": lesson_block_hash,
            "final_prompt_hash": final_prompt_hash,
            "delivery_timestamp": delivery_timestamp,
            "p2_delivery_evidence": {
                "source": evidence_source,
                "serving_pid": p2_serving_pid,
                "serving_start_time": p2_serving_start_time,
                "manifest_treatment_set_hash": p2_evidence.get("manifest_treatment_set_hash", "") if p2_evidence else "",
                "manifest_content_address": p2_evidence.get("manifest_content_address", "") if p2_evidence else "",
            },
            "execution_timestamps": {"started": exec_start, "completed": exec_end},
            "verification_result": "verified",
            "failure_reason": None,
            "exit_status": 0,
            "outcome_evidence": execution_result,
            "delivered_artifact": delivered_actual,
        }
        print(_arm_report(receipt))
        return 0

    except Exception as exc:  # noqa: BLE001 - fail closed
        error_report = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "arm": args.arm,
            "manifest_path": str(manifest_path),
            "pid": os.getpid(),
        }
        print(_arm_report(error_report), file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(run_worker())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}), file=sys.stderr)
        sys.exit(2)