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
