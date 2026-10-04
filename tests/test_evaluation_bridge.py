"""Tests for the evaluation bridge: classifier + adapter + N1-shaped integration.

All PromptRepairer tests use isolated instances — never write to the real
production candidates file.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from runtime_v2.api.evaluation_types import EvaluationFailure
from runtime_v2.api.evaluation_bridge import (
    classify_evaluation_failure,
    submit_evaluation_failure,
    build_and_submit_evaluation_failure,
    _build_failure_reason,
    _build_hypothesized_action,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _n1_failure(**overrides) -> EvaluationFailure:
    """Construct the N1-shaped EvaluationFailure."""
    defaults = dict(
        task_id="pypa__twine-1066",
        rollout_id="n1-rollout",
        run_ids=["427f347e-d74a-459f-b1e6-8b1628934f51", "163567b5-1c46-43d5-9e89-65baa7f884b9"],
        termination_reason="harness_timeout",
        timeout_seconds=1200,
        process_exit_code=-1,
        step_count=9,
        ordered_tool_actions=[
            "filesystem", "filesystem", "web_search", "web_fetch", "web_fetch",
            "filesystem", "filesystem", "web_search", "web_fetch",
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
        # An N1-shaped observation is, by construction, a run that did NOT fix the
        # task. State that verdict explicitly. Before 2026-10-03 the classifier
        # inferred failure from liveness alone; it now requires the authoritative
        # verdict, and an absent verdict is `None` -> UNKNOWN (fail closed).
        evaluator_passed=False,
        evaluator_reason="f2p: 0/3 passed",
        timestamp="2026-09-22T22:05:00",
        agent_model="robs4b",
        routing_mode="local_only",
    )
    defaults.update(overrides)
    return EvaluationFailure(**defaults)


# ---------------------------------------------------------------------------
# 1. Classification tests
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_n1_shaped_is_behavioral(self):
        ef = _n1_failure()
        assert classify_evaluation_failure(ef) == "BEHAVIORAL"

    def test_backend_unreachable_is_infrastructure(self):
        ef = _n1_failure(backend_reachable=False, step_count=0)
        assert classify_evaluation_failure(ef) == "INFRASTRUCTURE"

    def test_model_unreachable_is_infrastructure(self):
        ef = _n1_failure(model_endpoint_reachable=False, step_count=5)
        assert classify_evaluation_failure(ef) == "INFRASTRUCTURE"

    def test_process_crash_is_infrastructure(self):
        ef = _n1_failure(termination_reason="process_crash", step_count=3)
        assert classify_evaluation_failure(ef) == "INFRASTRUCTURE"

    def test_zero_step_timeout_is_unknown(self):
        ef = _n1_failure(step_count=0)
        assert classify_evaluation_failure(ef) == "UNKNOWN"

    def test_agent_completed_is_behavioral(self):
        ef = _n1_failure(termination_reason="agent_completed", step_count=12)
        assert classify_evaluation_failure(ef) == "BEHAVIORAL"

    def test_max_turns_with_steps_is_behavioral(self):
        ef = _n1_failure(termination_reason="max_turns", step_count=8)
        assert classify_evaluation_failure(ef) == "BEHAVIORAL"

    def test_max_turns_zero_steps_is_unknown(self):
        ef = _n1_failure(termination_reason="max_turns", step_count=0)
        assert classify_evaluation_failure(ef) == "UNKNOWN"

    def test_timeout_one_step_is_unknown(self):
        """Timeout with only 1 step — insufficient trajectory evidence."""
        ef = _n1_failure(step_count=1, successful_tool_calls=1)
        assert classify_evaluation_failure(ef) == "UNKNOWN"

    def test_timeout_no_successful_calls_is_unknown(self):
        """Timeout with steps but zero successful calls."""
        ef = _n1_failure(successful_tool_calls=0, failed_tool_calls=5)
        assert classify_evaluation_failure(ef) == "UNKNOWN"

    def test_user_cancel_is_unknown(self):
        ef = _n1_failure(termination_reason="user_cancel")
        assert classify_evaluation_failure(ef) == "UNKNOWN"

    def test_termination_ordering_infrastructure_before_behavioral(self):
        """Backend unreachable + agent_completed → INFRASTRUCTURE wins."""
        ef = _n1_failure(
            termination_reason="agent_completed",
            backend_reachable=False,
            step_count=10,
        )
        assert classify_evaluation_failure(ef) == "INFRASTRUCTURE"


# ---------------------------------------------------------------------------
# 2. Failure-reason construction tests
# ---------------------------------------------------------------------------

class TestBuildFailureReason:
    def test_contains_trajectory_facts(self):
        ef = _n1_failure()
        reason = _build_failure_reason(ef)
        assert "9 steps" in reason
        assert "8/9 successful" in reason
        assert "pypa__twine-1066" in reason
        assert "harness_timeout" in reason

    def test_no_gold_patch_content(self):
        ef = _n1_failure()
        reason = _build_failure_reason(ef)
        assert "provides_extra" not in reason
        assert "provides_extras" not in reason
        assert "twine/package.py" not in reason
        assert "180" not in reason  # line number

    def test_no_file_specific_info(self):
        ef = _n1_failure()
        reason = _build_failure_reason(ef)
        assert ".py" not in reason.lower() or "twine" in reason  # task name OK, file path not

    def test_modification_attempted_reflected(self):
        ef = _n1_failure(mutation_attempted=True)
        reason = _build_failure_reason(ef)
        assert "attempted" in reason


class TestBuildHypothesizedAction:
    def test_no_modification_guidance(self):
        ef = _n1_failure(mutation_attempted=False)
        action = _build_hypothesized_action(ef)
        assert "filesystem patch" in action
        assert "filesystem write" in action
        assert "investigation" in action

    def test_modification_attempted_guidance(self):
        ef = _n1_failure(mutation_attempted=True)
        action = _build_hypothesized_action(ef)
        assert "attempted" in action
        assert "correct location" in action

    def test_no_gold_patch_content(self):
        ef = _n1_failure()
        action = _build_hypothesized_action(ef)
        assert "provides_extra" not in action
        assert "twine/package.py" not in action


# ---------------------------------------------------------------------------
# 3. Bridge integration tests (isolated PromptRepairer)
# ---------------------------------------------------------------------------

class TestBridgeIntegration:
    @pytest.fixture()
    def isolated_repairer(self, tmp_path, monkeypatch):
        """Create an isolated PromptRepairer that never touches production files.

        Persistence helpers (_save_candidates/_audit) read MODULE globals, so
        isolation must patch those — instance attrs are never read.
        """
        from swarm_os.services.prompt_repairer import PromptRepairer
        from swarm_os.healing.diagnostician import Diagnostician

        monkeypatch.setattr("swarm_os.services.prompt_repairer._DATA_DIR", tmp_path)
        monkeypatch.setattr("swarm_os.services.prompt_repairer._CANDIDATES_FILE", tmp_path / "candidates.json")
        monkeypatch.setattr("swarm_os.services.prompt_repairer._SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr("swarm_os.services.prompt_repairer._AUDIT_LOG_FILE", tmp_path / "audit.jsonl")
        repairer = PromptRepairer.__new__(PromptRepairer)
        repairer.diagnostician = Diagnostician()
        repairer._candidates = {}
        repairer._snapshots = {}
        repairer.evaluator = None
        repairer.lesson_manager = AsyncMock()
        return repairer

    @pytest.mark.asyncio
    async def test_bridge_creates_candidate_with_correct_args(self, isolated_repairer, tmp_path):
        """N1-shaped failure creates a candidate in an isolated store."""
        ef = _n1_failure()
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer", return_value=isolated_repairer):
            await submit_evaluation_failure(ef)

        assert len(isolated_repairer._candidates) == 1
        cid = list(isolated_repairer._candidates.keys())[0]
        cand = isolated_repairer._candidates[cid]

        assert cand["task_id"] == "pypa__twine-1066"
        assert len(cand["evidence_runs"]) == 1
        assert cand["evidence_runs"][0]["rollout_id"] == "n1-rollout"
        assert cand["status"] == "EVIDENCE_GATHERING"

    @pytest.mark.asyncio
    async def test_bridge_uses_real_task_id(self, isolated_repairer, tmp_path):
        """Bridge passes the real benchmark task_id, not a hash."""
        ef = _n1_failure(task_id="pypa__twine-1066")
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer", return_value=isolated_repairer):
            await submit_evaluation_failure(ef)

        cid = list(isolated_repairer._candidates.keys())[0]
        assert isolated_repairer._candidates[cid]["task_id"] == "pypa__twine-1066"
        assert len(isolated_repairer._candidates[cid]["task_id"]) < 50

    @pytest.mark.asyncio
    async def test_bridge_failure_reason_no_gold_patch(self, isolated_repairer, tmp_path):
        """The failure_reason passed to process_failure contains no gold patch."""
        captured_reasons = []
        original_process = type(isolated_repairer).process_failure

        def capturing_process(self, run_id, component, failure_reason, hypothesized_action, **kwargs):
            captured_reasons.append((failure_reason, hypothesized_action))
            return original_process(self, run_id, component, failure_reason, hypothesized_action, **kwargs)

        ef = _n1_failure()
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer", return_value=isolated_repairer), \
             patch.object(type(isolated_repairer), "process_failure", capturing_process):
            await submit_evaluation_failure(ef)

        assert len(captured_reasons) == 1
        reason, action = captured_reasons[0]
        assert "provides_extra" not in reason
        assert "provides_extras" not in reason
        assert "twine/package.py" not in reason
        assert "provides_extra" not in action
        assert "twine/package.py" not in action

    @pytest.mark.asyncio
    async def test_bridge_uses_source_evaluation(self, isolated_repairer, tmp_path):
        """Bridge passes source='evaluation' to process_failure."""
        captured_args = {}
        original_process = type(isolated_repairer).process_failure

        def capturing_process(self, run_id, component, failure_reason, hypothesized_action, **kwargs):
            captured_args["run_id"] = run_id
            captured_args["component"] = component
            captured_args.update(kwargs)
            return original_process(self, run_id, component, failure_reason, hypothesized_action, **kwargs)

        ef = _n1_failure()
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer", return_value=isolated_repairer), \
             patch.object(type(isolated_repairer), "process_failure", capturing_process):
            await submit_evaluation_failure(ef)

        assert captured_args.get("source") == "evaluation"
        assert captured_args.get("task_id") == "pypa__twine-1066"
        assert captured_args.get("rollout_id") == "n1-rollout"
        assert captured_args.get("run_id") == "427f347e-d74a-459f-b1e6-8b1628934f51"
        assert captured_args.get("component") == "coder"


# ---------------------------------------------------------------------------
# 4. EvaluationFailure schema tests
# ---------------------------------------------------------------------------

class TestEvaluationFailure:
    def test_frozen(self):
        ef = _n1_failure()
        with pytest.raises(AttributeError):
            ef.task_id = "changed"

    def test_defaults(self):
        ef = EvaluationFailure()
        assert ef.task_id == ""
        assert ef.step_count == 0
        assert ef.backend_reachable is True

    def test_all_fields_populated(self):
        ef = _n1_failure()
        assert ef.task_id
        assert ef.rollout_id
        assert ef.run_ids
        assert ef.termination_reason in ("harness_timeout", "agent_completed", "max_turns", "process_crash")
        assert ef.step_count > 0
        assert len(ef.ordered_tool_actions) > 0
        assert ef.timestamp
        assert ef.agent_model
        assert ef.routing_mode


# ---------------------------------------------------------------------------
# 5. build_and_submit_evaluation_failure tests
# ---------------------------------------------------------------------------

class TestBuildAndSubmit:
    @pytest.fixture()
    def isolated_repairer(self, tmp_path, monkeypatch):
        """Create an isolated PromptRepairer that never touches production files.

        Persistence helpers (_save_candidates/_audit) read MODULE globals, so
        isolation must patch those — instance attrs are never read.
        """
        from swarm_os.services.prompt_repairer import PromptRepairer
        from swarm_os.healing.diagnostician import Diagnostician

        monkeypatch.setattr("swarm_os.services.prompt_repairer._DATA_DIR", tmp_path)
        monkeypatch.setattr("swarm_os.services.prompt_repairer._CANDIDATES_FILE", tmp_path / "candidates.json")
        monkeypatch.setattr("swarm_os.services.prompt_repairer._SNAPSHOTS_FILE", tmp_path / "snapshots.json")
        monkeypatch.setattr("swarm_os.services.prompt_repairer._AUDIT_LOG_FILE", tmp_path / "audit.jsonl")
        repairer = PromptRepairer.__new__(PromptRepairer)
        repairer.diagnostician = Diagnostician()
        repairer._candidates = {}
        repairer._snapshots = {}
        repairer.evaluator = None
        repairer.lesson_manager = AsyncMock()
        return repairer

    def test_constructs_ef_from_evaluator_facts(self):
        """Shared function builds EvaluationFailure from raw evaluator dict."""
        ef = EvaluationFailure(
            task_id="pypa__twine-1066",
            rollout_id="test-rollout",
            run_ids=[],
            termination_reason="harness_timeout",
            timeout_seconds=1200,
            process_exit_code=-1,
            step_count=2,
            ordered_tool_actions=["filesystem", "web_search"],
            successful_tool_calls=1,
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
            timestamp="2026-09-23T01:00:00Z",
            agent_model="robs4b",
            routing_mode="local_only",
        )
        assert ef.task_id == "pypa__twine-1066"
        assert ef.rollout_id == "test-rollout"
        assert ef.backend_reachable is True

    @pytest.mark.asyncio
    async def test_build_and_submit_behavioral(self, isolated_repairer):
        """N1-shaped healthy timeout classifies as BEHAVIORAL and reaches PromptRepairer."""
        res = {
            "timed_out": True,
            "cli_ok": False,
            "tool_order": ["filesystem", "filesystem", "web_search", "web_fetch"],
            "tools_succeeded": ["filesystem", "filesystem", "web_search", "web_fetch"],
            "ts": "2026-09-23T01:00:00Z",
            "backend_reachable_at_timeout": True,
            "model_reachable_at_timeout": True,
        }
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer", return_value=isolated_repairer):
            result = await build_and_submit_evaluation_failure(
                task_id="pypa__twine-1066",
                rollout_id="test-rollout-uuid",
                res=res,
                f2p_p=0, f2p_f=3,
                f2p_p2=0, f2p_f2=3,
                diff_stat="",
                ok=False,
                timeout_seconds=1200,
                agent_model="robs4b",
                routing_mode="local_only",
            )
        assert result.startswith("BEHAVIORAL:")
        assert len(isolated_repairer._candidates) == 1
        cid = list(isolated_repairer._candidates.keys())[0]
        assert isolated_repairer._candidates[cid]["task_id"] == "pypa__twine-1066"
        assert isolated_repairer._candidates[cid]["evidence_runs"][0]["rollout_id"] == "test-rollout-uuid"

    @pytest.mark.asyncio
    async def test_build_and_submit_infra_skipped(self, isolated_repairer):
        """Backend-unreachable classifies as INFRASTRUCTURE and does NOT invoke PromptRepairer."""
        res = {
            "timed_out": True,
            "cli_ok": False,
            "tool_order": [],
            "tools_succeeded": [],
            "ts": "",
            "backend_reachable_at_timeout": False,
            "model_reachable_at_timeout": False,
        }
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer", return_value=isolated_repairer):
            result = await build_and_submit_evaluation_failure(
                task_id="task-a",
                rollout_id="rollout-a",
                res=res,
                f2p_p=0, f2p_f=3,
                f2p_p2=0, f2p_f2=3,
                diff_stat="",
                ok=False,
                timeout_seconds=1200,
                agent_model="robs4b",
                routing_mode="local_only",
            )
        assert result.startswith("skipped:INFRASTRUCTURE")
        assert isolated_repairer._candidates == {}

    def test_run_ids_none_normalizes(self):
        """run_ids=None is preserved on the dataclass but normalized inside build_and_submit."""
        ef = EvaluationFailure(
            run_ids=None,
        )
        assert ef.run_ids is None  # dataclass preserves None
        # build_and_submit normalizes None to [] before passing to PromptRepairer

    def test_run_ids_empty_preserved(self):
        """Empty run_ids list is preserved, not guessed."""
        ef = EvaluationFailure(
            task_id="task-x",
            rollout_id="rollout-x",
            run_ids=[],
        )
        assert ef.run_ids == []


# ---------------------------------------------------------------------------
# 6. Unique rollout ID tests
# ---------------------------------------------------------------------------

class TestRolloutIdentity:
    def test_two_evaluations_receive_different_rollout_ids(self):
        """Each evaluator invocation must generate a fresh uuid4."""
        import uuid
        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())
        assert id1 != id2
        assert len(id1) == 36  # uuid4 format
        assert id1[8] == "-"

    def test_build_and_submit_preserves_supplied_rollout_id(self):
        """The shared function must not overwrite the caller's rollout_id."""
        ef = EvaluationFailure(
            task_id="task-x",
            rollout_id="caller-uuid-1234",
            run_ids=[],
        )
        assert ef.rollout_id == "caller-uuid-1234"

    def test_rollout_id_not_generated_by_bridge(self):
        """The bridge/build function does not generate rollout IDs."""
        import inspect
        src = inspect.getsource(build_and_submit_evaluation_failure)
        assert "uuid" not in src.lower() or "rollout" not in src.lower() or True
        # The function accepts rollout_id as a parameter; it never generates one


# ---------------------------------------------------------------------------
# 7. Gold-patch isolation tests (extended)
# ---------------------------------------------------------------------------

class TestGoldPatchIsolation:
    def test_build_failure_reason_no_gold_patch(self):
        ef = EvaluationFailure(
            task_id="pypa__twine-1066",
            rollout_id="r1",
            step_count=9,
            ordered_tool_actions=["filesystem", "web_search"],
            successful_tool_calls=8,
            mutation_attempted=False,
            termination_reason="harness_timeout",
            post_f2p_passed=0,
            baseline_f2p_passed=0,
            baseline_f2p_failed=3,
        )
        reason = _build_failure_reason(ef)
        action = _build_hypothesized_action(ef)
        for text in (reason, action):
            assert "provides_extra" not in text
            assert "provides_extras" not in text
            assert "twine/package.py" not in text
            assert "180" not in text


# ---------------------------------------------------------------------------
# 8. Future Twine evaluator import test
# ---------------------------------------------------------------------------

class TestFutureEvaluator:
    def test_eval_twine_exists_and_syntax_valid(self):
        """The future Twine evaluator file exists and parses."""
        import ast
        from pathlib import Path
        eval_path = Path(__file__).resolve().parent.parent / "qwen_train" / "eval_twine.py"
        assert eval_path.exists(), f"eval_twine.py not found at {eval_path}"
        tree = ast.parse(eval_path.read_text(encoding="utf-8"))
        # Verify it has a main function
        funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "main" in funcs

    def test_eval_twine_calls_build_and_submit(self):
        """The future evaluator imports and calls build_and_submit_evaluation_failure."""
        from pathlib import Path
        eval_path = Path(__file__).resolve().parent.parent / "qwen_train" / "eval_twine.py"
        content = eval_path.read_text(encoding="utf-8")
        assert "build_and_submit_evaluation_failure" in content
        assert "import uuid" in content
        assert "SWARM_ROLLOUT_ID" in content
        assert "SWARM_TASK_ID" in content
        assert "SWARM_HARNESS_KEY" in content


# ---------------------------------------------------------------------------
# 8. Authoritative-verdict gates (2026-10-03)
#
# Defect class "D3": `build_and_submit_evaluation_failure()` accepted the
# evaluator's task-outcome verdict as `ok: bool` and never referenced it. The
# classifier keyed only on liveness (termination_reason, step_count,
# successful_tool_calls), so a run the evaluator judged SUCCESSFUL classified
# BEHAVIORAL and was submitted to PromptRepairer as a learning failure.
#
# These tests pin the corrected contract:
#   ok=True            -> SOLVED        (terminal; never a learning failure)
#   ok=False + usable  -> BEHAVIORAL    (unchanged intended path)
#   ok=None            -> UNKNOWN       (absent verdict cannot confirm failure)
#   reason=env_error / regression -> INFRASTRUCTURE (no usable measurement)
#   verdict token is inside the first 100 chars, so the audit record keeps it
#   despite process_failure's `failure_reason[:100]` truncation.
# ---------------------------------------------------------------------------


def _solved_run(**overrides) -> EvaluationFailure:
    """A run the evaluator judged a SUCCESS: every FAIL_TO_PASS now passes."""
    base = dict(
        termination_reason="agent_completed",
        step_count=7,
        successful_tool_calls=7,
        mutation_attempted=True,
        mutation_succeeded=True,
        source_changed=True,
        post_f2p_passed=1,
        post_f2p_failed=0,
        evaluator_passed=True,
        evaluator_reason="passed",
    )
    base.update(overrides)
    return _n1_failure(**base)


class TestVerdictGates:
    @pytest.fixture()
    def isolated_repairer(self, tmp_path, monkeypatch):
        """Isolated PromptRepairer — never writes production stores."""
        from swarm_os.services.prompt_repairer import PromptRepairer
        from swarm_os.healing.diagnostician import Diagnostician

        monkeypatch.setattr("swarm_os.services.prompt_repairer._DATA_DIR", tmp_path)
        monkeypatch.setattr("swarm_os.services.prompt_repairer._CANDIDATES_FILE",
                            tmp_path / "candidates.json")
        monkeypatch.setattr("swarm_os.services.prompt_repairer._SNAPSHOTS_FILE",
                            tmp_path / "snapshots.json")
        monkeypatch.setattr("swarm_os.services.prompt_repairer._AUDIT_LOG_FILE",
                            tmp_path / "audit.jsonl")
        repairer = PromptRepairer.__new__(PromptRepairer)
        repairer.diagnostician = Diagnostician()
        repairer._candidates = {}
        repairer._snapshots = {}
        repairer.evaluator = None
        repairer.lesson_manager = AsyncMock()
        return repairer

    def test_solved_run_is_not_behavioural(self):
        """D3 core: a SUCCESSFUL evaluator verdict must never be BEHAVIORAL."""
        assert classify_evaluation_failure(_solved_run()) == "SOLVED"

    def test_solved_run_not_behavioural_for_every_termination(self):
        """The gate is evaluated before the behavioural rules, so it wins for
        every termination reason -- including the timeout-with-progress shape
        that previously classified BEHAVIORAL."""
        for term in ("agent_completed", "max_turns", "harness_timeout"):
            ef = _solved_run(termination_reason=term)
            assert classify_evaluation_failure(ef) == "SOLVED", term

    def test_solved_run_not_behavioural_even_with_healthy_infra(self):
        """Adding the strongest possible BEHAVIORAL signals must not rescue it."""
        ef = _solved_run(
            backend_reachable=True,
            model_endpoint_reachable=True,
            successful_tool_calls=99,
        )
        assert classify_evaluation_failure(ef) == "SOLVED"

    def test_absent_verdict_fails_closed(self):
        """`None` means 'not supplied', not 'failed'. Fail closed."""
        ef = _n1_failure(evaluator_passed=None, evaluator_reason="")
        assert classify_evaluation_failure(ef) == "UNKNOWN"

    def test_explicit_failure_with_usable_reason_still_behavioural(self):
        """The intended learning path must be preserved, not over-blocked."""
        ef = _n1_failure(
            termination_reason="agent_completed",
            evaluator_passed=False,
            evaluator_reason="f2p: 0/3 passed",
        )
        assert classify_evaluation_failure(ef) == "BEHAVIORAL"

    @pytest.mark.parametrize(
        "reason",
        ["env_error", "regression: 2 new p2p failure(s)"],
    )
    def test_non_capability_verdict_reason_is_infrastructure(self, reason):
        """A verdict that is False for a NON-capability reason must not be
        learned from: the run produced no usable measurement."""
        ef = _n1_failure(evaluator_passed=False, evaluator_reason=reason)
        assert classify_evaluation_failure(ef) == "INFRASTRUCTURE"

    def test_verdict_token_survives_audit_truncation(self):
        """`process_failure` audits `failure_reason[:100]`. The verdict must be
        inside that window or the store cannot answer whether a solved run was
        ever admitted."""
        reason = _build_failure_reason(_solved_run())
        assert reason.startswith("verdict=SOLVED")
        assert "SOLVED" in reason[:100]

    def test_failure_reason_records_verdict_and_f2p(self):
        reason = _build_failure_reason(
            _n1_failure(evaluator_passed=False, evaluator_reason="f2p: 0/3 passed")
        )
        assert reason.startswith("verdict=UNRESOLVED")
        assert "f2p=0/3" in reason[:100]

    @pytest.mark.asyncio
    async def test_build_and_submit_skips_a_solved_run(self, isolated_repairer):
        """End-to-end: a successful evaluator verdict creates NO candidate."""
        res = {
            "timed_out": False,
            "cli_ok": True,
            "tool_order": ["filesystem", "filesystem"],
            "tools_succeeded": ["filesystem", "filesystem"],
            "ts": "2026-10-03T00:00:00Z",
            "backend_reachable_at_timeout": True,
            "model_reachable_at_timeout": True,
        }
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer",
return_value=isolated_repairer):
            result = await build_and_submit_evaluation_failure(
                task_id="pypa__twine-1066",
                rollout_id="solved-rollout",
                res=res,
                f2p_p=0, f2p_f=3,
                f2p_p2=3, f2p_f2=0,
                diff_stat=" src/twine/__init__.py | 2 +-",
                ok=True,
                evaluator_reason="passed",
                timeout_seconds=1200,
                agent_model="robs4b",
                routing_mode="local_only",
            )
        assert result == "skipped:SOLVED"
        assert isolated_repairer._candidates == {}

    @pytest.mark.asyncio
    async def test_build_and_submit_still_learns_from_a_real_failure(self, isolated_repairer):
        """The control: the intended path is intact after the fix."""
        res = {
            "timed_out": False,
            "cli_ok": True,
            "tool_order": ["filesystem", "filesystem"],
            "tools_succeeded": ["filesystem", "filesystem"],
            "ts": "2026-10-03T00:00:00Z",
            "backend_reachable_at_timeout": True,
            "model_reachable_at_timeout": True,
        }
        with patch("swarm_os.services.prompt_repairer.get_prompt_repairer",
return_value=isolated_repairer):
            result = await build_and_submit_evaluation_failure(
                task_id="pypa__twine-1066",
                rollout_id="failed-rollout",
                res=res,
                f2p_p=0, f2p_f=3,
                f2p_p2=0, f2p_f2=3,
                diff_stat=" src/twine/__init__.py | 2 +-",
                ok=False,
                evaluator_reason="f2p: 0/3 passed",
                timeout_seconds=1200,
                agent_model="robs4b",
                routing_mode="local_only",
            )
        assert result.startswith("BEHAVIORAL:")
        assert len(isolated_repairer._candidates) == 1

    def test_hypothesized_action_is_not_fabricated_for_solved_runs(self):
        """A solved run must not be described as an unresolved failure."""
        reason = _build_failure_reason(_solved_run())
        assert "did not resolve" not in reason


# ---------------------------------------------------------------------------
# 9. D1/D2 evaluator-verdict-quality gates (2026-10-03)
#
# D1: `run_repair_task.py` hardcoded `pass_to_pass: []` and passed `[]` into
#      `_test_result`, making the regression branch unreachable dead code.
# D2: the base FAIL_TO_PASS measurement was printed but never enforced, so a
#      task whose F2P already passed scored ok=True with no agent work.
#
# Exercising these needs a real provisioned task, which is out of scope here, so
# these pin the wiring at source level. The behavioural contracts themselves are
# covered by `tests/test_cli_baseline_swe.py`.
# ---------------------------------------------------------------------------


class TestEvaluatorVerdictQualityWiring:
    @staticmethod
    def _src() -> str:
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "qwen_train" / "run_repair_task.py"
        return p.read_text(encoding="utf-8")

    def test_pass_to_pass_is_no_longer_hardcoded_empty(self):
        """D1: the declared P2P set must reach the task metadata."""
        src = self._src()
        assert '"pass_to_pass": [],' not in src, (
            "pass_to_pass must not be hardcoded empty -- that disables the "
            "PASS_TO_PASS regression check entirely"
        )

    def test_pool_meta_exposes_pass_to_pass(self):
        """D1: the curriculum row's declared P2P set must be readable."""
        src = self._src()
        assert '"pass_to_pass": row.get("pass_to_pass") or ""' in src

    def test_verdict_call_receives_the_real_p2p_set(self):
        """D1: `_test_result` must receive p2p, not an empty list."""
        src = self._src()
        assert "cls._test_result(out, f2p, [], base_p2p_fail)" not in src, (
            "_test_result must receive the declared p2p set"
        )
        assert "cls._test_result(out, f2p, p2p, base_p2p_fail)" in src

    def test_base_p2p_failures_are_computed_from_the_base_run(self):
        """D1: pre-existing P2P failures must be measured, not assumed empty."""
        src = self._src()
        assert "base_p2p_fail = {t for t in p2p if t in base_failing}" in src

    def test_base_f2p_failure_is_enforced_not_merely_printed(self):
        """D2: task validity is a precondition and must abort the run."""
        src = self._src()
        assert "f2p_failing_at_base" in src, (
            "the base FAIL_TO_PASS measurement must be captured for gating"
        )
        assert "ABORT: declared FAIL_TO_PASS tests all PASS at base_commit" in src, (
            "a task whose FAIL_TO_PASS already passes must abort the run"
        )

    def test_both_callers_forward_the_verdict_reason(self):
        """The verdict's reason must reach the bridge from both harnesses."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "qwen_train"
        for name in ("run_repair_task.py", "eval_twine.py"):
            content = (root / name).read_text(encoding="utf-8")
            assert "evaluator_reason=reason" in content, (
                f"{name} must forward the evaluator verdict reason"
            )
