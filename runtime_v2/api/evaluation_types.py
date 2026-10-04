"""Structured evaluation failure types for the learning bridge.

Phase 1: connects evaluator behavioral failures to PromptRepairer without
modifying PromptRepairer, Qdrant, or retrieval.

The gold patch is never stored in these types.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class EvaluationFailure:
    """Immutable structured record of an evaluation outcome.

    Created by the harness after an evaluation run. Contains everything
    the evaluator knows about what happened. Never modified after creation.
    The gold patch is never stored here.
    """

    # --- Identity ---
    task_id: str = ""                         # swe_pool.jsonl instance_id
    rollout_id: str = ""                      # unique per harness invocation (uuid4)
    run_ids: List[str] = field(default_factory=list)  # backend agent run IDs (may be >1 on retry)

    # --- Termination ---
    termination_reason: str = "unknown"       # "harness_timeout" | "agent_completed" | "max_turns" | "process_crash"
    timeout_seconds: int = 0                  # harness timeout (0 if not applicable)
    process_exit_code: int = -1               # OS exit code (-1 if unknown)

    # --- Trajectory ---
    step_count: int = 0                       # total tool-call steps across all attempts
    ordered_tool_actions: List[str] = field(default_factory=list)  # bare tool names: ["filesystem", "web_search", ...]
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    # --- Source-mutation measurement (tri-state; from the execution boundary) ---
    # ``mutation_attempted``: True = a source-mutating filesystem operation was
    # dispatched; False = tool calls happened but no source-mutating operation
    # and no indirect-mutation-capable tool; None = UNKNOWN (an indirect tool
    # such as sandbox_repl/mcp could have edited). ``mutation_succeeded``:
    # True = a dispatched source-mutating operation returned ok=True; False =
    # all such dispatches failed; None = UNKNOWN. Both are DISTINCT from
    # ``source_changed`` (an independent git-diff measurement) and from the
    # evaluator verdict.
    mutation_attempted: bool | None = None
    mutation_succeeded: bool | None = None

    # --- Evaluation ---
    baseline_f2p_passed: int = 0
    baseline_f2p_failed: int = 0
    post_f2p_passed: int = 0
    post_f2p_failed: int = 0
    source_changed: bool = False              # git diff shows modifications?

    # --- Authoritative evaluator verdict ---
    # ``evaluator_passed`` is the harness evaluator's task-outcome verdict
    # (``cli_baseline_swe._test_result``: all FAIL_TO_PASS pass after the run).
    # ``None`` means the verdict was not supplied, which is NOT the same as
    # False: an absent verdict cannot confirm a failure, so the learning bridge
    # must fail closed rather than assume one.
    #
    # ``evaluator_reason`` carries the verdict's own explanation (e.g.
    # "env_error", "regression: N new p2p failure(s)", "f2p: 0/1 passed"). A
    # non-capability reason means the run did not produce a usable measurement
    # and must not be treated as a behavioural learning signal.
    evaluator_passed: bool | None = None
    evaluator_reason: str = ""

    # --- Infrastructure (post-run) ---
    backend_reachable: bool = True            # /readyz after run completed
    model_endpoint_reachable: bool = True     # robs4b health after run completed

    # --- Metadata ---
    timestamp: str = ""                       # ISO 8601
    agent_model: str = ""                     # "robs4b"
    routing_mode: str = ""                    # "local_only"
