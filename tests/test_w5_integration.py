"""W5 end-to-end integration: evidence -> synthesis -> evaluation -> promotion.

Everything here uses tmp storage, a mocked lesson manager and a deterministic
FAKE distiller. No Qdrant, no receipt key, no ACTIVE production state.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarm_os.services.lesson_distiller import make_fake_distiller
from swarm_os.services.lesson_synthesis import (
    FailureEvidence,
    synthesize_candidate,
)
from swarm_os.services.prompt_repairer import CandidateState, PromptRepairer

GOOD_PRINCIPLE = (
    "Before modifying a dependency-facing call, verify the currently supported "
    "argument contract and update every caller to match the observed behaviour."
)
ACTION = "use filesystem patch and verify with sandbox_repl"


@pytest.fixture(autouse=True)
def _test_receipt_key(monkeypatch):
    """Test-only signing key so the promotion path is exercised. Never a real key."""
    monkeypatch.setenv("SWARM_RECEIPT_KEY", "w5-integration-test-key")


def _ev(task_id: str, rollout_id: str, **over) -> FailureEvidence:
    base = dict(
        task_id=task_id,
        rollout_id=rollout_id,
        evaluator_passed=False,
        evaluator_reason="f2p: 0/1 passed",
        classification="BEHAVIORAL",
        termination_reason="agent_completed",
        step_count=9,
        successful_tool_calls=7,
        failed_tool_calls=2,
        ordered_tool_actions=("filesystem:read", "filesystem:patch"),
        source_modification_attempted=True,
        source_modification_succeeded=True,
        source_changed=True,
        post_f2p_passed=0,
        post_f2p_failed=1,
    )
    base.update(over)
    return FailureEvidence(**base)


def _ev_dict(task_id: str, rollout_id: str, **over) -> dict:
    return _ev(task_id, rollout_id, **over).to_dict()


def _repairer(tmp_path, *, distiller=None) -> PromptRepairer:
    diag = MagicMock()
    diag._classify_fix.return_value = "prompt_sensitivity"
    lm = MagicMock()
    lm.get_all = AsyncMock(return_value=[])
    lm.store = AsyncMock(return_value="lesson_new")
    lm.remove = AsyncMock(return_value=True)
    with patch("swarm_os.services.prompt_repairer._DATA_DIR", tmp_path), patch(
        "swarm_os.services.prompt_repairer._CANDIDATES_FILE", tmp_path / "c.json"
    ), patch(
        "swarm_os.services.prompt_repairer._SNAPSHOTS_FILE", tmp_path / "s.json"
    ), patch(
        "swarm_os.services.prompt_repairer._AUDIT_LOG_FILE", tmp_path / "a.jsonl"
    ):
        return PromptRepairer(diagnostician=diag, lesson_manager=lm, distiller=distiller)


def _seed_run(r, task_id, rollout_id, evidence):
    return r.process_failure(
        run_id=rollout_id, component="coder", failure_reason="t",
        hypothesized_action=ACTION, task_id=task_id, rollout_id=rollout_id,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# synthesize_candidate: shared-mechanism contract
# ---------------------------------------------------------------------------


class TestCandidateSynthesis:
    def test_unanimous_mechanism_produces_attestation(self):
        evs = [_ev("tA", "rA"), _ev("tA", "rB"), _ev("tB", "rC")]
        att, why = synthesize_candidate(evs, make_fake_distiller(GOOD_PRINCIPLE))
        assert why == "ok" and att is not None
        assert att.principle_text == GOOD_PRINCIPLE
        assert att.task_id == "tA" and att.rollout_id == "rA"
        assert "shared_mechanism=" in att.rationale
        assert "distiller=fake:test-double" in att.rationale
        assert "runs=3" in att.rationale

    def test_no_shared_mechanism_fails_closed(self):
        edit = _ev("tA", "rA")  # edit_without_effect
        investigate = _ev(
            "tB", "rB", source_modification_attempted=False,
            source_changed=False, ordered_tool_actions=("filesystem:read", "filesystem:glob"),
            step_count=2, successful_tool_calls=2,
        )  # investigation_without_edit
        att, why = synthesize_candidate([edit, investigate], make_fake_distiller(GOOD_PRINCIPLE))
        assert att is None and why == "no_shared_mechanism_across_runs"

    def test_no_distiller_fails_closed(self):
        att, why = synthesize_candidate([_ev("tA", "rA")], None)
        assert att is None and why == "no_distiller_available"

    def test_undiagnosable_run_fails_closed(self):
        good = _ev("tA", "rA")
        solved = _ev("tB", "rB", evaluator_passed=True, evaluator_reason="passed")
        att, why = synthesize_candidate([good, solved], make_fake_distiller(GOOD_PRINCIPLE))
        assert att is None and why.startswith("run_undiagnosable")

    def test_distiller_failure_fails_closed(self):
        def boom(_prompt):
            raise RuntimeError("local model down")

        att, why = synthesize_candidate([_ev("tA", "rA"), _ev("tB", "rB")], boom)
        assert att is None and why.startswith("stage_b:distiller_error")


# ---------------------------------------------------------------------------
# PromptRepairer tick: real production integration
# ---------------------------------------------------------------------------


class TestTickIntegration:
    @pytest.mark.asyncio
    async def test_tick_synthesizes_and_promotes(self, tmp_path):
        r = _repairer(tmp_path, distiller=make_fake_distiller(GOOD_PRINCIPLE))
        _seed_run(r, "taskA", "rollA", _ev_dict("taskA", "rollA"))
        _seed_run(r, "taskA", "rollB", _ev_dict("taskA", "rollB"))
        _seed_run(r, "taskB", "rollC", _ev_dict("taskB", "rollC"))
        cid = list(r._candidates)[0]
        assert r._candidates[cid]["status"] == CandidateState.CANDIDATE.value

        async def pass_eval(_c):
            return {"pass": True}

        r.evaluator = pass_eval
        summary = await r.evaluate_and_promote_eligible()
        assert summary["promoted"] == 1
        cand = r._candidates[cid]
        assert cand["status"] == CandidateState.ACTIVE.value
        assert cand["synthesis"]["principle_text"] == GOOD_PRINCIPLE
        assert "distiller=fake:test-double" in cand["synthesis"]["rationale"]

    @pytest.mark.asyncio
    async def test_missing_structured_evidence_blocks_promotion(self, tmp_path):
        r = _repairer(tmp_path, distiller=make_fake_distiller(GOOD_PRINCIPLE))
        _seed_run(r, "taskA", "rollA", _ev_dict("taskA", "rollA"))
        _seed_run(r, "taskA", "rollB", None)  # no structured evidence
        _seed_run(r, "taskB", "rollC", _ev_dict("taskB", "rollC"))
        cid = list(r._candidates)[0]

        async def pass_eval(_c):
            return {"pass": True}

        r.evaluator = pass_eval
        summary = await r.evaluate_and_promote_eligible()
        assert summary["promoted"] == 0
        assert r._candidates[cid]["status"] == CandidateState.REJECTED.value
        assert "synthesis" not in r._candidates[cid]

    @pytest.mark.asyncio
    async def test_no_distiller_blocks_promotion(self, tmp_path):
        r = _repairer(tmp_path, distiller=None)
        _seed_run(r, "taskA", "rollA", _ev_dict("taskA", "rollA"))
        _seed_run(r, "taskA", "rollB", _ev_dict("taskA", "rollB"))
        _seed_run(r, "taskB", "rollC", _ev_dict("taskB", "rollC"))
        cid = list(r._candidates)[0]

        async def pass_eval(_c):
            return {"pass": True}

        r.evaluator = pass_eval
        summary = await r.evaluate_and_promote_eligible()
        assert summary["promoted"] == 0
        assert r._candidates[cid]["status"] == CandidateState.REJECTED.value


class TestProvenanceAndSnapshot:
    @pytest.mark.asyncio
    async def test_empty_rollout_id_still_binds_run_provenance(self, tmp_path):
        """A legacy run_id-only evidence run must still bind run provenance."""
        r = _repairer(tmp_path, distiller=make_fake_distiller(GOOD_PRINCIPLE))
        for i, task in enumerate(["taskA", "taskA", "taskB"]):
            r.process_failure(
                run_id=f"run{i}", component="coder", failure_reason="t",
                hypothesized_action=ACTION, task_id=task, rollout_id="",
                evidence=_ev_dict(task, ""),
            )
        cid = list(r._candidates)[0]
        assert r._candidates[cid]["status"] == CandidateState.CANDIDATE.value

        att, why = r._synthesize_eligible(r._candidates[cid])
        assert why == "ok" and att is not None
        assert att.rollout_id == "run0"  # bound to the run_id, not left empty
        assert att.task_id == "taskA"
        assert r.attach_synthesis(cid, att) == "synthesis_attached"

    @pytest.mark.asyncio
    async def test_evaluator_snapshot_is_isolated_from_live_candidate(self, tmp_path):
        """The evaluator receives an immutable snapshot; it cannot rewrite the
        live candidate or the promoted text."""
        r = _repairer(tmp_path, distiller=make_fake_distiller(GOOD_PRINCIPLE))
        _seed_run(r, "taskA", "rollA", _ev_dict("taskA", "rollA"))
        _seed_run(r, "taskA", "rollB", _ev_dict("taskA", "rollB"))
        _seed_run(r, "taskB", "rollC", _ev_dict("taskB", "rollC"))
        cid = list(r._candidates)[0]
        att, why = r._synthesize_eligible(r._candidates[cid])
        assert why == "ok" and att is not None
        assert r.attach_synthesis(cid, att) == "synthesis_attached"

        async def mutating_eval(snapshot):
            snapshot["synthesis"]["principle_text"] = "MUTATED BY EVALUATOR"
            snapshot["action"] = "MUTATED"
            return {"pass": True}

        r.evaluator = mutating_eval
        await r.evaluate_candidate(cid)
        cand = r._candidates[cid]
        assert cand["synthesis"]["principle_text"] == GOOD_PRINCIPLE
        assert cand["action"] == ACTION
        res = await r.promote(cid)
        assert res == "promoted"
        assert cand["status"] == CandidateState.ACTIVE.value


# ---------------------------------------------------------------------------
# Membrane against the ACTUAL W5 input representation (unioned provenance)
# ---------------------------------------------------------------------------


class TestMembraneOnW5Inputs:
    def _att(self, leaky_text):
        evs = [
            _ev("databricks__dbt-databricks-935", "rA",
                evaluator_reason="f2p: 0/1 passed in tests/unit/macros/test_table.py::test_macros_create"),
            _ev("databricks__dbt-databricks-935", "rB",
                evaluator_reason="f2p: 0/1 passed in tests/unit/macros/test_table.py::test_macros_create"),
        ]
        return synthesize_candidate(evs, make_fake_distiller(leaky_text))

    @pytest.mark.parametrize("leak", [
        "Before changing dbt-databricks-935, verify the argument contract.",
        "Before editing, fix the failing test_table.py case first.",
        "Apply the diff hunk @@ -1 +1 @@ to resolve the failure.",
        "Before changing the macro, change the function to accept the new argument.",
        "Edit macros/materializations/table.sql before re-running the checks.",
        "This is the gold patch for the observed failure.",
    ])
    def test_solution_leakage_rejected(self, leak):
        att, why = self._att(leak)
        assert att is None, leak
        assert why.startswith("stage_c") or why.startswith("stage_d"), why

    def test_generic_principle_allowed(self):
        att, why = self._att(GOOD_PRINCIPLE)
        assert why == "ok" and att is not None


class TestProvenanceCrossing:
    @pytest.mark.asyncio
    async def test_attestation_cannot_cross_candidates(self, tmp_path):
        """An attestation synthesized for candidate A cannot attach to B."""
        r = _repairer(tmp_path, distiller=make_fake_distiller(GOOD_PRINCIPLE))
        _seed_run(r, "taskA", "rollA", _ev_dict("taskA", "rollA"))
        _seed_run(r, "taskA", "rollB", _ev_dict("taskA", "rollB"))
        _seed_run(r, "taskB", "rollC", _ev_dict("taskB", "rollC"))
        cid = list(r._candidates)[0]
        att, why = r._synthesize_eligible(r._candidates[cid])
        assert why == "ok" and att is not None

        # A second, unrelated candidate.
        r._candidates["other"] = {
            "id": "other", "trigger": "t", "action": "different hypothesis text",
            "task_id": "taskZ",
            "evidence_runs": [{"run_id": "rz", "rollout_id": "rollZ", "hypothesis": "x"}],
            "evidence_tasks": ["taskZ"],
            "status": CandidateState.CANDIDATE.value,
            "governance_version": r._candidates[cid]["governance_version"],
        }
        out = r.attach_synthesis("other", att)
        assert out == "rejected: attestation_task_mismatch"
        assert "synthesis" not in r._candidates["other"]


# ---------------------------------------------------------------------------
# Bridge: structured evidence persisted, never reconstructed from prose
# ---------------------------------------------------------------------------


class TestBridgeEvidenceAdapter:
    @pytest.mark.asyncio
    async def test_submit_evaluation_failure_persists_structured_evidence(self, tmp_path):
        from runtime_v2.api.evaluation_bridge import submit_evaluation_failure
        from runtime_v2.api.evaluation_types import EvaluationFailure

        r = _repairer(tmp_path)
        ef = EvaluationFailure(
            task_id="pypa__twine-1066", rollout_id="roll-a",
            termination_reason="agent_completed", step_count=9,
            ordered_tool_actions=["filesystem:read", "filesystem:patch"],
            successful_tool_calls=7, failed_tool_calls=2,
            source_modification_attempted=True, source_modification_succeeded=True,
            source_changed=True, post_f2p_passed=0, post_f2p_failed=1,
            baseline_f2p_failed=1, evaluator_passed=False,
            evaluator_reason="f2p: 0/1 passed",
        )
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer", return_value=r):
            res = await submit_evaluation_failure(ef)
        assert res == "evidence_added"
        cand = next(iter(r._candidates.values()))
        run = cand["evidence_runs"][0]
        ev = run["evidence"]
        assert ev["task_id"] == "pypa__twine-1066"
        assert ev["step_count"] == 9
        assert ev["classification"] == "BEHAVIORAL"
        assert ev["ordered_tool_actions"] == ["filesystem:read", "filesystem:patch"]
        # Lossless round-trip.
        back = FailureEvidence.from_dict(ev)
        assert back is not None and back.step_count == 9
