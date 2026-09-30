"""F2 P2→P1 delivery-evidence transport tests.

Proves that P2-produced delivery evidence (recorded in trajectory JSONL) is
correctly bound to the P1 rollout that produced it and consumed by the F1
worker (P1) into the arm receipt.

Identity contract under test
----------------------------
P1's ``rollout_id`` is minted by the orchestrator and propagated into the fresh
P2 process as ``SWARM_F2_ROLLOUT_ID``. P2 has its own per-invocation
``run_id = uuid4()`` which names the trajectory FILE and is unknown to P1. P2
therefore stamps the rollout identity it was given into the delivery-evidence
record, and P1 selects evidence on that field.

The tests below keep the two identities DISTINCT on purpose: ``run_id`` is never
passed as the reader's ``rollout_id``, because in real execution they differ.

Transport decision: trajectory readback — P2 writes delivery_evidence to
data/trajectories/{run_id}.jsonl at the model-facing delivery seam; P1 reads
it back and uses P2's values. No new IPC subsystem introduced.

These tests are unit-level: they construct trajectory files on disk and call
_read_p2_delivery_evidence / _compute_final_prompt_hash directly. No backend
services are started. No real F2 execution occurs.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

# Bootstrap repo root (matches worker's pattern).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from qwen_train.f2_arm_worker import (  # noqa: E402
    _compute_final_prompt_hash,
    _read_p2_delivery_evidence,
)

# Distinct identity spaces, as in real execution. P1 never knows the run_id.
P1_ROLLOUT_ID = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"  # P1-minted, uuid4().hex
P2_RUN_ID = "9f8e7d6c-5b4a-3210-fedc-ba9876543210"  # P2-minted, str(uuid4())


def _make_traj_dir(tmp: Path) -> Path:
    """Create a trajectory directory under tmp and return it."""
    traj = tmp / "data" / "trajectories"
    traj.mkdir(parents=True, exist_ok=True)
    return traj


def _write_traj_record(traj_dir: Path, record: dict, run_id: str) -> Path:
    """Append one JSONL record to the trajectory file named after run_id."""
    f = traj_dir / f"{run_id}.jsonl"
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return f


def _make_delivery_evidence(
    *,
    arm: str = "T",
    rollout_id: str = P1_ROLLOUT_ID,
    run_id: str = P2_RUN_ID,
    block: str = "use pathlib",
) -> dict:
    """Build the record P2 writes at its delivery seam.

    Shape mirrors agent_service_v2.py's yield: the chunk's identity fields
    (``run_id`` + ``rollout_id``) plus the 9 authoritative fields from
    ``record_f2_delivery_evidence()``.
    """
    return {
        "record_type": "delivery_evidence",
        "type": "f2_delivery_evidence",
        "run_id": run_id,
        "rollout_id": rollout_id,
        "arm": arm,
        "delivered_block": block,
        "lesson_block_hash": hashlib.sha256(block.encode("utf-8")).hexdigest(),
        "final_prompt_hash": hashlib.sha256(("sys_prefix" + block).encode("utf-8")).hexdigest(),
        "delivery_timestamp": time.time(),
        "serving_pid": 4242,
        "serving_start_time": 1790000000.0,
        "manifest_treatment_set_hash": "abc123def456",
        "manifest_content_address": "deadbeefcafe0001",
    }


class TestIdentityBinding:
    """The rollout↔evidence binding itself."""

    def test_correct_rollout_binds_to_its_evidence(self, tmp_path):
        """P1's rollout id selects P2's evidence even though run_id differs."""
        traj = _make_traj_dir(tmp_path)
        ev = _make_delivery_evidence()
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)

        # The reader is given P1's rollout id, NOT the record's run_id.
        assert P1_ROLLOUT_ID != P2_RUN_ID
        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        assert result is not None
        assert result["rollout_id"] == P1_ROLLOUT_ID
        assert result["run_id"] == P2_RUN_ID

    def test_wrong_rollout_rejected(self, tmp_path):
        """Another arm's rollout id selects nothing."""
        traj = _make_traj_dir(tmp_path)
        _write_traj_record(
            traj, _make_delivery_evidence(rollout_id="some-other-rollout"), run_id=P2_RUN_ID
        )

        assert _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID) is None

    def test_stale_foreign_evidence_rejected(self, tmp_path):
        """A prior rollout's evidence in the store is never accepted."""
        traj = _make_traj_dir(tmp_path)
        # Stale evidence from a previous arm, written earlier.
        _write_traj_record(
            traj, _make_delivery_evidence(rollout_id="stale-rollout-0000"), run_id="old-run-uuid"
        )
        # This arm's own evidence, written later (newer mtime).
        _write_traj_record(
            traj, _make_delivery_evidence(rollout_id=P1_ROLLOUT_ID), run_id=P2_RUN_ID
        )

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        assert result is not None
        assert result["rollout_id"] == P1_ROLLOUT_ID
        assert result["serving_pid"] == 4242

    def test_run_id_alone_does_not_satisfy_binding(self, tmp_path):
        """Matching P2's run_id is not enough; the rollout identity governs.

        Guards the original defect: a record whose run_id equals the queried
        value must still be rejected when its rollout identity does not match.
        """
        traj = _make_traj_dir(tmp_path)
        ev = _make_delivery_evidence(rollout_id="different-rollout", run_id=P2_RUN_ID)
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)

        assert _read_p2_delivery_evidence(tmp_path, rollout_id=P2_RUN_ID) is None

    def test_identity_less_evidence_rejected(self, tmp_path):
        """A record with no rollout identity is never selectable."""
        traj = _make_traj_dir(tmp_path)
        ev = _make_delivery_evidence()
        ev.pop("rollout_id")
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)

        assert _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID) is None

    def test_missing_rollout_id_argument_fails_closed(self, tmp_path):
        """No identity to select on → no evidence, even if one exists."""
        traj = _make_traj_dir(tmp_path)
        _write_traj_record(traj, _make_delivery_evidence(), run_id=P2_RUN_ID)

        assert _read_p2_delivery_evidence(tmp_path, rollout_id=None) is None
        assert _read_p2_delivery_evidence(tmp_path, rollout_id="") is None

    def test_conflicting_duplicates_rejected(self, tmp_path):
        """Two disagreeing records for one rollout are ambiguous → rejected."""
        traj = _make_traj_dir(tmp_path)
        first = _make_delivery_evidence()
        second = _make_delivery_evidence(block="something else entirely")
        second["delivery_timestamp"] = first["delivery_timestamp"] + 10.0
        _write_traj_record(traj, first, run_id=P2_RUN_ID)
        _write_traj_record(traj, second, run_id="another-run-uuid")

        assert _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID) is None

    def test_identical_duplicates_collapse(self, tmp_path):
        """An idempotent re-write of the same record is not a conflict."""
        traj = _make_traj_dir(tmp_path)
        ev = _make_delivery_evidence()
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)
        _write_traj_record(traj, dict(ev), run_id=P2_RUN_ID)

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        assert result is not None
        assert result["lesson_block_hash"] == ev["lesson_block_hash"]


class TestFailClosedOnAbsence:
    """Missing / unusable evidence never yields a record."""

    def test_missing_trajectory_dir(self, tmp_path):
        assert _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID) is None

    def test_empty_trajectory_dir(self, tmp_path):
        _make_traj_dir(tmp_path)
        assert _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID) is None

    def test_non_delivery_record_skipped(self, tmp_path):
        """Summary records are not treated as delivery evidence."""
        traj = _make_traj_dir(tmp_path)
        _write_traj_record(
            traj,
            {"record_type": "summary", "run_id": P2_RUN_ID, "rollout_id": P1_ROLLOUT_ID},
            run_id=P2_RUN_ID,
        )
        assert _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID) is None

    def test_malformed_json_line_skipped(self, tmp_path):
        """A corrupt line does not prevent reading the valid record."""
        traj = _make_traj_dir(tmp_path)
        f = traj / f"{P2_RUN_ID}.jsonl"
        f.write_text("{not json\n" + json.dumps(_make_delivery_evidence()) + "\n", encoding="utf-8")

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        assert result is not None
        assert result["rollout_id"] == P1_ROLLOUT_ID


class TestP2AuthorityPreserved:
    """P2's delivery values reach P1 unchanged; P1 does not recompute them."""

    def test_authoritative_values_passed_through(self, tmp_path):
        """Every P2 delivery value survives the readback unmodified."""
        traj = _make_traj_dir(tmp_path)
        ev = _make_delivery_evidence()
        # Deliberately unnatural values so any P1 recomputation would differ.
        ev["final_prompt_hash"] = "DELIBERATE_P2_HASH_NOT_EQUAL_TO_P1"
        ev["lesson_block_hash"] = "DELIBERATE_P2_BLOCK_HASH"
        ev["delivery_timestamp"] = 1234567890.123
        ev["serving_pid"] = 31337
        ev["serving_start_time"] = 1700000123.5
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        assert result is not None
        for field in (
            "lesson_block_hash",
            "final_prompt_hash",
            "delivery_timestamp",
            "serving_pid",
            "serving_start_time",
            "manifest_treatment_set_hash",
            "manifest_content_address",
            "arm",
        ):
            assert result[field] == ev[field], f"P2 value not preserved: {field}"

    def test_p2_value_not_replaced_by_p1_recomputation(self, tmp_path):
        """P2's prompt hash differs from P1's inputs and still wins."""
        traj = _make_traj_dir(tmp_path)
        # P2 hashed a system prompt P1 never sees, so its hash differs from
        # anything P1 could recompute from its own arguments.
        ev = _make_delivery_evidence()
        ev["final_prompt_hash"] = hashlib.sha256(
            "P2_ONLY_SYS_PROMPTuse pathlib".encode("utf-8")
        ).hexdigest()
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        assert result is not None
        assert result["final_prompt_hash"] == ev["final_prompt_hash"]

        # P1's independent computation over its own inputs is a different value,
        # so this assertion also proves the reader did not substitute it.
        p1_hash = _compute_final_prompt_hash("sys_prefix", "use pathlib")
        assert p1_hash != result["final_prompt_hash"]


class TestEvidenceFieldIntegrity:
    """The 9-field authoritative structure is intact after readback."""

    def test_all_nine_fields_present(self, tmp_path):
        traj = _make_traj_dir(tmp_path)
        ev = _make_delivery_evidence()
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        required = [
            "delivered_block", "lesson_block_hash", "final_prompt_hash",
            "delivery_timestamp", "serving_pid", "serving_start_time",
            "arm", "manifest_treatment_set_hash", "manifest_content_address",
        ]
        for field in required:
            assert field in result, f"missing field: {field}"

    def test_hash_consistency(self, tmp_path):
        traj = _make_traj_dir(tmp_path)
        ev = _make_delivery_evidence()
        _write_traj_record(traj, ev, run_id=P2_RUN_ID)

        result = _read_p2_delivery_evidence(tmp_path, rollout_id=P1_ROLLOUT_ID)
        block = result["delivered_block"]
        assert result["lesson_block_hash"] == hashlib.sha256(block.encode("utf-8")).hexdigest()
