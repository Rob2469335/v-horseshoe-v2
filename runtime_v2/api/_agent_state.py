"""Per-invocation agent state (`_CallState`).

Extracted from `agent_service_v2.py` (2026-09-10 refactor, step 2/2). Pure
dataclass — no dependency on `AgentServiceV2`. Re-exported from
`agent_service_v2` (`_CallState as _CallState`) so existing
`from runtime_v2.api.agent_service_v2 import _CallState` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _CallState:
    """Per-invocation mutable state. The AgentServiceV2 singleton is shared across
    concurrent /step/stream requests — keeping counters/status here prevents
    cross-run contamination (wrong premature-final aborts, mixed tool results)."""

    handler_status: str = ""
    premature_finals: int = 0
    reviewer_fails: int = 0
    tool_success: bool = False
    tool_action: str = ""
    tool_payload: dict = field(default_factory=dict)
    tool_result: Any = None
    tool_result_str: str = ""
    # Outcome-fitness: real-task-outcome signal fed to the evolutionary kernel.
    _start_time: float = 0.0
    _tool_attempts: int = 0
    _tool_successes: int = 0
    _filesystem_reads: int = 0  # Per-run cap for code_analyzer's read budget (2026-09-10 fix)
    _filesystem_read_capped: bool = False  # One-time stop-reading nudge already sent
    _forced_final: bool = False  # Tools restricted to final once the read budget is hit
    _turn: int = 0
    # Todo tracking (multi-step task state, like a human agent's checklist)
    todos: list = field(default_factory=list)
    todo_id: int = 0
    # Verify-after-change: set after a successful patch/write on a code file;
    # a final while pending_verify is rejected once so the agent tests first.
    pending_verify: bool = False
    _verify_final_rejected: bool = False
    # Internet-research tracking: set when the agent runs web_search successfully,
    # so internet-involving goals cannot short-circuit to final without it.
    did_web_search: bool = False
    # Set when the agent deep-reads at least one page via web_fetch — an
    # internet goal needs actual fetched content, not just search snippets.
    did_web_fetch: bool = False
    _web_final_rejected: bool = False
    # Top URLs from the last successful web_search, captured so the loop can
    # deterministically inject a web_fetch (the model — even cloud deepseek —
    # repeatedly re-selects web_search instead of web_fetch, loop-tripping the
    # circuit breaker before any page is ever read).
    last_search_urls: list = field(default_factory=list)
    _web_fetch_injected: bool = False
    # Executor compound-goal chaining phase flags: research was delegated (and
    # returned) so the implementation phase must now go to coder — deterministically,
    # because the executor LLM has been observed to re-loop instead of delegating.
    _executor_research_delegated: bool = False
    _executor_impl_delegated: bool = False
    # Set when the editing agent (`coder`) successfully writes or patches a
    # file this run. Used as a hard invariant: an editing agent that is handed a
    # fix-intent goal may NOT finalize without having actually modified code
    # (it otherwise just runs web_search and restates the goal — the repeated
    # /upgrade autonomous-loop failure where every attempt ended with
    # "No file changes detected").
    did_code_change: bool = False
    # L1 (2026 structural verifier): files actually READ this run (via filesystem
    # operation=read/read_all) vs merely LISTED/GLOBbed. An analysis agent may
    # not reference a file path in its final that it never actually read — only
    # seeing the name in a listing is not "read the content", so a vague final
    # that cites never-read paths fails closed (treated like a system failure).
    # L1 (2026 structural verifier): file paths the agent genuinely grounded on
    # this run. Populated from BOTH filesystem read/read_all AND real
    # semantic_search hits (whose returned chunks carry `File: <path>` lines) —
    # a semantic_search hit is real content grounding, not a placeholder dodge.
    # An analysis agent may not cite a .py path in its final unless it actually
    # saw that file's content this run (via read or a real search hit).
    read_paths: set = field(default_factory=set)
    # L1: number of times a final was rejected for a contract violation
    # (placeholder / unreferenced-read). Mirrors `premature_finals`: after 2
    # strikes the run aborts instead of looping forever under MAX_TURNS.
    _contract_finals: int = 0
    # L3 (2026 real-test signal): actual test outcome after a coder code change.
    # None = not yet run / no test suite; else 1.0 (exit 0) or 0.0 (failed).
    # Replaces the completion-proxy of outcome_fitness's test_pass when set.
    test_pass_result: float | None = None
    # Guard so the in-sandbox test run happens at most once per step_agent_stream
    # (it is expensive — a full DangerRoom copy + pytest).
    _tests_ran: bool = False
    # Genome identifier for this run — set from the evolutionary kernel's active
    # genome before the turn loop starts (see _step_agent_stream_inner). Listed in
    # _CHECKPOINT_STATE_FIELDS so it is preserved across resume checkpoints.
    genome_id: str = ""
    genome_weights: dict = field(default_factory=dict)
    # Per-run trace-tree linkage (2026-09, step-level fitness prerequisite):
    # run_id = one UUID minted per top-level step_agent_stream; parent_id = the
    # delegating agent's run_id, or "" at the top level. A delegated child keeps a
    # DIFFERENT run_id but points parent_id back at its delegator, so a whole
    # delegation subtree is reconstructable. Additive to outcome/event records.
    run_id: str = ""
    parent_id: str = ""
    # Which agent delegated to this run (empty at top level). Persisted alongside
    # run_id/parent_id on outcome records for the who-delegated axis.
    delegated_by: str = ""
    # Design A pre-action authorization: pending_ids whose approval was already
    # resolved THIS run (consumed via execute_approved, or denied via
    # deny_pending). The CLI feeds the approve/deny answer back as an
    # Observation that REMAINS in history, so without this set the deterministic
    # resolution block re-resolves the same pending on every turn — a consumed
    # pending re-denies ("expired or already used") on each loop until MAX_TURNS.
    # One-shot resolution is required. Listed in _CHECKPOINT_STATE_FIELDS so a
    # resume cannot re-resolve (the pending is consumed; re-resolving would
    # replay the DENY loop against the restored history).
    _resolved_approvals: set = field(default_factory=set)
