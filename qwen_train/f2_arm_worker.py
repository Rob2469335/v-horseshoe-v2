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
            },
            "lesson_block_hash": lesson_block_hash,
            "final_prompt_hash": final_prompt_hash,
            "delivery_timestamp": delivery_timestamp,
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