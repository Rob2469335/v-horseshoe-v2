# Experiment J F2 — Worker-Execution Implementation Authorization

**Status:** IMPLEMENTATION AUTHORIZATION — authorizes implementation of the real
F2 execution seam (Architecture D) within the exact scope below. This does NOT
mean implementation has occurred. This does NOT authorize F2 scientific
execution, N=2, or Experiment J.
**Date:** 2026-09-29

## 1. Authority hierarchy

```
F0 (frozen, docs/EXPERIMENT_J.md)
→ F1 authorization (docs/EXPERIMENT_J_F1_AUTHORIZATION.md)  [F1-only]
→ F2 execution-contract authorization (bb38ed85; fresh-process import/load/verify)
→ F2 orchestrator design (docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md §13-§14; DESIGN COMPLETE,
     Architecture D selected §14.1)
→ F2 orchestrator implementation authorization (docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md;
     infrastructure only; commit cdb8d0f6)
→ THIS worker-execution implementation authorization
→ AGENTS.md (operating rules; UNTRACKED IMPLEMENTATION / PROVENANCE RULE)
```

This document does NOT modify or supersede F0, F1 authorization, the F2
execution-contract authorization, or the F2 orchestrator design. It implements
Architecture D as specified in design §14.

## 2. Purpose

Authorize implementation of the real F2 execution seam: the thin
F2-specific execution adapter owned by the F2 worker/controller, which
establishes a fresh execution backend (P2) with explicit F2 replay environment,
drives the governed task through existing F1-derived mechanics, enforces the
frozen-delivery invariant, prevents LIVE fallback, and returns the actual
execution evidence that joins into the F2 receipt.

## 3. Prior proven state

- F0 is the frozen scientific design; F2 engineering is post-F0.
- F1 authorization is F1-only (no F2 grant).
- F2 fresh-process execution/import contract is authorized and implemented
  (commit `bb38ed85`).
- F2 orchestrator infrastructure is implemented, committed, and pushed at
  `cdb8d0f6`: `qwen_train/f2_arm_primitives.py`, `f2_arm_worker.py`,
  `f2_arm_orchestrator.py`, plus two test files.
- The F2 orchestrator design (§13) established: P2 is the authoritative replay
  owner; the frozen manifest must reach P2 via environment; `is_replay_active()`
  must be true in P2; LIVE render is never an acceptable fallback.
- The design (§14) selected Architecture D (thin execution adapter).
- Real F2 task execution has NOT occurred and is NOT authorized other than by
  this artifact's implementation scope (implementation ≠ execution).

## 4. Selected architecture D

The concrete architecture for the future F2 worker execution is **D — the
F2-specific thin execution adapter**, per design §14.1. It is a design decision,
not a ranking. The adapter is the single explicit F2-to-runtime boundary.

## 5. Exact process graph

```
P0 — F2 orchestrator
↓
P1 — F2 arm worker / controller
↓
F2 execution adapter
↓
fresh backend P2
   env: SWARM_F2_REPLAY=1
        SWARM_F2_MANIFEST_PATH=<verified manifest>
        F2 arm / rollout identity
   main.py install_verified_replay_from_env() → _f2_state in P2
↓
P1 → P2  (direct loopback HTTP/SSE, 127.0.0.1, task surface)
↓
stream_runner / agent_service_v2
↓
is_replay_active() == TRUE
↓
get_delivery_artifact()
↓
system prompt / model request
↓
task trajectory / endpoint evidence
↓
adapter collects execution evidence
↓
F2 receipt
```

> HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY AMENDMENT:
> the prior graph node "CLI / task process P3" is superseded. The selected F2
> HTTP execution graph is exactly `P0 → P1 → P2`; P3 is NOT required.

## 6. Exact replay propagation contract

1. P1 verifies the manifest and validates arm identity (existing worker behavior,
   unchanged).
2. The F2 execution adapter receives a verified arm/manifest reference.
3. The adapter starts a fresh backend (P2) whose environment explicitly contains
   `SWARM_F2_REPLAY=1`, `SWARM_F2_MANIFEST_PATH=<content-addressed path>`, and
   F2 arm / rollout identity.
4. P2's `main.py` lifespan calls `install_verified_replay_from_env()` →
   `_f2_state = FROZEN_REPLAY` in P2 (swarm_os/app/main.py:522-541).
5. P1 (in the adapter) drives P2 directly over loopback HTTP/SSE (`127.0.0.1`).
   P3 is not required and carries no replay/delivery semantics; delivery occurs
   in P2. [HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY
   AMENDMENT: the prior wording "The adapter (or P1) drives a CLI/task process
   (P3) that talks to P2 over HTTP/SSE" is superseded.]

## 7. Exact frozen-delivery invariant

**The model-facing lesson/prompt content for an F2 arm MUST originate from the
verified frozen T/X/C0 artifact via `get_delivery_artifact()` in P2.**

### 7.1 Current source state vs required enforcement

The independent source audit (2026-09-29) confirmed the current delivery code
has LIVE fallbacks when replay is inactive:

- `runtime_v2/services/stream_runner.py:658-669` —
  `if is_replay_active(): ... else: render_active_lessons(...)`
- `runtime_v2/services/stream_runner.py:770-780` —
  `if is_replay_active(): ... else: render_active_lessons(...)`
- `runtime_v2/api/agent_service_v2.py:2667-2675` —
  `if is_replay_active(): ... else: render_active_lessons(...)`

This behavior is **incompatible with F2 replay-required execution**. The existing
delivery branches silently serve LIVE lessons whenever replay is inactive in the
delivery process. The existing code therefore **cannot be treated as satisfying
the F2 frozen-delivery invariant**. This is an IMPLEMENTATION PREREQUISITE — it
is not something the adapter may silently assume is solved.

### 7.2 The F2 invariant (conditional, mandatory)

WHEN F2 replay is REQUIRED:

- `is_replay_active()` MUST be TRUE in the P2 process before delivery.

IF F2 replay is REQUIRED and replay is inactive:

- **ABORT** before model execution.
- NO LIVE lesson rendering.
- NO model request.
- NO trajectory / model-delivery evidence may be accepted as successful
  execution.

Do NOT weaken this to "the adapter should normally ensure replay is active." The
abort is a hard requirement.

### 7.3 Non-F2 behavior is preserved

This authorization does NOT globally remove LIVE behavior. The requirement is
conditional:

- F2 replay required → inactive replay = ABORT.
- non-F2 execution → existing behavior may remain unchanged.

The amendment must not accidentally become a broad runtime redesign.

### 7.4 C0 / LIVE / replay-state distinction

Explicit resolution:

- **C0 + active verified replay + intentionally empty delivery artifact** is
  valid C0 behavior.
- It MUST NOT be interpreted as "empty artifact → replay inactive → LIVE
  fallback".

The implementation and tests MUST distinguish four states explicitly:

1. replay inactive (LIVE mode);
2. replay active with C0 empty artifact;
3. replay active with non-empty replay artifact;
4. legitimate non-F2 LIVE mode.

**Artifact truthiness must NOT be used as the sole replay-state discriminator.**
Replay state is established by `is_replay_active()` / `_f2_state`, never by
whether the delivered string is empty.

Prohibited for an F2 arm (unchanged):
- LIVE `render_active_lessons()`;
- retrieval / reranking / refill / backfill / substitution;
- `exclude_ids`;
- any silent fallback.

If `is_replay_active() != TRUE` at the actual model-facing delivery point in P2,
or `get_delivery_artifact()` cannot return the verified frozen artifact, the arm
**ABORTS before model execution**. LIVE render is never an acceptable fallback.

### 7.5 State taxonomy (design requirement vs source state)

| Aspect | Design requirement | Current source state | Required implementation change | Required test proof |
|---|---|---|---|---|
| F2 frozen-delivery invariant | Delivery from verified frozen artifact; ABORT if replay inactive | **NOT met** — delivery seams fall back to `render_active_lessons()` when inactive (stream_runner:658,770; agent_service_v2:2667) | Minimal conditional enforcement at the three seams (§7.2, §9.1, §15) | items 8, 18, 19 in §14 |
| Replay state in P2 | P2 owns authoritative `_f2_state` | main.py installs replay from env at startup (main.py:522-541); process-local | adapter establishes P2 with F2 replay env; no cross-process assumption | items 3-6 in §14 |
| C0 vs LIVE discrimination | replay state, not artifact truthiness | `get_delivery_artifact()` returns `""` for C0 and for non-replay; no state marker distinguishing them | enforce discrimination at delivery seam by replay state | item 19 in §14 |
| Cold-start | F2-required backend must ABORT, not serve LIVE | a cold backend with no F2 env currently serves LIVE | adapter/fresh-backend must set F2 env; cold-start without it → ABORT | item 18 in §14 |

## 8. Fresh-process contract

- Every real F2 arm preserves the fresh-process contract.
- Authorized: a fresh execution backend (P2); **no separate CLI/task process is
  required** for the `P0 → P1 → P2` HTTP graph (a CLI/task process may be used
  only when a future task is routed through CLI-shaped machinery, which then
  requires its own authorization); repository-root bootstrap; explicit process
  identity capture.
  [HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY AMENDMENT:
  the prior phrase "a fresh CLI/task process where the existing machinery
  requires it" is superseded as a generally-required item.]
- Replay state must never be assumed to cross a subprocess via inherited Python
  globals/ContextVars — `_f2_state` is process-local.
- Do NOT use `PYTHONPATH` / editable-install tricks as a substitute for the
  established execution contract.

## 9. F1 technical-reuse boundary

The following are **reusable technical machinery; NOT F2 scientific
authorization** (design §14.6):

- `cli_baseline_swe._reset_instance`
- `f1_infra.start_backend_fresh`
- `f1_infra.wait_for_backend`
- `run_curriculum._attempt_once` (NOT part of the selected `P0 → P1 → P2` HTTP
  graph — future CLI-shaped use requires separate authorization)
- existing replay-gated delivery in `swarm_os/services/stream_runner.py` and
  `runtime_v2/api/agent_service_v2.py`

Explicit statement: **F1 scientific authorization is NOT being reused.** No F1
experimental authorization, sample size, endpoint, or scientific conclusion is
transferred merely by reusing code. These mechanisms are read-only resources
unless this authorization explicitly identifies a minimal defect that must change
(it does, for the delivery seams only — see §15). Prefer read-only reuse.

### 9.1 Delivery-seam enforcement scope (minimal, conditional)

The three delivery seams listed above currently implement the LIVE-fallback
behavior documented in §7.1. This authorization permits ONLY the minimal
conditional enforcement needed to make the F2 invariant (§7.2) hold at those
seams:

- When F2 replay is REQUIRED (the run is an F2 arm), an inactive replay state at
  the delivery point MUST cause an ABORT (no model request, no trajectory/
  delivery evidence accepted).
- Non-F2 executions MUST remain unchanged.

This is NOT authorization for broad refactoring of the delivery seams. Any
modification must: be minimal; preserve non-F2 behavior; preserve T/X/C0
derivation; preserve manifest verification; preserve fail-closed startup; avoid
changing scientific definitions; avoid changing F1 scientific behavior.

## 10. Adapter responsibilities

The adapter (`qwen_train/f2_execution_adapter.py`) must:

1. receive a verified F2 arm/manifest from the worker;
2. preserve arm identity;
3. establish a fresh execution backend;
4. explicitly propagate the F2 replay environment into P2;
5. verify and record the serving backend process identity — `p2_serving_pid`
   and `p2_serving_start_time` identify the process owning the listening socket
   serving the F2 task; `p2_launcher_pid`/`p2_launcher_start_time` and
   `p2_evidence_pid` and `command_identity` are recorded separately; expected-PID
   equality is NOT a requirement (2026-09-29 F2 AUTHORITY AMENDMENT);
6. wait for backend readiness;
7. establish task delivery to P2 directly over loopback HTTP/SSE (`127.0.0.1`);
   a CLI-shaped task process is used only if a task is routed through CLI-shaped
   machinery (not required by the `P0 → P1 → P2` graph);
8. preserve rollout/trajectory identity;
9. prove replay is active in P2;
10. prevent LIVE fallback;
11. collect backend/CLI/task/model/outcome evidence;
12. return that evidence to the F2 worker;
13. bind the evidence to the frozen manifest (recording the committed F0
    delivery identity: `lesson_block_hash`, `final_prompt_hash`,
    `delivery_timestamp`, backend PID, backend start time — F0 §4/§7/§9; this
    does NOT add a delivery-time hash-mismatch ABORT requirement);
14. contribute the required evidence layers to the F2 receipt;
15. propagate failures and timeouts as fail-closed failures.

The exact Python call structure inside the adapter is implementation work.

## 11. Worker integration boundary

Minimal changes to `qwen_train/f2_arm_worker.py` ONLY as required to invoke the
adapter and incorporate its execution evidence.
- Do NOT redesign the worker.
- Do NOT remove existing manifest verification.
- Do NOT weaken existing fail-closed behavior.
- Do NOT change existing T/X/C0 derivation rules.

## 12. Receipt / evidence contract

Implement collection of the six evidence layers:

A. F2 controller/worker — P1 / F2 primitives;
B. execution backend — adapter from P2;
C. CLI/task — **optional**; produced from a CLI-shaped task process only when a
   task is routed through CLI-shaped machinery; for the `P0 → P1 → P2` HTTP graph
   it is absent (P1 drives P2 directly);
D. model/provider — adapter from P2;
E. task endpoint/outcome — adapter;
F. frozen-delivery identity — F2 primitives (manifest identity, hashes, delivery
   identity).

Bind them with the design identity fields: experiment ID, arm, task ID, rollout
ID, trajectory run ID, manifest identity, treatment identity, delivery identity,
process identity.

Resolved by the 2026-09-29 F2 AUTHORITY AMENDMENT: backend/CLI process-identity
producer wiring is resolved for the HTTP graph — the backend identity is the
serving process (socket/listener owner) recorded via `p2_serving_pid` +
`p2_serving_start_time`, with launcher and evidence-writer identities recorded
separately; a CLI identity exists only if a CLI-shaped task process is used.
Remaining unresolved (NOT treated as already resolved): whether the F2 receipt
wraps or joins F1 evidence; the source of first-edit/endpoint evidence. Each must
be resolved and documented in implementation/test evidence. Do NOT change F2
scientific definition merely to make receipt implementation convenient.

## 13. Fail-closed matrix

| Case | Behavior |
|---|---|---|
| Manifest missing / unreadable / hash invalid | ABORT, no model execution |
| Arm mismatch (requested != manifest.arm) | ABORT |
| T/X/C0 identity mismatch vs manifest | ABORT |
| Replay cannot be installed in P2 | ABORT |
| F2 replay required + `is_replay_active()` false in P2 at delivery point | ABORT before model execution; NO LIVE render; NO trajectory/delivery evidence accepted |
| `get_delivery_artifact()` items missing in P2 | ABORT |
| Delivered artifact hash != manifest `treatment_set_hash` | ABORT [DESIGN-PROPOSAL — NON-NORMATIVE — NOT CURRENT F2 AUTHORITY: committed F0 authority requires RECORDING the delivery identity; whether a delivery-time hash mismatch is itself an abort condition is NOT resolved by the 2026-09-29 amendment] |
| Execution/backend process identity unestablished / mismatched | ABORT |
| Backend not the expected fresh process | ABORT [HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY AMENDMENT: P2 identity is the serving process (socket/listener owner) with its own start time; launcher and evidence-writer identities recorded separately; expected-PID equality is NOT a requirement] |
| Task crosses an unauthorized LIVE path (brain/repair/`/heal`/simulation) | ABORT |
| Child / backend / CLI timeout | ABORT (fail closed) |
| Model / task execution fails | propagate as failure |
| Required execution evidence missing | INVALID RUN |
| Receipt cannot bind evidence to frozen manifest | INVALID RUN |

Replay-state discrimination is by `is_replay_active()` / `_f2_state`, never by
artifact truthiness. C0 empty-artifact (replay active) is valid C0; it is never
interpreted as replay-inactive / LIVE. LIVE `render_active_lessons()` is never an
acceptable fallback for an F2 arm.

## 14. Test authorization

Authorize only tests directly proving the worker-execution seam. The final
authorized test inventory:

1. adapter receives verified manifest;
2. correct arm identity;
3. fresh backend;
4. correct F2 environment propagation;
5. P2 independently installs replay;
6. P2 replay is active;
7. delivery artifact matches frozen manifest;
8. **LIVE fallback aborts** — F2 replay required + P2 replay inactive → ABORT and
   LIVE delivery is impossible (see below);
9. missing/invalid manifest aborts;
10. arm mismatch aborts;
11. process identity mismatch aborts;
12. backend/CLI timeout aborts;
13. model/task failure propagates;
14. brain/repair_engine bypass paths are not used;
15. receipt contains required evidence layers;
16. evidence binds to the correct manifest/arm/task/rollout;
17. fresh-process behavior is actually exercised, not mocked away;
18. **cold-start exclusion** — F2 replay-required cold backend with no replay
    environment/state cannot serve LIVE lessons (ABORT, no LIVE render, no model
    request);
19. **C0 empty-artifact distinction** — F2 replay active + verified C0 + empty
    delivery artifact is accepted as C0 and is never interpreted as LIVE
    (replay state distinguished from artifact truthiness).

Mandatory test for item 8 must establish ALL of:
- F2 replay is explicitly required;
- P2 has no active verified replay state;
- delivery reaches the relevant seam;
- execution aborts;
- `render_active_lessons()` is NOT used;
- no model request occurs;
- no successful trajectory / model-delivery evidence is emitted.

Test scope is strictly at the worker/execution seam. Do NOT turn these into N=2
or Experiment J scientific execution. Do NOT authorize N=2, Experiment J
execution, or scientific evaluation.

## 15. Exact file-scope boundary

- **AUTHORIZED NEW FILE:** `qwen_train/f2_execution_adapter.py`.
- **AUTHORIZED EXISTING FILE:** `qwen_train/f2_arm_worker.py` — only as minimally
  required for adapter integration.
- **AUTHORIZED MINIMAL DELIVERY-SEAM ENFORCEMENT:** the three delivery seams in
  §7.1 (`runtime_v2/services/stream_runner.py` at ~658 and ~770;
  `runtime_v2/api/agent_service_v2.py` at ~2667) — ONLY the minimal conditional
  enforcement required to ABORT when F2 replay is required but inactive (§7.2,
  §9.1). Non-F2 behavior must remain unchanged. This is NOT authorization to
  redesign, refactor, or globally alter those files.
- **AUTHORIZED TEST FILES:** only new or existing tests directly covering the
  adapter / worker execution seam and the delivery-seam enforcement.
- **POTENTIALLY READ-ONLY REUSED FILES:** the F1 machinery listed in §9.
- Everything else is unauthorized; no silent scope expansion.

## 16. Explicit exclusions

The following are NOT authorized in any way by this document:

- N=2;
- Experiment J execution (T/X/C0 observations);
- scientific evaluation;
- certification;
- promotion;
- QLoRA;
- frontend;
- deployment;
- production rollout;
- unrelated infrastructure;
- unrelated tests;
- F1 scientific changes;
- F0 changes;
- changes to frozen scientific definitions;
- changes to T/X/C0 construction semantics;
- live fallback behavior;
- broad refactoring.

## 17. Authorization status

**AUTHORIZED** for implementation of the F2 worker-execution seam (Architecture
D) and the minimum integration described above, within the exact scope.

**NOT AUTHORIZED** for any item in §16 and for F2 scientific execution, N=2, or
Experiment J.

## 18. Implementation has NOT yet occurred

This document authorizes implementation work within the exact scope above.

It does NOT mean implementation has occurred.

It does NOT mean tests have passed.

It does NOT authorize F2 scientific execution.

It does NOT authorize N=2.

It does NOT authorize Experiment J.

It does NOT authorize evaluation, certification, promotion, or deployment.

---

*Authorized: 2026-09-29*
*Operator: Rob (human operator)*
*Scope: F2 worker-execution seam — Architecture D thin execution adapter (design §14)*
*Boundaries preserved: F0, F1 authorization, F2 execution contract, F2 orchestrator design, F2 state documentation*
*Not authorized: N=2, Experiment J execution, evaluation, certification, promotion, QLoRA, frontend, deployment*