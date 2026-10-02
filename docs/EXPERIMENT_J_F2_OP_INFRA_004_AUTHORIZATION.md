# F2-OP-INFRA-004 Authorization: F2 Engineering Execution Path Completion

## Purpose
Authorize completion and validation of the F2 ENGINEERING execution path so one
isolated, non-scientific engineering execution can traverse the real architecture
end to end:

  orchestrator → arm → adapter → fresh P2 → verified P1→P2 communication →
  rollout/trajectory/arm identity → task identity → isolated workspace →
  declared interpreter → existing production model-facing seam →
  delivery evidence → worker verification → receipt

This is an ENGINEERING authorization only. It does not authorize any F2
scientific execution or observation.

## Root Cause
The F2 worker-execution seam is implemented to its midpoint and was never
exercised in production. Four engineering defects were independently reproduced
against HEAD a5844460 on 2026-10-02 (D1/D4 already repaired; D2/D3/W2/W4
outstanding at the time of this authorization):

- D2 — P2 evidence writer and worker reader resolve different directories.
  `runtime_v2/api/agent_service_v2.py:295` `_TRAJ_DIR = _Path("data/trajectories")`
  is relative to the serving process cwd, while `qwen_train/f2_arm_worker.py`
  reads from `SWARM_WORKSPACE_ROOT`. With workspace != repo root the paths never meet.
- D3 — `runtime_v2/api/agent_service_v2.py:2678-2679` `except Exception: pass`
  silently discards evidence-write failure, making a failed write
  indistinguishable from absent evidence.
- W2 — `qwen_train/f2_execution_adapter.py` states "NO real model/provider is ever
  contacted"; `_fake_complete` monkeypatches `complete_for_tool_decision`, so only
  `start_real_p2_with_fake_model` exists. The production seam already exists
  (`stream_runner._call_llm` → `get_litellm_model`, driven over
  `POST /agents/{id}/step/stream` by `post_task_stream`).
- W4 — `run_f2_arm` accepts only `task_id: str`; no repository, base commit, test
  command, fail-to-pass set, workspace, or task interpreter enters F2.
- Wiring — `F2_ARM_EXEC_CMD` is set by no tracked code, so the production worker
  always records execution as "delegated"; the adapter is imported only by tests.

## Authorized Corrections

### 1. Explicit F2 evidence transport path (D2)
- `runtime_v2/api/agent_service_v2.py:295` — allow the F2 evidence directory to be
  supplied as an explicit absolute path via `SWARM_F2_TRAJ_DIR`. The NON-F2
  default MUST remain byte-identical to today's relative `data/trajectories`.
- `qwen_train/f2_arm_worker.py` — read that same single path. It MUST NOT search
  multiple candidate directories.
- `qwen_train/f2_execution_adapter.py` — add `SWARM_F2_TRAJ_DIR` to the existing
  `F2_ENV_VARS` so it propagates into fresh P2. Path MUST be absolute and
  deterministic per execution.

### 2. Explicit evidence-write outcome (D3)
- `runtime_v2/api/agent_service_v2.py` (~2665-2679) — replace the bare
  `except Exception: pass` with explicit success/failure outcome carrying the
  actual exception type and message. The stream MUST still not be killed by an
  evidence-write failure.
- `qwen_train/f2_arm_worker.py` — distinguish `EVIDENCE_NOT_FOUND` from
  `EVIDENCE_WRITE_FAILED` in the fail-closed diagnostic.

### 3. Production P2 model path (W2)
- `qwen_train/f2_execution_adapter.py` — add a real-model entry point alongside
  `start_real_p2_with_fake_model` that does NOT monkeypatch
  `complete_for_tool_decision`, so P2 resolves its model through the existing
  production path (`get_litellm_model`) and is driven over the existing
  loopback HTTP/SSE task surface.
- The adapter MUST retain P2 readiness gating and the fail-closed
  serving-process-identity handling.
- The fake-model entry point MUST be retained for existing tests.

### 4. Task and workspace binding (W4)
- Bind F2 execution to the EXISTING task-environment machinery. Reuse, do not
  reinvent: `_pool_env_meta`, `resolve_task_python`, `task_test_argv`,
  `_task_python_path`, `ensure_task_pytest`, `task_exec_plan`,
  `_preflight_evaluator_separation`, `swe_rebench_probe._test_cmd`, the curriculum
  pool, and `F1_AUTHORIZED_BASE_COMMIT`.
- Establish for each execution: task_id, instance_id, base_commit, isolated
  workspace, declared interpreter, install requirements, test command, and
  evaluator separation.
- If existing machinery genuinely cannot satisfy the contract, that must be
  reported as a blocker — not solved by inventing a second task-environment
  system.
- The main repository MUST NOT become the task workspace. The evaluator MUST
  remain outside the mutable task workspace where the existing contract requires it.

### 5. Production wiring
- `qwen_train/f2_arm_worker.py` — invoke the adapter through a structured seam in
  place of the unset `F2_ARM_EXEC_CMD` string seam, and incorporate its execution
  evidence. Existing fail-closed behavior, manifest verification, and arm
  derivation MUST be preserved.

### 6. In-scope integration repair (anti-fragmentation)
Once a component above is authorized, the implementing agent is authorized to
diagnose and repair ordinary in-scope integration failures rather than stopping
at the first failing test. A newly discovered defect on the authorized execution
path MAY be repaired without a further authorization request, provided it is
within the files and behaviors listed here and does not cross an exclusion below.
Anything outside this scope MUST be reported as a blocker.

## Authorized Tests
Unit, focused F2, adapter, worker, P2 integration, task/workspace integration,
process-identity, evidence-transport, and negative/fail-closed tests, plus the
smallest end-to-end ENGINEERING REHEARSAL. A broad repository test campaign is
NOT authorized except where existing repository policy already requires it
(`AGENTS.md` §3.6 currently withholds broad pytest; that policy is unchanged).

Starting the repository's already-authorized runtime services (backend, local
model ports) via existing startup mechanisms is permitted for engineering
validation only. No new service, model download, or model training.

## Scope Limitations
This authorization ONLY covers the six areas above and their directly relevant
tests.

This authorization DOES NOT authorize:
- F2 T/X/C0 scientific execution; any official F2 observation; population or
  evaluation; N=2; scientific-state conclusions.
- Candidate creation; lesson creation, activation, or promotion; ACTIVE lesson
  mutation; N1 reuse.
- Any change to F0 (`docs/EXPERIMENT_J.md`), F1 authority, or F1 historical
  evidence (`docs/F1_FINAL_RECONCILIATION.md` and `qwen_train/results/f1_obs*`).
- Any change to `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md`, or to
  the worker-execution authorization document (including its stale §18 status
  line, which is reported separately).
- W5 — external-parameter verification beyond the current F2 provenance contract.
- W6 — general evidence-transport hardening: per-rollout files, file locking,
  torn-tail recovery, `st_mtime` ordering, transport redesign.
- Any new model subsystem, model architecture change, model weight change,
  QLoRA training, or change to model configuration or resolution.
- Any change to the F0/F1 scientific definition of the task population or F0/F1
  task data.
- Any change to `runtime_v2/services/f2_replay.py`, `swarm_os/services/*`, or
  `qwen_train/f1_infra.py`.
- Broad refactoring of `runtime_v2/api/agent_service_v2.py`,
  `runtime_v2/services/stream_runner.py`, or unrelated logging cleanup.
- Committing or pushing without explicit human approval.

## Testing Requirements
1. New regression tests for D2, D3, W2, W4 and wiring must pass.
2. Existing focused F2 adapter/worker/delivery-seam tests must pass.
3. The engineering rehearsal must produce zero candidate, lesson, promotion, or
   ACTIVE-lesson mutation, and zero F2 observation.
4. `docs/EXPERIMENT_J.md` SHA-256 must remain
   `4EAFD2FAF2BA79078F8C1CAD7415DC1CEB2D24E753122A06FC2FA6AF76908337`.
5. `ruff check . --select E9,F` must pass on changed files.
6. The non-F2 default trajectory path must be proven byte-identical to today's.
7. No test may write production learning state.

## Authorization
**AUTHORIZED** for engineering implementation, validation, in-scope integration
repair, and the engineering rehearsal described above.

**NOT AUTHORIZED** for any excluded item, for F2 scientific execution, evaluation,
certification, promotion, or deployment.

---

*Authorized: 2026-10-02*
*Operator: Rob (human operator) — explicit authorization given in session*
*Scope: F2-OP-INFRA-004 — F2 engineering execution path completion only*
*Related: `docs/EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md` §13, §15, §16;
`docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md` §14.7*
