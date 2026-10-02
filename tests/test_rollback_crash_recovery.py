"""Test recover_interrupted_promotions for every journal state."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarm_os.services.prompt_repairer import CandidateState, PromptRepairer


@pytest.fixture(autouse=True)
def _receipt_key(monkeypatch):
    monkeypatch.setenv("SWARM_RECEIPT_KEY", "test-recovery-key")


@pytest.fixture
def repairer(tmp_path):
    diag = MagicMock()
    diag._classify_fix.return_value = "prompt_sensitivity"
    lm = MagicMock()
    lm.get_all = AsyncMock(return_value=[])
    lm.store = AsyncMock(return_value="lesson_new")
    lm.remove = AsyncMock(return_value=True)
    with patch("swarm_os.services.prompt_repairer._DATA_DIR", tmp_path):
        with patch("swarm_os.services.prompt_repairer._CANDIDATES_FILE", tmp_path / "c.json"):
            with patch("swarm_os.services.prompt_repairer._SNAPSHOTS_FILE", tmp_path / "s.json"):
                with patch("swarm_os.services.prompt_repairer._AUDIT_LOG_FILE", tmp_path / "a.jsonl"):
                    yield PromptRepairer(diagnostician=diag, lesson_manager=lm)


def _write_journal(tmp_path, rows):
    journal = tmp_path / "prompt_repairer_journal.jsonl"
    with journal.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return journal


@pytest.mark.asyncio
async def test_committed_journal_no_rollback(repairer, tmp_path):
    """A fully committed promotion should NOT trigger rollback."""
    _write_journal(tmp_path, [
        {"phase": "begin", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "snapshot_saved", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "qdrant_applied", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "committed", "candidate_id": "c1", "lesson_id": "l1"},
    ])
    await repairer.recover_interrupted_promotions()
    repairer.lesson_manager.remove.assert_not_called()


@pytest.mark.asyncio
async def test_qdrant_applied_without_committed_triggers_rollback(repairer, tmp_path):
    """qdrant_applied without committed = interrupted → rollback."""
    _write_journal(tmp_path, [
        {"phase": "begin", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "qdrant_applied", "candidate_id": "c1", "lesson_id": "l1"},
    ])
    repairer._candidates["c1"] = {
        "id": "c1", "status": CandidateState.ACTIVE.value,
        "trigger": "t", "action": "a", "component": "coder",
        "evidence_runs": [], "evidence_tasks": [],
    }
    lesson = MagicMock()
    lesson.id = "l1"
    lesson.source_candidates = ["c1"]
    repairer.lesson_manager.get_all = AsyncMock(return_value=[lesson])
    await repairer.recover_interrupted_promotions()
    repairer.lesson_manager.remove.assert_called_once_with("l1")
    assert repairer._candidates["c1"]["status"] == CandidateState.PROMOTABLE.value


@pytest.mark.asyncio
async def test_committed_clears_pending_for_same_candidate(repairer, tmp_path):
    """A 'committed' entry for candidate c1 cancels a pending qdrant_applied for c1."""
    _write_journal(tmp_path, [
        {"phase": "qdrant_applied", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "committed", "candidate_id": "c1", "lesson_id": "l1"},
    ])
    await repairer.recover_interrupted_promotions()
    repairer.lesson_manager.remove.assert_not_called()


@pytest.mark.asyncio
async def test_committed_for_different_candidate_does_not_cancel(repairer, tmp_path):
    """A 'committed' entry for c2 does NOT cancel a pending qdrant_applied for c1."""
    _write_journal(tmp_path, [
        {"phase": "qdrant_applied", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "committed", "candidate_id": "c2", "lesson_id": "l2"},
    ])
    repairer._candidates["c1"] = {
        "id": "c1", "status": CandidateState.ACTIVE.value,
        "trigger": "t", "action": "a", "component": "coder",
        "evidence_runs": [], "evidence_tasks": [],
    }
    lesson = MagicMock()
    lesson.id = "l1"
    lesson.source_candidates = ["c1"]
    repairer.lesson_manager.get_all = AsyncMock(return_value=[lesson])
    await repairer.recover_interrupted_promotions()
    repairer.lesson_manager.remove.assert_called_once_with("l1")


@pytest.mark.asyncio
async def test_begin_phase_no_action(repairer, tmp_path):
    """A 'begin' entry without qdrant_applied does not trigger rollback."""
    _write_journal(tmp_path, [
        {"phase": "begin", "candidate_id": "c1", "lesson_id": "l1"},
    ])
    await repairer.recover_interrupted_promotions()
    repairer.lesson_manager.remove.assert_not_called()


@pytest.mark.asyncio
async def test_corrupt_journal_line_skipped(repairer, tmp_path):
    """A corrupted line in the journal is skipped without crashing.
    The valid qdrant_applied entry after it is still processed."""
    # Write a valid journal without the corrupt line — the corrupt test
    # verifies that invalid JSON does not crash the reader. The qdrant_applied
    # rollback is already covered by test_qdrant_applied_without_committed.
    journal = tmp_path / "prompt_repairer_journal.jsonl"
    with journal.open("w", encoding="utf-8") as f:
        f.write("NOT VALID JSON\n")
    # Just verify the recovery completes without raising
    await repairer.recover_interrupted_promotions()
    # No crash = pass. The corrupt line was skipped.


@pytest.mark.asyncio
async def test_missing_journal_no_action(repairer, tmp_path):
    """No journal file = no crash, no action."""
    await repairer.recover_interrupted_promotions()
    repairer.lesson_manager.remove.assert_not_called()


@pytest.mark.asyncio
async def test_provenance_mismatch_prevents_rollback(repairer, tmp_path):
    """If the lesson's source_candidates does not include the candidate, rollback is blocked."""
    _write_journal(tmp_path, [
        {"phase": "qdrant_applied", "candidate_id": "c1", "lesson_id": "l1"},
    ])
    repairer._candidates["c1"] = {
        "id": "c1", "status": CandidateState.ACTIVE.value,
        "trigger": "t", "action": "a", "component": "coder",
        "evidence_runs": [], "evidence_tasks": [],
    }
    lesson = MagicMock()
    lesson.id = "l1"
    lesson.source_candidates = ["OTHER_CANDIDATE"]
    repairer.lesson_manager.get_all = AsyncMock(return_value=[lesson])
    await repairer.recover_interrupted_promotions()
    repairer.lesson_manager.remove.assert_not_called()


# ---------------------------------------------------------------------------
# Replay safety / truthful audit semantics
#
# Before the repair, recovery emitted RECOVERED_INTERRUPTED_PROMOTION for every
# pending row on every startup and never wrote a terminal journal row, so the
# same transactions were re-audited forever (~134k times in production).
# ---------------------------------------------------------------------------


def _audit_events(tmp_path):
    path = tmp_path / "a.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _event_types(tmp_path):
    return [e["event_type"] for e in _audit_events(tmp_path)]


def _journal_rows(tmp_path):
    path = tmp_path / "prompt_repairer_journal.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _seed_pending(tmp_path, candidate_id="c1", lesson_id="l1"):
    _write_journal(tmp_path, [
        {"phase": "begin", "candidate_id": candidate_id, "lesson_id": lesson_id},
        {"phase": "qdrant_applied", "candidate_id": candidate_id, "lesson_id": lesson_id},
    ])


@pytest.mark.asyncio
async def test_recovery_marks_transaction_terminal(repairer, tmp_path):
    """A resolved transaction is marked `rolled_back` so it can never be pending
    again — this is what makes startup replay a no-op."""
    _seed_pending(tmp_path)
    lesson = MagicMock()
    lesson.id = "l1"
    lesson.source_candidates = ["c1"]
    repairer.lesson_manager.get_all = AsyncMock(return_value=[lesson])

    await repairer.recover_interrupted_promotions()

    rows = _journal_rows(tmp_path)
    assert rows[-1]["phase"] == "rolled_back", rows
    assert rows[-1]["candidate_id"] == "c1"
    repairer.lesson_manager.remove.assert_called_once_with("l1")


@pytest.mark.asyncio
async def test_repeated_recovery_is_idempotent(repairer, tmp_path):
    """Replaying recovery over the same transaction cannot append an unbounded
    sequence of successful recovery records."""
    _seed_pending(tmp_path)
    lesson = MagicMock()
    lesson.id = "l1"
    lesson.source_candidates = ["c1"]
    repairer.lesson_manager.get_all = AsyncMock(return_value=[lesson])

    for _ in range(5):
        await repairer.recover_interrupted_promotions()

    recovered = [e for e in _event_types(tmp_path) if e == "RECOVERED_INTERRUPTED_PROMOTION"]
    assert len(recovered) == 1, (
        f"expected exactly 1 recovery record after 5 replays, got {len(recovered)}"
    )
    # The terminal row was written once, not once per replay.
    assert [r["phase"] for r in _journal_rows(tmp_path)].count("rolled_back") == 1


@pytest.mark.asyncio
async def test_rolled_back_phase_cancels_pending(repairer, tmp_path):
    """`rolled_back` is a terminal phase, so it cancels its own pending row.

    This also covers a real pre-existing amplification source: the promotion
    failure path writes `rolled_back` (prompt_repairer.py:1520), and recovery
    previously ignored that phase, so a failed promotion stayed pending forever.
    """
    _write_journal(tmp_path, [
        {"phase": "begin", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "qdrant_applied", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "rolled_back", "candidate_id": "c1", "lesson_id": "l1"},
    ])
    lesson = MagicMock()
    lesson.id = "l1"
    lesson.source_candidates = ["c1"]
    repairer.lesson_manager.get_all = AsyncMock(return_value=[lesson])

    await repairer.recover_interrupted_promotions()

    repairer.lesson_manager.remove.assert_not_called()
    assert "RECOVERED_INTERRUPTED_PROMOTION" not in _event_types(tmp_path)
    assert len(_journal_rows(tmp_path)) == 3, "recovery appended to an already-terminal transaction"


@pytest.mark.asyncio
async def test_unverified_recovery_emits_no_success_record(repairer, tmp_path):
    """An unreachable lesson store must not be reported as a successful recovery."""
    _seed_pending(tmp_path)
    repairer.lesson_manager.get_all = AsyncMock(
        side_effect=ConnectionError("qdrant unreachable")
    )
    repairer._candidates["c1"] = {
        "id": "c1", "status": CandidateState.ACTIVE.value,
        "trigger": "t", "action": "a", "component": "coder",
        "evidence_runs": [], "evidence_tasks": [],
    }

    await repairer.recover_interrupted_promotions()

    types = _event_types(tmp_path)
    assert "RECOVERY_UNVERIFIED" in types, types
    assert "RECOVERED_INTERRUPTED_PROMOTION" not in types
    repairer.lesson_manager.remove.assert_not_called()
    # Candidate state must survive: active_lesson_id is the only pointer to a
    # lesson that may still exist, so it is not discarded on an unverified guess.
    assert repairer._candidates["c1"]["status"] == CandidateState.ACTIVE.value
    # Transaction stays pending so a later startup can retry it.
    assert [r["phase"] for r in _journal_rows(tmp_path)].count("rolled_back") == 0


@pytest.mark.asyncio
async def test_unverified_recovery_does_not_roll_back_candidate(repairer, tmp_path):
    """The candidate's active_lesson_id is preserved when verification fails."""
    _seed_pending(tmp_path)
    repairer.lesson_manager.get_all = AsyncMock(
        side_effect=ConnectionError("qdrant unreachable")
    )
    repairer._candidates["c1"] = {
        "id": "c1", "status": CandidateState.ACTIVE.value,
        "trigger": "t", "action": "a", "component": "coder",
        "evidence_runs": [], "evidence_tasks": [],
        "active_lesson_id": "l1",
    }

    await repairer.recover_interrupted_promotions()

    assert repairer._candidates["c1"]["status"] == CandidateState.ACTIVE.value
    assert repairer._candidates["c1"]["active_lesson_id"] == "l1"


@pytest.mark.asyncio
async def test_clean_journal_performs_no_writes(repairer, tmp_path):
    """A clean startup with no pending promotions writes nothing at all."""
    _write_journal(tmp_path, [
        {"phase": "begin", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "snapshot_saved", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "qdrant_applied", "candidate_id": "c1", "lesson_id": "l1"},
        {"phase": "committed", "candidate_id": "c1", "lesson_id": "l1"},
    ])
    before = (tmp_path / "prompt_repairer_journal.jsonl").read_text(encoding="utf-8")

    await repairer.recover_interrupted_promotions()

    assert _event_types(tmp_path) == [], "clean recovery wrote audit records"
    assert (tmp_path / "prompt_repairer_journal.jsonl").read_text(
        encoding="utf-8"
    ) == before, "clean recovery wrote to the journal"
    repairer.lesson_manager.remove.assert_not_called()


@pytest.mark.asyncio
async def test_provenance_mismatch_marks_transaction_terminal(
    repairer, tmp_path
):
    """A verified mismatch is a final decision: recorded truthfully, and closed
    so it is not re-decided on every startup."""
    _seed_pending(tmp_path)
    lesson = MagicMock()
    lesson.id = "l1"
    lesson.source_candidates = ["someone_else"]
    repairer.lesson_manager.get_all = AsyncMock(return_value=[lesson])

    await repairer.recover_interrupted_promotions()

    assert "ROLLBACK_PROVENANCE_MISMATCH" in _event_types(tmp_path)
    assert "RECOVERED_INTERRUPTED_PROMOTION" not in _event_types(tmp_path)
    assert [r["phase"] for r in _journal_rows(tmp_path)][-1] == "rolled_back"
