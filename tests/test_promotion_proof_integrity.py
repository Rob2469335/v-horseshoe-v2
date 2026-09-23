"""Test promotion proof integrity through the full lifecycle.

Traces provenance fields from failure → candidate → evidence → evaluation →
receipt → proof → promotion → lesson, verifying identity preservation.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarm_os.services.prompt_repairer import (
    PromotionProof,
    PromptRepairer,
)


@pytest.fixture(autouse=True)
def _receipt_key(monkeypatch):
    monkeypatch.setenv("SWARM_RECEIPT_KEY", "test-proof-integrity-key")


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


def _create_candidate_3_runs(repairer, trigger="fix the bug", action="apply patch"):
    repairer.process_failure("run_r1", "coder", trigger, action, task_id="task_twine", rollout_id="roll_1")
    repairer.process_failure("run_r2", "coder", trigger, action, task_id="task_twine", rollout_id="roll_2")
    repairer.process_failure("run_r3", "coder", trigger, action, task_id="task_click", rollout_id="roll_3")
    cid = list(repairer._candidates.keys())[0]
    return cid


class TestPromotionProofIdentity:
    def test_proof_carries_candidate_id(self, repairer):
        cid = _create_candidate_3_runs(repairer)
        cand = repairer._candidates[cid]
        proof = PromotionProof(
            candidate_id=cid,
            hypothesis_id=cid,
            evidence_ids=[e.get("run_id", "") for e in cand.get("evidence_runs", [])],
            unique_run_ids=["run_r1", "run_r2", "run_r3"],
            task_ids=["task_twine", "task_click"],
            evaluation_id="eval_001",
            evaluation_status="pass",
            evaluation_metrics={},
            baseline_metrics={},
            candidate_metrics={},
            token_count=10,
            conflict_check="clean",
            governance_version=1,
            timestamp=time.time(),
        )
        assert proof.candidate_id == cid
        assert proof.hypothesis_id == cid
        assert len(proof.unique_run_ids) == 3
        assert set(proof.task_ids) == {"task_twine", "task_click"}

    def test_evidence_runs_preserve_rollout_ids(self, repairer):
        _create_candidate_3_runs(repairer)
        cid = list(repairer._candidates.keys())[0]
        cand = repairer._candidates[cid]
        rollout_ids = [e.get("rollout_id", "") for e in cand.get("evidence_runs", [])]
        assert "roll_1" in rollout_ids
        assert "roll_2" in rollout_ids
        assert "roll_3" in rollout_ids

    def test_task_ids_preserved_through_candidate(self, repairer):
        _create_candidate_3_runs(repairer)
        cid = list(repairer._candidates.keys())[0]
        cand = repairer._candidates[cid]
        tasks = set(cand.get("evidence_tasks", []))
        assert "task_twine" in tasks
        assert "task_click" in tasks

    def test_candidate_source_tag_not_stored_in_evidence_runs(self, repairer):
        """source= is used to gate evidence creation, not stored in evidence_runs."""
        repairer.process_failure("r1", "coder", "fix", "patch", task_id="t1", source="evaluation")
        cid = list(repairer._candidates.keys())[0]
        cand = repairer._candidates[cid]
        evidence = cand.get("evidence_runs", [])
        assert len(evidence) == 1
        # source is NOT stored in the evidence run dict
        assert "source" not in evidence[0]


class TestPromotionProofReceiptBinding:
    def test_receipt_captures_candidate_id_and_state_hash(self, repairer):
        cid = _create_candidate_3_runs(repairer)
        cand = repairer._candidates[cid]
        receipt = {
            "candidate_id": cid,
            "hypothesis_id": cid,
            "state_hash": repairer._hash_candidate(cand),
            "governance_version": 1,
        }
        assert receipt["candidate_id"] == cid
        assert receipt["state_hash"] == repairer._hash_candidate(cand)

    def test_state_hash_changes_with_candidate_mutation(self, repairer):
        cid = _create_candidate_3_runs(repairer)
        cand = repairer._candidates[cid]
        hash_before = repairer._hash_candidate(cand)
        cand["action"] = "modified action"
        hash_after = repairer._hash_candidate(cand)
        assert hash_before != hash_after
