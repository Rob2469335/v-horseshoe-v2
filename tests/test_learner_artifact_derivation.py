"""Regression tests for the governance-v2 learner-artifact derivation.

Representation boundary: candidate.trigger stays the full diagnostic and
candidate.action stays the behavioral hypothesis; the learner-facing artifact
is derived deterministically (`condition: action` or `action`) and is the
exact string delivered at all three construction sites (evaluation temp
lesson, evaluation context, promotion). The 50-token gate and every other
governance invariant are unchanged.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from swarm_os.services.prompt_repairer import (
    _CONDITION_KEYWORDS,
    _derive_learner_artifact,
    CandidateState,
    PromptRepairer,
    EVALUATOR_ID,
    EVALUATOR_VERSION,
    GOVERNANCE_VERSION,
)
from swarm_os.services.lesson_manager import ActiveLesson, estimate_tokens
from swarm_os.services.lesson_synthesis import SynthesisAttestation

REPO = Path(__file__).resolve().parents[1]

# A worker-facing principle that is evidence-grounded, transferable and free of
# source-task identity. Used wherever a test needs a candidate that satisfies the
# lesson-synthesis prerequisite added to `promote()` on 2026-10-03. Tests that use
# it still assert exactly what they asserted before; they simply now also supply
# the validated synthesis the strengthened contract requires.
VALID_PRINCIPLE = (
    "Before modifying a dependency-facing call, verify the currently supported "
    "argument contract and update every caller to match the observed behaviour."
)


def valid_synthesis(
    text: str = VALID_PRINCIPLE,
    *,
    task_id: str = "task_twine",
    rollout_id: str = "roll_1",
) -> dict:
    """A minimal independently validated synthesis attestation.

    Carries structured task/rollout provenance so `attach_synthesis` can bind it
    to the candidate it belongs to.
    """
    return SynthesisAttestation(
        synthesis_version="ej-lesson-synthesis/2",
        principle_text=text,
        feature_codes=("edit_without_effect",),
        mechanism="test-seeded mechanism",
        evidence_ref=f"rollout:{rollout_id};task:{task_id}",
        validator_id="ej-independent-lesson-validator/1",
        validator_passed=True,
        task_id=task_id,
        rollout_id=rollout_id,
    ).to_dict()

# Real static/bridge trigger shapes (copied from the call sites).
TRIG_FORCED_SYNTH = "agent repeated an exploration cycle; forced to synthesize."
TRIG_CALL_LOOP = (
    'agent repeated the same tool decision >=3 times within the last 8 actions '
    '({"action": "filesystem", "operation": "read"}) and tripped the circuit breaker.'
)
TRIG_EXPLORATION = "fix-intent coder repeated an exploration cycle without editing."
TRIG_TURN_BUDGET = (
    "agent ran out of turns before completing the goal (likely a compound goal "
    "needing filesystem + web_search, or a slow LLM)."
)
TRIG_NO_EDIT = (
    "Evaluation task pypa__twine-1066: agent executed 4 steps (filesystem, "
    "filesystem, web_search, web_fetch) with 4/4 successful tool calls, no "
    "source modification before harness_timeout. Tests remained at 0/3 F2P."
)
TRIG_EDIT_FAILED = (
    "Evaluation task pypa__twine-1066: agent executed 6 steps (filesystem, "
    "patch) with 5/6 successful tool calls, attempted source modification before "
    "harness_timeout. Tests remained at 0/3 F2P."
)
TRIG_FILE_NOT_FOUND = "File not found: data/curriculum_fix/cand_011/module.py"

ACT_NO_EDIT = (
    "After researching the problem and gathering sufficient information, apply "
    "the fix with filesystem patch or filesystem write. Do not spend all turns "
    "on investigation without transitioning to code modification."
)


@pytest.fixture(autouse=True)
def _trusted_receipt_key(monkeypatch):
    monkeypatch.setenv("SWARM_RECEIPT_KEY", "derivation-test-receipt-key")


@pytest.fixture
def repairer(tmp_path, monkeypatch):
    from swarm_os.healing.diagnostician import Diagnostician

    monkeypatch.setattr("swarm_os.services.prompt_repairer._DATA_DIR", tmp_path)
    monkeypatch.setattr("swarm_os.services.prompt_repairer._CANDIDATES_FILE", tmp_path / "candidates.json")
    monkeypatch.setattr("swarm_os.services.prompt_repairer._SNAPSHOTS_FILE", tmp_path / "snapshots.json")
    monkeypatch.setattr("swarm_os.services.prompt_repairer._AUDIT_LOG_FILE", tmp_path / "audit.jsonl")
    lesson_manager = AsyncMock()
    lesson_manager.get_all = AsyncMock(return_value=[])
    lesson_manager.store = AsyncMock(return_value="lesson_123")
    lesson_manager.remove = AsyncMock(return_value=True)
    repairer = PromptRepairer.__new__(PromptRepairer)
    repairer.diagnostician = Diagnostician()
    repairer._candidates = {}
    repairer._snapshots = {}
    repairer.evaluator = None
    repairer.lesson_manager = lesson_manager
    return repairer


def _negate(rule: str) -> str:
    """The same rule with its leading directive inverted."""
    return re.sub(r"^Before\b", "Never", rule, count=1)


def _seed_oversized_attestation(text: str) -> SynthesisAttestation:
    """An attestation whose principle is over the per-rule token ceiling."""
    return SynthesisAttestation.from_dict(valid_synthesis(text))


def _seed_candidate(
    r: PromptRepairer, trigger: str, action: str, *, synthesis: bool = True
) -> str:
    """3 independent evidence runs across 2 tasks -> CANDIDATE state.

    ``synthesis=False`` seeds a candidate WITHOUT a validated synthesis, which is
    how the legacy ``trigger``/``action`` derivation path is exercised. Such a
    candidate cannot reach promotion (the synthesis prerequisite is fail-closed),
    so it is only used by tests of the fallback path itself.
    """
    r.process_failure("run1", "coder", trigger, action, task_id="task_twine", rollout_id="roll_1")
    r.process_failure("run2", "coder", trigger, action, task_id="task_twine", rollout_id="roll_2")
    r.process_failure("run3", "coder", trigger, action, task_id="task_click", rollout_id="roll_3")
    cands = [c for c in r._candidates.values() if c["trigger"] == trigger]
    assert len(cands) == 1
    # The lesson-synthesis prerequisite in `promote()` is fail-closed, so a
    # candidate that is meant to reach promotion must carry a validated
    # attestation. Attaching it here keeps every downstream test asserting the
    # behaviour it was written for (receipts, rollback, contradiction, token
    # gates) rather than the new prerequisite.
    if synthesis:
        r.attach_synthesis(
            cands[0]["id"],
            SynthesisAttestation.from_dict(
                valid_synthesis(task_id="task_twine", rollout_id="roll_1")
            ),
        )
    return cands[0]["id"]


# ---------------------------------------------------------------------------
# A. Keyword mapping against the seven real trigger shapes
# ---------------------------------------------------------------------------

class TestKeywordMapping:
    @pytest.mark.parametrize("trigger,label", [
        (TRIG_FORCED_SYNTH, "forced-synthesis"),
        (TRIG_CALL_LOOP, "call-loop"),
        (TRIG_EXPLORATION, "exploration-loop"),
        (TRIG_TURN_BUDGET, "turn-budget"),
        (TRIG_NO_EDIT, "no-edit"),
        (TRIG_EDIT_FAILED, "edit-failed"),
        (TRIG_FILE_NOT_FOUND, "file-not-found"),
    ])
    def test_real_trigger_shapes_map_to_labels(self, trigger, label):
        art = _derive_learner_artifact({"trigger": trigger, "action": "do the thing"})
        assert art == f"{label}: do the thing"

    def test_condition_vocabulary_order_is_pinned(self):
        """The vocabulary itself is governance semantics: exact order, exact pairs."""
        assert _CONDITION_KEYWORDS == (
            ("forced to synthesize", "forced-synthesis"),
            ("repeated the same tool decision", "call-loop"),
            ("without editing", "exploration-loop"),
            ("ran out of turns", "turn-budget"),
            ("no source modification", "no-edit"),
            ("attempted source modification", "edit-failed"),
            ("File not found", "file-not-found"),
        )

    def test_derivation_reads_only_trigger_and_action(self, repairer):
        """Metadata is never consulted: same trigger+action derive identically
        regardless of task/component/rollout identity."""
        base = {"trigger": TRIG_NO_EDIT, "action": ACT_NO_EDIT}
        noisy = {
            "trigger": TRIG_NO_EDIT,
            "action": ACT_NO_EDIT,
            "task_id": "something-else",
            "component": "debugger",
            "source": "somewhere",
            "rollout_id": "r-99",
            "governance_version": 999,
        }
        assert _derive_learner_artifact(base) == _derive_learner_artifact(noisy)


# ---------------------------------------------------------------------------
# B. Persisted production trigger derives no-edit
# ---------------------------------------------------------------------------

class TestPersistedProductionTrigger:
    def test_production_candidate_trigger_derives_no_edit(self):
        prod = REPO / "data" / "prompt_repairer_candidates.json"
        if not prod.exists():
            pytest.skip("production candidate store absent (data/ is gitignored)")
        data = json.loads(prod.read_text(encoding="utf-8"))
        cand = data.get("a8f69f262bae")
        if cand is None:
            pytest.skip("stale production candidate not present")
        art = _derive_learner_artifact(cand)
        assert art.startswith("no-edit: ")
        assert art.endswith(cand["action"])
        # The full diagnostic is preserved verbatim in candidate state.
        assert "no source modification" in cand["trigger"]
        assert len(cand["trigger"]) > 100


# ---------------------------------------------------------------------------
# C. Unknown/raw-error trigger -> action only
# ---------------------------------------------------------------------------

class TestUnknownTriggerFallback:
    def test_raw_error_trigger_derives_action_only(self):
        action = "Use the filesystem list operation before reading."
        art = _derive_learner_artifact({
            "trigger": "Search query is required",
            "action": action,
        })
        assert art == action
        assert ":" not in art or art == action  # no condition prefix invented

    def test_second_raw_error_shape(self):
        action = "Check the tool contract in _TOOL_DEFINITIONS before retrying."
        art = _derive_learner_artifact({
            "trigger": "tool decision failed after 3 retries (timeout=True)",
            "action": action,
        })
        assert art == action


# ---------------------------------------------------------------------------
# D. Three-site identity (evaluation temp lesson == eval context == promote)
# ---------------------------------------------------------------------------

class TestThreeSiteIdentity:
    @pytest.mark.asyncio
    async def test_all_three_sites_deliver_the_same_artifact(self, repairer, monkeypatch):
        r = repairer
        cid = _seed_candidate(r, TRIG_NO_EDIT, ACT_NO_EDIT)
        cand = r._candidates[cid]
        expected = _derive_learner_artifact(cand)
        # Since 2026-10-03 the delivered artifact is the independently validated
        # principle when one is attached, so it is the principle rather than a
        # "no-edit: " label. This test's subject is IDENTITY across the three
        # construction sites, which is what must hold for the evaluator and the
        # promoted lesson to be the same rule.
        assert expected == VALID_PRINCIPLE

        # --- Site 2: register_eval_context inside evaluate_candidate ---
        import swarm_os.services.prompt_repairer as pr_mod
        captured_ctx: list[str] = []
        real_register = pr_mod.register_eval_context

        def spy_register(evaluation_id, candidate_id, task_id, lesson):
            captured_ctx.append(lesson)
            return real_register(evaluation_id, candidate_id, task_id, lesson)

        monkeypatch.setattr(pr_mod, "register_eval_context", spy_register)

        async def mock_eval(c):
            return {"pass": True, "effectiveness": 0.9}

        r.evaluator = mock_eval
        res = await r.evaluate_candidate(cid)
        assert res == "evaluation_passed"
        assert len(captured_ctx) == 1
        artifact_ctx = captured_ctx[0]

        # --- Site 3: ActiveLesson.rule stored by promote ---
        res = await r.promote(cid)
        assert res == "promoted"
        stored_lesson = r.lesson_manager.store.call_args[0][0]
        assert isinstance(stored_lesson, ActiveLesson)
        artifact_promote = stored_lesson.rule

        # --- Site 1: BenchmarkEvaluator temporary lesson ---
        from swarm_os.services.prompt_repairer import BenchmarkEvaluator

        evaluator = BenchmarkEvaluator(task_id="task_twine", timeout=5)
        eval_lm = AsyncMock()
        eval_lm.store = AsyncMock(return_value="eval_lid")
        eval_lm.remove = AsyncMock(return_value=True)
        monkeypatch.setattr("qwen_train.run_curriculum.load_items",
                            lambda: [{"id": "task_twine"}])
        monkeypatch.setattr("qwen_train.run_curriculum._attempt_once",
                            lambda item, timeout, a, b: {"verified": True})
        monkeypatch.setattr(pr_mod, "get_lesson_manager", lambda: eval_lm)
        fresh = {
            "id": cand["id"],
            "trigger": cand["trigger"],
            "action": cand["action"],
            "task_id": "task_twine",
            # Mirror production: `evaluate_candidate` passes the FULL candidate
            # to the evaluator, so the synthesis attestation is present there.
            # Omitting it here would make the evaluator derive a different
            # artifact from the one promotion derives, which is precisely the
            # divergence this test exists to prevent.
            "synthesis": cand.get("synthesis"),
        }
        result = await evaluator(fresh)
        assert result["pass"] is True
        temp_lesson = eval_lm.store.call_args[0][0]
        assert isinstance(temp_lesson, ActiveLesson)
        artifact_eval = temp_lesson.rule

        # One canonical artifact at all three sites.
        assert artifact_eval == artifact_ctx == artifact_promote == expected

    @pytest.mark.asyncio
    async def test_derivation_is_deterministic_across_calls(self):
        cand = {"trigger": TRIG_TURN_BUDGET, "action": "interleave exploration and edits"}
        first = _derive_learner_artifact(cand)
        for _ in range(5):
            assert _derive_learner_artifact(cand) == first
        assert first == "turn-budget: interleave exploration and edits"


# ---------------------------------------------------------------------------
# E. Overflow fallback: label+action > 50 -> action, no truncation
# ---------------------------------------------------------------------------

class TestOverflowFallback:
    def test_labeled_overflow_falls_back_to_full_action(self):
        # Measured: action=50 tokens, "turn-budget: "+action=54 > MAX_RULE_TOKENS.
        action = "review the failure and change the approach " * 7
        assert estimate_tokens(action) == 50
        # Boundary space merges: 4 + 50 measures as 53, still over the 50 ceiling.
        assert estimate_tokens(f"turn-budget: {action}") == 53
        assert estimate_tokens(f"turn-budget: {action}") > 50
        art = _derive_learner_artifact({"trigger": TRIG_TURN_BUDGET, "action": action})
        assert art == action
        assert len(art) == len(action)  # no truncation anywhere
        assert estimate_tokens(art) <= 50


# ---------------------------------------------------------------------------
# F. Action overflow: action > 50 returned unchanged; gate rejects token_limit
# ---------------------------------------------------------------------------

class TestActionOverflowGate:
    @pytest.mark.asyncio
    async def test_oversized_action_returned_unchanged_and_gate_rejects(self, repairer):
        """The token budget is enforced at attach AND still guards promotion.

        Two properties, both preserved:

        1. The FALLBACK derivation path never truncates: an oversized ``action``
           comes back unchanged. Seeded with ``synthesis=False`` because an
           attached synthesis takes derivation precedence.
        2. An OVERSIZED validated principle is refused at attach time, and if one
           is forced onto a candidate by bypassing ``attach_synthesis`` (a
           hand-edited candidates.json), ``promote``'s token gate still rejects
           it. The budget protection is therefore defence in depth, not merely
           relocated.
        """
        action = "review the failure and change the approach " * 8
        assert estimate_tokens(action) == 57  # exceeds the 50-token ceiling
        art = _derive_learner_artifact({"trigger": TRIG_TURN_BUDGET, "action": action})
        assert art == action  # derivation never truncates, never rejects
        assert estimate_tokens(art) == 57

        r = repairer
        cid = _seed_candidate(r, TRIG_TURN_BUDGET, action, synthesis=False)

        # (a) attach refuses an over-budget principle
        oversized = _seed_oversized_attestation(action)
        assert r.attach_synthesis(cid, oversized) == "rejected: principle_exceeds_token_ceiling"

        # (b) bypassing attach, promote's own token gate must still fire
        r._candidates[cid]["synthesis"] = oversized.to_dict()
        cand = r._candidates[cid]
        cand["status"] = CandidateState.PROMOTABLE.value
        cand["evidence_tasks"] = ["task_twine", "task_click"]
        cand["eval_result"] = {
            "pass": True,
            "governance_version": GOVERNANCE_VERSION,
            "receipt": {"eval_id": "e", "candidate_id": cid, "hypothesis_id": cid,
                        "state_hash": r._hash_candidate(cand),
                        "governance_version": GOVERNANCE_VERSION,
                        "evaluator_id": EVALUATOR_ID,
                        "evaluator_version": EVALUATOR_VERSION, "pass": True},
        }
        cand["eval_result"]["receipt_sig"] = r._sign_receipt(cand["eval_result"]["receipt"])
        res = await r.promote(cid)
        assert res == "rejected: token_limit"
        assert r._candidates[cid]["status"] == CandidateState.REJECTED.value
        # Gate untouched: no lesson was stored.
        r.lesson_manager.store.assert_not_called()


# ---------------------------------------------------------------------------
# G. Contradiction preservation
# ---------------------------------------------------------------------------

class TestContradictionPreservation:
    def test_same_label_negation_triggers_v1(self):
        # Both derive under the no-edit label; "never" vs bare => negation asymmetry.
        a1 = _derive_learner_artifact({
            "trigger": TRIG_NO_EDIT,
            "action": "never apply with filesystem write after research.",
        })
        a2 = _derive_learner_artifact({
            "trigger": TRIG_NO_EDIT,
            "action": "apply with filesystem write after research.",
        })
        assert a1.startswith("no-edit: ") and a2.startswith("no-edit: ")
        assert PromptRepairer._is_contradiction_v1(None, a1, a2) is True

    def test_different_labels_do_not_trigger_scoped_v1(self):
        a1 = _derive_learner_artifact({
            "trigger": TRIG_NO_EDIT,
            "action": "never apply with filesystem write after research.",
        })
        a2 = _derive_learner_artifact({
            "trigger": TRIG_EDIT_FAILED,
            "action": "apply with filesystem write after research.",
        })
        assert a1.startswith("no-edit: ") and a2.startswith("edit-failed: ")
        assert PromptRepairer._is_contradiction_v1(None, a1, a2) is False

    @pytest.mark.asyncio
    async def test_cross_task_labeled_duplicate_detected_at_promote(self, repairer):
        r = repairer
        cid = _seed_candidate(r, TRIG_NO_EDIT, ACT_NO_EDIT)
        derived = _derive_learner_artifact(r._candidates[cid])
        # An identically derived rule is already active (cross-task provenance
        # is irrelevant to the duplicate check).
        r.lesson_manager.get_all = AsyncMock(return_value=[
            ActiveLesson(rule=derived, confidence=1.0, effectiveness=1.0),
        ])

        async def mock_eval(c):
            return {"pass": True, "effectiveness": 0.9}

        r.evaluator = mock_eval
        await r.evaluate_candidate(cid)
        res = await r.promote(cid)
        assert res == "rejected: duplicate_lesson"
        r.lesson_manager.store.assert_not_called()

    @pytest.mark.asyncio
    async def test_promote_same_label_negation_rejected(self, repairer):
        r = repairer
        trigger = TRIG_NO_EDIT
        cid = _seed_candidate(r, trigger, "never apply with filesystem write after research.")
        # The contradiction gate operates on the DELIVERED artifact. Since
        # 2026-10-03 that is the validated principle, not `action`, so the
        # counter-rule must negate the principle. Constructing it from `action`
        # would no longer be a contradiction of anything the worker receives.
        counter = _derive_learner_artifact(r._candidates[cid])
        counter = _negate(counter)
        r.lesson_manager.get_all = AsyncMock(return_value=[
            ActiveLesson(rule=counter, confidence=1.0, effectiveness=1.0),
        ])

        async def mock_eval(c):
            return {"pass": True, "effectiveness": 0.9}

        r.evaluator = mock_eval
        await r.evaluate_candidate(cid)
        res = await r.promote(cid)
        assert res == "rejected: contradiction"
        assert r._candidates[cid]["status"] == CandidateState.CONTRADICTED.value


# ---------------------------------------------------------------------------
# H. Stale governance-v1 candidate handling
# ---------------------------------------------------------------------------

class TestStaleCandidateHandling:
    def test_migrated_production_candidate_is_governance_v2(self):
        prod = REPO / "data" / "prompt_repairer_candidates.json"
        if not prod.exists():
            pytest.skip("production candidate store absent")
        data = json.loads(prod.read_text(encoding="utf-8"))
        cand = data.get("a8f69f262bae")
        if cand is None:
            pytest.skip("stale production candidate not present")
        assert cand["governance_version"] == 2
        # Migration preserved everything else.
        assert cand["status"] == "EVIDENCE_GATHERING"
        assert cand["task_id"] == "pypa__twine-1066"
        assert len(cand["evidence_runs"]) == 1
        assert "eval_result" not in cand
        assert "promotion_proof" not in cand

    @pytest.mark.asyncio
    async def test_gov1_candidate_cannot_promote_after_bump(self, repairer):
        """A stranded gov-1 record fails closed at promotion: evidence may
        still group onto it, but it can never activate under governance 2."""
        r = repairer
        cid = _seed_candidate(r, TRIG_NO_EDIT, ACT_NO_EDIT)
        r._candidates[cid]["governance_version"] = 1

        async def mock_eval(c):
            return {"pass": True, "effectiveness": 0.9}

        r.evaluator = mock_eval
        res = await r.evaluate_candidate(cid)
        assert res == "evaluation_passed"  # evaluation itself is version-agnostic
        res = await r.promote(cid)
        assert res == "rejected: governance_version"
        assert r._candidates[cid]["status"] == CandidateState.REJECTED.value
        r.lesson_manager.store.assert_not_called()


# ---------------------------------------------------------------------------
# I. Static trigger-shape source scan
# ---------------------------------------------------------------------------

class TestStaticTriggerShapeScan:
    """A future wording change at a call site must break this test, not the
    silent mapping of a keyword family."""

    _NEEDLES = [
        ("runtime_v2/api/agent_service_v2.py", "repeated an exploration cycle; forced to synthesize."),
        ("runtime_v2/api/agent_service_v2.py", "repeated the same tool decision"),
        ("runtime_v2/api/agent_service_v2.py", "repeated an exploration cycle without editing."),
        ("runtime_v2/api/agent_service_v2.py", "agent ran out of turns before completing the goal"),
        ("runtime_v2/api/agent_service_v2.py", '"File not found" in error'),
        ("runtime_v2/api/evaluation_bridge.py", '{True: "attempted", False: "no", None: "unknown"}[ef.mutation_attempted]'),
        ("runtime_v2/api/evaluation_bridge.py", "source modification before "),
    ]

    @pytest.mark.parametrize("relpath,needle", _NEEDLES,
                             ids=[f"{p}:{n[:32]}" for p, n in _NEEDLES])
    def test_static_trigger_family_still_present(self, relpath, needle):
        content = (REPO / relpath).read_text(encoding="utf-8")
        assert needle in content, (
            f"{relpath} no longer contains {needle!r} — the closed condition "
            f"vocabulary in prompt_repairer._CONDITION_KEYWORDS must be "
            f"reconciled with this wording change (governance bump expected)."
        )

    @pytest.mark.parametrize("trigger,label", [
        (TRIG_FORCED_SYNTH, "forced-synthesis"),
        (TRIG_CALL_LOOP, "call-loop"),
        (TRIG_EXPLORATION, "exploration-loop"),
        (TRIG_TURN_BUDGET, "turn-budget"),
        (TRIG_NO_EDIT, "no-edit"),
        (TRIG_EDIT_FAILED, "edit-failed"),
        (TRIG_FILE_NOT_FOUND, "file-not-found"),
    ])
    def test_family_shapes_still_derive_expected_labels(self, trigger, label):
        assert _derive_learner_artifact({"trigger": trigger, "action": "x"}) == f"{label}: x"


# ---------------------------------------------------------------------------
# Section 9: production-path chain (bridge -> candidate -> artifact ->
# evaluation context -> promotion) on a temporary isolated store.
# ---------------------------------------------------------------------------

class TestProductionPathChain:
    def _n1_failure(self, task_id: str, rollout_id: str):
        from runtime_v2.api.evaluation_types import EvaluationFailure

        return EvaluationFailure(
            task_id=task_id,
            rollout_id=rollout_id,
            run_ids=[],
            termination_reason="harness_timeout",
            timeout_seconds=1200,
            process_exit_code=-1,
            step_count=9,
            ordered_tool_actions=[
                "filesystem", "filesystem", "web_search", "web_fetch",
                "web_fetch", "filesystem", "filesystem", "web_search", "web_fetch",
            ],
            successful_tool_calls=8,
            failed_tool_calls=1,
            mutation_attempted=False,
            mutation_succeeded=False,
            baseline_f2p_passed=0,
            baseline_f2p_failed=3,
            post_f2p_passed=0,
            post_f2p_failed=3,
            source_changed=False,
            backend_reachable=True,
            model_endpoint_reachable=True,
            timestamp="2026-09-23T12:00:00",
            agent_model="robs4b",
            routing_mode="local_only",
        )

    @pytest.mark.asyncio
    async def test_bridge_to_promotion_single_canonical_artifact(self, repairer, monkeypatch):
        from runtime_v2.api.evaluation_bridge import submit_evaluation_failure
        import swarm_os.services.prompt_repairer as pr_mod

        monkeypatch.setattr(pr_mod, "get_prompt_repairer", lambda: repairer)

        # Three independent rollouts across two tasks -> CANDIDATE state.
        await submit_evaluation_failure(self._n1_failure("pypa__twine-1066", "roll-a"))
        await submit_evaluation_failure(self._n1_failure("pypa__twine-1066", "roll-b"))
        await submit_evaluation_failure(self._n1_failure("pallets__click-2380", "roll-c"))
        assert len(repairer._candidates) == 1
        cand = next(iter(repairer._candidates.values()))
        assert cand["status"] == CandidateState.CANDIDATE.value
        assert set(cand["evidence_tasks"]) == {"pypa__twine-1066", "pallets__click-2380"}
        # The bridge alone only produces the candidate + evidence. The validated
        # synthesis is attached by the learning pipeline (Stages A-D) before
        # evaluation; `promote` refuses any candidate without one.
        repairer.attach_synthesis(
            cand["id"],
            SynthesisAttestation.from_dict(
                valid_synthesis(task_id="pypa__twine-1066", rollout_id="roll-a")
            ),
        )

        # Diagnostic preserved full-fidelity in candidate state. Since 2026-10-03
        # the trigger is prefixed with the authoritative evaluator verdict so the
        # verdict survives `process_failure`'s `failure_reason[:100]` audit
        # truncation -- without it the store could not answer whether a solved
        # run had ever been admitted. The diagnostic itself is unchanged.
        assert "Evaluation task pypa__twine-1066" in cand["trigger"]
        assert "no source modification" in cand["trigger"]
        assert cand["trigger"].startswith("verdict=")
        assert "f2p=0/3" in cand["trigger"]

        expected = _derive_learner_artifact(cand)
        # With a validated synthesis attached the delivered artifact IS the
        # principle, so the labelled fallback form is not what a worker receives.
        assert expected == VALID_PRINCIPLE
        assert estimate_tokens(expected) <= 50

        # Evaluation context receives the derived artifact.
        captured_ctx: list[str] = []
        real_register = pr_mod.register_eval_context

        def spy_register(evaluation_id, candidate_id, task_id, lesson):
            captured_ctx.append(lesson)
            return real_register(evaluation_id, candidate_id, task_id, lesson)

        monkeypatch.setattr(pr_mod, "register_eval_context", spy_register)

        async def mock_eval(c):
            return {"pass": True, "effectiveness": 0.9}

        repairer.evaluator = mock_eval
        res = await repairer.evaluate_candidate(cand["id"])
        assert res == "evaluation_passed"
        assert captured_ctx[0] == expected

        # Promotion stores the same artifact; receipt binds the full state.
        res = await repairer.promote(cand["id"])
        assert res == "promoted"
        stored = repairer.lesson_manager.store.call_args[0][0]
        assert stored.rule == expected
        proof = cand["promotion_proof"]
        assert proof["governance_version"] == 2
        assert proof["token_count"] == estimate_tokens(expected)
        # Receipt/state binding still covers the full diagnostic + action.
        assert cand["eval_result"]["receipt"]["state_hash"] == repairer._hash_candidate(cand)
        # Canonical state contains trigger and action verbatim, no learner_rule.
        canon = json.loads(repairer._canonical_state(cand))
        assert canon["trigger"] == cand["trigger"]
        assert canon["action"] == cand["action"]
        assert "learner_rule" not in canon
        assert "rule_text" not in canon


# ---------------------------------------------------------------------------
# Section 10: receipt integrity — derivation adds no new bound field.
# ---------------------------------------------------------------------------

class TestReceiptIntegrity:
    @pytest.mark.asyncio
    async def test_state_hash_ignores_derived_artifact(self, repairer):
        """The artifact is a pure function of trigger+action; mutating only
        derivation inputs' metadata (unbound fields) must not change the hash,
        and mutating trigger/action must (existing contract)."""
        r = repairer
        cid = _seed_candidate(r, TRIG_NO_EDIT, ACT_NO_EDIT)
        cand = r._candidates[cid]
        h1 = r._hash_candidate(cand)
        # Deriving does not mutate the candidate.
        _derive_learner_artifact(cand)
        assert r._hash_candidate(cand) == h1
        # Canonical payload has exactly the governance fields, nothing new.
        canon = json.loads(r._canonical_state(cand))
        assert set(canon.keys()) == {
            "candidate_id", "hypothesis_id", "trigger", "action",
            "evidence_runs", "evidence_tasks", "activation_scope",
            "governance_version", "synthesis",
        }
        assert canon["governance_version"] == 2
        # The synthesis attestation is BOUND, not decorative: it determines the
        # delivered rule, so mutating it after evaluation must invalidate the
        # receipt exactly as mutating `trigger` does.
        assert canon["synthesis"] is not None

    @pytest.mark.asyncio
    async def test_post_eval_trigger_mutation_still_rejected(self, repairer):
        """Existing receipt contract: any post-evaluation mutation of bound
        state (incl. the diagnostic trigger) invalidates the receipt."""
        r = repairer
        cid = _seed_candidate(r, TRIG_NO_EDIT, ACT_NO_EDIT)

        async def mock_eval(c):
            return {"pass": True, "effectiveness": 0.9}

        r.evaluator = mock_eval
        await r.evaluate_candidate(cid)
        r._candidates[cid]["trigger"] = "tampered diagnostic"
        res = await r.promote(cid)
        assert res == "rejected: forged_or_mutated_evaluation"
        r.lesson_manager.store.assert_not_called()
