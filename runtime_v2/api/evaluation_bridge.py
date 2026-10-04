"""Evaluation bridge: connects evaluator behavioral failures to PromptRepairer.

Phase 1: deterministic classifier + adapter that translates EvaluationFailure
into the existing PromptRepairer.process_failure() contract.

No PromptRepairer changes. No Qdrant changes. No gold-patch access.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from runtime_v2.api.evaluation_types import EvaluationFailure
from swarm_os.services.lesson_synthesis import (
    FailureEvidence,
    is_valid_tool_action_name,
)

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

_TERMINAL = frozenset({"harness_timeout", "agent_completed", "max_turns", "process_crash"})

# Verdict reasons produced by `cli_baseline_swe._test_result` that mean the run
# did NOT yield a usable capability measurement. Substring-matched (lowercased)
# against the verdict reason so the check survives future reason wording.
#   "env_error"  -> collection crash / no summary line and no test ids
#   "regression" -> a PASS_TO_PASS test newly broke, i.e. the environment itself
#                   is now untrustworthy as a measurement surface
_NON_CAPABILITY_REASONS = ("env_error", "regression")


def classify_evaluation_failure(ef: EvaluationFailure) -> str:
    """Deterministic first-stage classifier: BEHAVIORAL, INFRASTRUCTURE, or UNKNOWN.

    Rules are ordered to prefer INFRASTRUCTURE/UNKNOWN when ambiguous.
    A deliberate harness timeout with a healthy backend and meaningful
    trajectory is BEHAVIORAL — not infrastructure.

    VERDICT GATES (rule 0, added 2026-10-03). These run BEFORE every behavioural
    rule and are the reason this function may no longer read a successful run as
    a learning failure:

    0a. ``evaluator_passed is True`` -> ``SOLVED``. The authoritative evaluator
        says every FAIL_TO_PASS test passed. There is no failure to learn from.
        Without this gate a fully successful run classified BEHAVIORAL, because
        the behavioural rules key only on liveness (termination_reason, step
        count, successful tool calls) and never on the task outcome.
    0b. ``evaluator_passed is None`` -> ``UNKNOWN``. An absent verdict cannot
        confirm a failure. Fail closed.
    0c. ``evaluator_reason`` names a non-capability outcome -> ``INFRASTRUCTURE``.
        A verdict of "env_error" or a new-regression reason means the run did not
        yield a usable capability measurement.

    A "SOLVED" verdict is a *terminal, expected* outcome, not an anomaly: it must
    be excluded from the learning-failure channel entirely so that solving a task
    can never accumulate promotion evidence.
    """
    # 0. AUTHORITATIVE EVALUATOR VERDICT (fail closed)
    if ef.evaluator_passed is True:
        return "SOLVED"
    if ef.evaluator_passed is None:
        return "UNKNOWN"
    if _NON_CAPABILITY_REASONS and any(
        marker in (ef.evaluator_reason or "").lower()
        for marker in _NON_CAPABILITY_REASONS
    ):
        return "INFRASTRUCTURE"

    # 1. Backend unreachable -> INFRASTRUCTURE
    if not ef.backend_reachable:
        return "INFRASTRUCTURE"

    # 2. Model unreachable -> INFRASTRUCTURE
    if not ef.model_endpoint_reachable:
        return "INFRASTRUCTURE"

    # 3. Process crashed -> INFRASTRUCTURE
    if ef.termination_reason == "process_crash":
        return "INFRASTRUCTURE"

    # 4. Zero steps -> UNKNOWN (ambiguous: infra stall or agent never started)
    if ef.step_count == 0:
        return "UNKNOWN"

    # 5. Agent completed -> BEHAVIORAL (it finished but didn't fix)
    if ef.termination_reason == "agent_completed":
        return "BEHAVIORAL"

    # 6. Max turns with steps -> BEHAVIORAL (explored but exhausted budget)
    if ef.termination_reason == "max_turns" and ef.step_count > 0:
        return "BEHAVIORAL"

    # 7. Harness timeout + healthy infra + useful trajectory -> BEHAVIORAL
    if (ef.termination_reason == "harness_timeout"
            and ef.backend_reachable
            and ef.model_endpoint_reachable
            and ef.step_count >= 2
            and ef.successful_tool_calls >= 1):
        return "BEHAVIORAL"

    # 8. Everything else -> UNKNOWN
    return "UNKNOWN"


def _build_failure_reason(ef: EvaluationFailure) -> str:
    """Construct failure_reason from trajectory facts. Never includes gold patch.

    The verdict token is placed FIRST so it survives `process_failure`'s audit
    truncation (`failure_reason[:100]`, prompt_repairer.py:1204). Without that,
    the only record of whether the run actually failed was truncated away, which
    is why the historical audit log could not answer whether a solved run had
    been admitted.
    """
    tools = ", ".join(ef.ordered_tool_actions[:20])
    mod = {True: "attempted", False: "no", None: "unknown"}[ef.mutation_attempted]
    verdict = {
        True: "SOLVED",
        False: "UNRESOLVED",
        None: "UNKNOWN",
    }[ef.evaluator_passed]
    return (
        f"verdict={verdict} f2p={ef.post_f2p_passed}/"
        f"{ef.post_f2p_passed + ef.post_f2p_failed} | "
        f"Evaluation task {ef.task_id}: agent executed {ef.step_count} steps "
        f"({tools}) with {ef.successful_tool_calls}/{ef.step_count} successful "
        f"tool calls, {mod} source modification before {ef.termination_reason}. "
        f"Tests remained at {ef.post_f2p_passed}/{ef.baseline_f2p_passed + ef.baseline_f2p_failed} F2P."
    )


def _build_hypothesized_action(ef: EvaluationFailure) -> str:
    """Construct generic behavioral guidance. Never includes file/line/patch."""
    if ef.mutation_attempted is True:
        return (
            "The agent attempted a source modification but it did not resolve the "
            "failing tests. Review the test failures and the agent's approach to "
            "determine whether the modification targeted the correct location."
        )
    return (
        "After researching the problem and gathering sufficient information, "
        "apply the fix with filesystem patch or filesystem write. Do not spend "
        "all turns on investigation without transitioning to code modification."
    )


async def submit_evaluation_failure(
    ef: EvaluationFailure,
) -> str:
    """Translate a structured evaluation failure into the PromptRepairer contract.

    This is the thin adapter between the evaluator and the existing learning
    infrastructure. It does NOT modify PromptRepairer itself.
    """
    from swarm_os.services.prompt_repairer import get_prompt_repairer

    failure_reason = _build_failure_reason(ef)
    hypothesized_action = _build_hypothesized_action(ef)

    # TYPED INGRESS: an ordered tool action must be a BARE tool name. A value
    # carrying a path/argument/symbol must never become evidence — it would
    # otherwise reach the Stage-B prompt through a feature detail. Fail closed:
    # skip, create no candidate, synthesize nothing.
    if not all(
        is_valid_tool_action_name(a) for a in (ef.ordered_tool_actions or ())
    ):
        _log.warning(
            "submit_evaluation_failure task=%s rejected: non-bare tool action",
            ef.task_id,
        )
        return "skipped:malformed_evidence"

    # W5: persist the STRUCTURED evidence Stage A needs, so synthesis never has
    # to reconstruct it from lossy prose. Only fields actually observed on the
    # EvaluationFailure are copied; nothing is invented. The gold patch and test
    # patch are never present in EvaluationFailure, so they cannot enter here.
    evidence = FailureEvidence(
        task_id=ef.task_id,
        rollout_id=ef.rollout_id,
        evaluator_passed=ef.evaluator_passed,
        evaluator_reason=str(ef.evaluator_reason or ""),
        classification=classify_evaluation_failure(ef),
        termination_reason=ef.termination_reason,
        step_count=ef.step_count,
        successful_tool_calls=ef.successful_tool_calls,
        failed_tool_calls=ef.failed_tool_calls,
        ordered_tool_actions=tuple(ef.ordered_tool_actions or ()),
        mutation_attempted=ef.mutation_attempted,
        mutation_succeeded=ef.mutation_succeeded,
        source_changed=ef.source_changed,
        baseline_f2p_failed=ef.baseline_f2p_failed,
        post_f2p_passed=ef.post_f2p_passed,
        post_f2p_failed=ef.post_f2p_failed,
        backend_reachable=ef.backend_reachable,
        model_endpoint_reachable=ef.model_endpoint_reachable,
    )

    _log.info(
        "submit_evaluation_failure task=%s rollout=%s classification=pending "
        "steps=%d tools=%d",
        ef.task_id, ef.rollout_id, ef.step_count, ef.successful_tool_calls,
    )

    # run_ids: the CLI-based evaluator path cannot reliably associate backend
    # run_ids (generated by agent_service_v2 as uuid4) with the evaluator
    # invocation. The NDJSON stream does not emit run_id. Use whatever the
    # evaluator provided — may be empty for the current CLI path.
    run_ids = ef.run_ids if ef.run_ids else []
    primary_run_id = run_ids[0] if run_ids else "unknown"

    result = get_prompt_repairer().process_failure(
        run_id=primary_run_id,
        component="coder",
        failure_reason=failure_reason[:2000],
        hypothesized_action=hypothesized_action[:2000],
        task_id=ef.task_id,
        rollout_id=ef.rollout_id,
        source="evaluation",
        evidence=evidence.to_dict(),
    )

    _log.info(
        "submit_evaluation_failure task=%s result=%s", ef.task_id, result
    )
    return result


async def build_and_submit_evaluation_failure(
    *,
    task_id: str,
    rollout_id: str,
    res: dict,
    f2p_p: int,
    f2p_f: int,
    f2p_p2: int,
    f2p_f2: int,
    diff_stat: str,
    ok: bool | None = None,
    evaluator_reason: str = "",
    timeout_seconds: int,
    agent_model: str,
    routing_mode: str,
    run_ids: list[str] | None = None,
) -> str:
    """Shared finalization function for evaluation-learning bridge.

    Called by both run_twine_eval.py (future) and run_repair_task.py after
    the evaluation completes. Constructs EvaluationFailure from evaluator
    facts, classifies it, and submits qualifying failures to PromptRepairer.

    This function does NOT:
    - generate identity (rollout_id comes from the caller)
    - run the evaluator
    - modify source
    - access the gold patch
    - rerun tests
    - invent run IDs
    """
    # Normalize run_ids: None -> empty list. Do NOT guess or fabricate.
    normalized_run_ids = run_ids if run_ids is not None else []

    # Independent post-run repository measurement — never derived from tool
    # names (a bare ``filesystem`` is not proof of an edit).
    source_changed = bool(diff_stat.strip())
    # Typed mutation measurement from the execution boundary (ATIF telemetry),
    # supplied by the harness in ``res``. Absent => UNKNOWN, never a guessed
    # False: a bare tool name cannot prove an edit did or did not happen.
    mutation_attempted = res.get("mutation_attempted")
    mutation_succeeded = res.get("mutation_succeeded")
    if mutation_attempted not in (True, False, None):
        mutation_attempted = None
    if mutation_succeeded not in (True, False, None):
        mutation_succeeded = None

    # Termination reason from evaluator result
    if res.get("timed_out"):
        termination = "harness_timeout"
    elif res.get("cli_ok"):
        termination = "agent_completed"
    else:
        termination = "process_crash"

    ef = EvaluationFailure(
        task_id=task_id,
        rollout_id=rollout_id,
        run_ids=normalized_run_ids,
        termination_reason=termination,
        timeout_seconds=timeout_seconds,
        process_exit_code=-1,
        step_count=len(res.get("tool_order") or []),
        ordered_tool_actions=res.get("tool_order", []),
        successful_tool_calls=len(res.get("tools_succeeded") or []),
        failed_tool_calls=max(
            0,
            len(res.get("tool_order") or [])
            - len(res.get("tools_succeeded") or []),
        ),
        mutation_attempted=mutation_attempted,
        mutation_succeeded=mutation_succeeded,
        baseline_f2p_passed=f2p_p,
        baseline_f2p_failed=f2p_f,
        post_f2p_passed=f2p_p2,
        post_f2p_failed=f2p_f2,
        source_changed=source_changed,
        backend_reachable=res.get("backend_reachable_at_timeout", True),
        model_endpoint_reachable=res.get("model_reachable_at_timeout", True),
        # The authoritative task-outcome verdict, threaded end-to-end. Before
        # this, `ok` was accepted and discarded, so a run the evaluator judged
        # SUCCESSFUL was indistinguishable from a genuine failure here.
        evaluator_passed=ok,
        evaluator_reason=str(evaluator_reason or ""),
        timestamp=res.get("ts", ""),
        agent_model=agent_model,
        routing_mode=routing_mode,
    )

    classification = classify_evaluation_failure(ef)

    if classification == "BEHAVIORAL":
        result = await submit_evaluation_failure(ef)
        return f"{classification}:{result}"

    return f"skipped:{classification}"
