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
    ordered_tool_actions: List[str] = field(default_factory=list)  # ["filesystem:read", "web_search", ...]
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    source_modification_attempted: bool = False  # any filesystem.patch/write dispatched?
    source_modification_succeeded: bool = False  # any write/patch returned ok=True?

    # --- Evaluation ---
    baseline_f2p_passed: int = 0
    baseline_f2p_failed: int = 0
    post_f2p_passed: int = 0
    post_f2p_failed: int = 0
    source_changed: bool = False              # git diff shows modifications?

    # --- Infrastructure (post-run) ---
    backend_reachable: bool = True            # /readyz after run completed
    model_endpoint_reachable: bool = True     # robs4b health after run completed

    # --- Metadata ---
    timestamp: str = ""                       # ISO 8601
    agent_model: str = ""                     # "robs4b"
    routing_mode: str = ""                    # "local_only"
