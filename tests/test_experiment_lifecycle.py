"""Test evaluate_and_promote_eligible — multi-candidate lifecycle."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarm_os.services.prompt_repairer import CandidateState, PromptRepairer


@pytest.fixture(autouse=True)
def _receipt_key(monkeypatch):
    monkeypatch.setenv("SWARM_RECEIPT_KEY", "test-lifecycle-key")


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


def _create_eligible_candidate(repairer, task_id="taskA", trigger="fix", action="patch"):
    repairer.process_failure("r1", "coder", trigger, action, task_id=task_id, rollout_id="roll1")
    repairer.process_failure("r2", "coder", trigger, action, task_id=task_id, rollout_id="roll2")
    repairer.process_failure("r3", "coder", trigger, action, task_id="other_task", rollout_id="roll3")
    cid = list(repairer._candidates.keys())[0]
    cand = repairer._candidates[cid]
    cand["status"] = CandidateState.CANDIDATE.value
    return cid


class TestLifecycleIsolation:
    @pytest.mark.asyncio
    async def test_only_candidates_enter_tick(self, repairer):
        """Non-CANDIDATE status entries are not counted as considered or skipped."""
        _create_eligible_candidate(repairer)
        c1 = list(repairer._candidates.keys())[0]
        repairer._candidates[c1]["status"] = CandidateState.EVIDENCE_GATHERING.value
        result = await repairer.evaluate_and_promote_eligible()
        assert result["considered"] == 0
        # EVIDENCE_GATHERING is silently skipped (not counted in any counter)
        assert result["skipped"] == 0

    @pytest.mark.asyncio
    async def test_cooldown_prevents_immediate_reeval(self, repairer):
        """A candidate evaluated recently is skipped on the next tick."""
        cid = _create_eligible_candidate(repairer)
        repairer._candidates[cid]["last_eval_attempt"] = time.time()
        result = await repairer.evaluate_and_promote_eligible()
        assert result["skipped"] >= 1


class TestEligibilityChecks:
    @pytest.mark.asyncio
    async def test_insufficient_runs_skipped(self, repairer):
        """Candidate with only 1 evidence run is skipped (needs 3)."""
        repairer.process_failure("r1", "coder", "fix", "patch", task_id="t1", rollout_id="roll1")
        cid = list(repairer._candidates.keys())[0]
        repairer._candidates[cid]["status"] = CandidateState.CANDIDATE.value
        result = await repairer.evaluate_and_promote_eligible()
        assert result["considered"] == 0

    @pytest.mark.asyncio
    async def test_insufficient_tasks_skipped(self, repairer):
        """Candidate with runs on only 1 task is skipped (needs 2)."""
        repairer.process_failure("r1", "coder", "fix", "patch", task_id="t1", rollout_id="roll1")
        repairer.process_failure("r2", "coder", "fix", "patch", task_id="t1", rollout_id="roll2")
        repairer.process_failure("r3", "coder", "fix", "patch", task_id="t1", rollout_id="roll3")
        cid = list(repairer._candidates.keys())[0]
        repairer._candidates[cid]["status"] = CandidateState.CANDIDATE.value
        result = await repairer.evaluate_and_promote_eligible()
        assert result["considered"] == 0


class TestCandidateIsolation:
    @pytest.mark.asyncio
    async def test_two_candidates_evaluated_independently(self, repairer):
        """Two distinct CANDIDATE entries are both processed in one tick.
        Without an evaluator, both get EVALUATION_FAILED independently."""
        cid1 = _create_eligible_candidate(repairer, task_id="tA",
                                          trigger="fix json formatting", action="apply json schema")
        cid2 = _create_eligible_candidate(repairer, task_id="tB",
                                          trigger="fix timeout handling", action="add retry logic")
        repairer._candidates[cid1]["status"] = CandidateState.CANDIDATE.value
        repairer._candidates[cid2]["status"] = CandidateState.CANDIDATE.value
        result = await repairer.evaluate_and_promote_eligible()
        assert result["considered"] >= 2
        # Both should fail evaluation (no evaluator) or skip — not contaminate each other
        for cid in [cid1, cid2]:
            status = repairer._candidates[cid]["status"]
            assert status in [
                CandidateState.CANDIDATE.value,
                CandidateState.EVALUATION_FAILED.value,
            ]


class TestEvidenceIdentityCount:
    """Regression: MIN_EVIDENCE_RUNS counts distinct evidence identities per
    the Evidence Model — non-empty rollout_id preferred, run_id fallback.

    The evaluation bridge supplies real rollout_ids but an unusable run_id
    ("unknown" on the CLI evaluator path). Evidence must be counted on the
    rollout identity, or bridge-fed candidates can never become eligible.
    """

    vi = "use filesystem patch and verify with sandbox_repl"

    @pytest.mark.asyncio
    async def test_bridge_shaped_rollout_evidence_is_eligible(self, repairer):
        """3 distinct rollout_ids (run_id='unknown') across 2 tasks must become
        eligible for evaluation — not skipped for insufficient evidence."""
        repairer.process_failure("unknown", "coder", "t", self.vi,
                                 task_id="pypa__twine-1066", source="evaluation",
                                 rollout_id="rollA")
        repairer.process_failure("unknown", "coder", "t", self.vi,
                                 task_id="pypa__twine-1066", source="evaluation",
                                 rollout_id="rollB")
        repairer.process_failure("unknown", "coder", "t", self.vi,
                                 task_id="pallets__click-2380", source="evaluation",
                                 rollout_id="rollC")
        cid = list(repairer._candidates.keys())[0]
        assert repairer._candidates[cid]["status"] == CandidateState.CANDIDATE.value

        async def pass_eval(c):
            return {"pass": True}

        repairer.evaluator = pass_eval
        summary = await repairer.evaluate_and_promote_eligible()
        assert summary["considered"] == 1
        assert summary["skipped"] == 0
        assert repairer._candidates[cid]["status"] == CandidateState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_legacy_run_id_fallback_still_counts_three(self, repairer):
        """No usable rollout_id → 3 distinct run_ids still count as 3."""
        repairer.process_failure("run1", "coder", "t", self.vi, task_id="t1")
        repairer.process_failure("run2", "coder", "t", self.vi, task_id="t2")
        repairer.process_failure("run3", "coder", "t", self.vi, task_id="t3")
        cid = list(repairer._candidates.keys())[0]
        assert repairer._candidates[cid]["status"] == CandidateState.CANDIDATE.value

        async def pass_eval(c):
            return {"pass": True}

        repairer.evaluator = pass_eval
        summary = await repairer.evaluate_and_promote_eligible()
        assert summary["considered"] == 1
        assert repairer._candidates[cid]["status"] == CandidateState.ACTIVE.value

    @pytest.mark.asyncio
    async def test_duplicate_rollout_counts_once(self, repairer):
        """Same rollout_id repeated → one evidence run, still below threshold."""
        repairer.process_failure("r1", "coder", "t", self.vi,
                                 task_id="t1", rollout_id="same-rollout")
        res = repairer.process_failure("r2", "coder", "t", self.vi,
                                       task_id="t1", rollout_id="same-rollout")
        assert res == "ignored: duplicate_run"
        cid = list(repairer._candidates.keys())[0]
        assert len(repairer._candidates[cid]["evidence_runs"]) == 1
        assert repairer._candidates[cid]["status"] == CandidateState.EVIDENCE_GATHERING.value

        async def pass_eval(c):
            return {"pass": True}

        repairer.evaluator = pass_eval
        summary = await repairer.evaluate_and_promote_eligible()
        assert summary["considered"] == 0
