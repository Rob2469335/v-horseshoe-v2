"""F2 Orchestrator — Derivation, C0, Receipt Primitives.

Implements, at the pure-function level, the F2 orchestrator design contract from
``docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md`` (DESIGN COMPLETE) under the
authorization in ``docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md``.

Scope (authorization §10):
    - deterministic X derivation from the frozen ordered lesson records
      (design §2).  L is identified by ``(position, lesson_id, lesson_hash)``.
    - C0 construction (design §3) — serialized empty-treatment control.
    - arm receipt/evidence schema and validation (design §6-§7).
    - fail-closed validation helpers (design §8).

This module deliberately has NO side effects: it does not call
``render_active_lessons``, does not touch Qdrant, does not spawn processes, and
does not read/write manifests.  It is a pure derivation + schema layer so that
the exact derivation behaviour is unit-testable with deterministic fixtures.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from runtime_v2.services.f2_freeze import (
    FrozenArtifact,
    FreezeVerificationError,
    LessonEntry,
    freeze_artifact,
    verify_manifest,
)

__all__ = [
    "derive_x_from_frozen",
    "build_c0_artifact",
    "source_t_manifest_provenance",
    "new_arm_receipt",
    "validate_arm_receipt",
    "lesson_hash_of",
    "render_block_from_records",
]

RECEIPT_REQUIRED_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "protocol_version",
    "git_sha",
    "task_id",
    "arm",
    "rollout_id",
    "trajectory_run_id",
    "process_identity",
    "treatment_identity",
    "tx_identity",
    "manifest_identity",
    "delivery_identity",
    "lesson_block_hash",
    "final_prompt_hash",
    "delivery_timestamp",
    "execution_timestamps",
    "verification_result",
    "failure_reason",
    "exit_status",
)


def lesson_hash_of(rule_text: str) -> str:
    """SHA-256 hex of a lesson's rule text — matches f2_freeze's LessonEntry contract."""
    import hashlib

    return hashlib.sha256(rule_text.encode("utf-8")).hexdigest()


def render_block_from_records(records: tuple[LessonEntry, ...]) -> str:
    """Render a deterministic ``[BEHAVIORAL LESSONS]`` block from frozen records.

    Uses each record's **original frozen ``position``** as the list number, so a
    removal gap is preserved (design §2.3: "positions must NOT be renumbered").
    This is the ONLY block-text constructor allowed; derivation never re-parses a
    live-rendered block.
    """
    lines = "\n".join(f"{le.position}. {le.rule_text}" for le in records)
    if not lines:
        return ""
    return f"\n\n[BEHAVIORAL LESSONS]\n{lines}"


def source_t_manifest_provenance(t: FrozenArtifact) -> str:
    """Provenance link X→T encoded as a compact JSON string.

    Carried in the X manifest's ``promotion_proof_ref`` field (opaque reference),
    binding X to the frozen T manifest via its content address and manifest hash
    so an auditor can reconstruct X from T (design §2.3 item 7, §4).
    """
    return json.dumps(
        {
            "kind": "source_t_manifest",
            "source_content_address": t.content_address,
            "source_manifest_hash": t.manifest_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def derive_x_from_frozen(t: FrozenArtifact, *, preserve_timestamp: bool = True) -> FrozenArtifact:
    """Deterministically derive the X control artifact from frozen T.

    X = the exact same frozen treatment artifact with ONLY L removed (F0 §3).

    STRICT rules (design §2, authorization §3):
      - L is identified ONLY by its frozen identity ``(position, lesson_id,
        lesson_hash)`` in ``t.ordered_lessons``.  NEVER by rule-text equality.
      - Non-L records are preserved byte-for-byte (id, hash, position, rule_text)
        in original order.  Positions are NOT renumbered; the L gap remains.
      - No live rendering, no ``exclude_ids``, no re-retrieval, no rerank, no
        refill, no backfill, no substitution, no budget recompute.
      - Any malformed/inconsistent frozen record set -> FreezeVerificationError
        (fail closed).

    Raises:
        FreezeVerificationError: on any validation failure.
    """
    verify_manifest(t)
    if t.arm != "T":
        raise FreezeVerificationError(
            f"derive_x_from_frozen: source artifact arm is {t.arm!r}, must be 'T'"
        )
    if not t.lesson_l_id or not t.lesson_l_hash:
        raise FreezeVerificationError("derive_x_from_frozen: L identity missing (lesson_l_id/lesson_l_hash)")

    # -- L identity must be exactly one frozen record matching (position,id,hash) --
    l_matches = [le for le in t.ordered_lessons if le.lesson_id == t.lesson_l_id]
    if not l_matches:
        raise FreezeVerificationError(
            f"derive_x_from_frozen: lesson_l_id {t.lesson_l_id!r} not in ordered_lessons"
        )
    if len(l_matches) > 1:
        raise FreezeVerificationError(
            f"derive_x_from_frozen: lesson_l_id {t.lesson_l_id!r} appears more than once"
        )
    l_rec = l_matches[0]
    if l_rec.lesson_hash != t.lesson_l_hash:
        raise FreezeVerificationError(
            f"derive_x_from_frozen: L hash mismatch for id={t.lesson_l_id!r} "
            f"(record={l_rec.lesson_hash!r}, manifest={t.lesson_l_hash!r})"
        )

    # -- frozen record-set integrity (positions) --
    seen_positions: set[int] = set()
    seen_ids: set[str] = set()
    for le in t.ordered_lessons:
        if le.position < 1:
            raise FreezeVerificationError(
                f"derive_x_from_frozen: malformed position {le.position} for lesson {le.lesson_id!r}"
            )
        if le.position in seen_positions:
            raise FreezeVerificationError(
                f"derive_x_from_frozen: duplicate position {le.position}"
            )
        seen_positions.add(le.position)
        if le.lesson_id in seen_ids:
            raise FreezeVerificationError(
                f"derive_x_from_frozen: duplicate lesson_id {le.lesson_id!r}"
            )
        seen_ids.add(le.lesson_id)
    expected_positions = set(range(1, len(t.ordered_lessons) + 1))
    if seen_positions != expected_positions:
        raise FreezeVerificationError(
            "derive_x_from_frozen: positions not a contiguous 1..N sequence "
            f"(got {sorted(seen_positions)})"
        )

    # -- remove exactly L --
    remaining: tuple[LessonEntry, ...] = tuple(
        le for le in t.ordered_lessons if le.lesson_id != t.lesson_l_id
    )
    if not remaining:
        raise FreezeVerificationError(
            "derive_x_from_frozen: L is the only lesson; X would be empty (ambiguous with C0)"
        )

    # -- block text is derived from remaining frozen records at original positions --
    x_rendered = render_block_from_records(remaining)

    x = freeze_artifact(
        rendered_artifact=x_rendered,
        arm="X",
        ordered_lessons=remaining,
        lesson_l_id=None,  # L removed from X
        lesson_l_hash=None,
        git_sha=t.git_sha,
        model_name=t.model_name,
        task_id=t.task_id,
        experiment_id=t.experiment_id,
        protocol_version=t.protocol_version,
        promotion_proof_ref=source_t_manifest_provenance(t),
        freeze_timestamp=t.freeze_timestamp if preserve_timestamp else time.time(),
    )
    verify_manifest(x)
    return x


def build_c0_artifact(
    *,
    task_id: str,
    git_sha: str = "",
    model_name: str = "",
    experiment_id: str = "experiment_j",
    protocol_version: str = "f2_v1",
    freeze_timestamp: float | None = None,
    task_readiness: dict | None = None,
) -> FrozenArtifact:
    """Construct the C0 no-treatment control artifact (design §3).

    C0 = same task, NO treatment artifact delivered (empty active block), routed
    through the same manifest/verify/install boundary.  It carries no ordered
    lessons and no L identity, so it shares no treatment state with T/X.
    """
    ts = freeze_timestamp if freeze_timestamp is not None else time.time()
    c0 = freeze_artifact(
        rendered_artifact="",
        arm="C0",
        ordered_lessons=(),
        lesson_l_id=None,
        lesson_l_hash=None,
        git_sha=git_sha,
        model_name=model_name,
        task_id=task_id,
        experiment_id=experiment_id,
        protocol_version=protocol_version,
        freeze_timestamp=ts,
        task_readiness=task_readiness,
    )
    verify_manifest(c0)
    return c0


def new_arm_receipt(
    *,
    manifest: FrozenArtifact,
    manifest_path: Path,
    rollout_id: str,
    trajectory_run_id: str,
    process_identity: dict[str, Any],
    delivered_artifact: str,
    lesson_block_hash: str,
    final_prompt_hash: str,
    delivery_timestamp: float,
    verification_result: str,
    failure_reason: str | None = None,
    exit_status: int | None = None,
    execution_start: float | None = None,
    execution_end: float | None = None,
    outcome_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an arm receipt per the design §7 schema.

    This is schema-only; the orchestrator/worker own each field.  No scientific
    metrics beyond the design are introduced.
    """
    return {
        "experiment_id": manifest.experiment_id,
        "protocol_version": manifest.protocol_version,
        "git_sha": manifest.git_sha,
        "task_id": manifest.task_id,
        "arm": manifest.arm,
        "rollout_id": rollout_id,
        "trajectory_run_id": trajectory_run_id,
        "process_identity": process_identity,
        "treatment_identity": {
            "ordered_lesson_ids": [le.lesson_id for le in manifest.ordered_lessons],
            "ordered_lesson_hashes": [le.lesson_hash for le in manifest.ordered_lessons],
            "lesson_l_id": manifest.lesson_l_id,
            "lesson_l_hash": manifest.lesson_l_hash,
        },
        "tx_identity": {
            "content_address": manifest.content_address,
            "treatment_set_hash": manifest.treatment_set_hash,
        },
        "manifest_identity": {
            "content_address": manifest.content_address,
            "manifest_hash": manifest.manifest_hash,
            "manifest_path": str(manifest_path),
            "source_t_manifest": manifest.promotion_proof_ref,
        },
        "delivery_identity": {
            "lesson_block_hash": lesson_block_hash,
            "final_prompt_hash": final_prompt_hash,
            "delivery_timestamp": delivery_timestamp,
        },
        "lesson_block_hash": lesson_block_hash,
        "final_prompt_hash": final_prompt_hash,
        "delivery_timestamp": delivery_timestamp,
        "execution_timestamps": {
            "started": execution_start,
            "completed": execution_end,
        },
        "verification_result": verification_result,
        "failure_reason": failure_reason,
        "exit_status": exit_status,
        "outcome_evidence": outcome_evidence if outcome_evidence is not None else {},
        "delivered_artifact": delivered_artifact,
    }


def validate_arm_receipt(receipt: dict[str, Any]) -> None:
    """Fail-closed validation of an arm receipt.

    Requires the design §7 fields to be present.  Raises ValueError on any
    missing required field.  An invalid/incomplete receipt is a run-level
    failure, never silently interpreted as model behaviour.
    """
    missing = [f for f in RECEIPT_REQUIRED_FIELDS if f not in receipt]
    if missing:
        raise ValueError(f"arm receipt missing required fields: {missing}")


__all__ = [
    "derive_x_from_frozen",
    "build_c0_artifact",
    "source_t_manifest_provenance",
    "new_arm_receipt",
    "validate_arm_receipt",
    "lesson_hash_of",
    "render_block_from_records",
]