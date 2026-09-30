# Experiment J F2 — Orchestrator Implementation Authorization

**Status:** IMPLEMENTATION AUTHORIZATION — authorizes implementation of the already-completed F2 orchestrator design. **DESIGN ONLY scope ends here; this authorizes code changes to a bounded file set.**
**Date:** 2026-09-29
**Authority:**

```
F0 (frozen) → F1 authorization → F2 execution-contract authorization (bb38ed85)
  → F2 orchestrator design (docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md, DESIGN COMPLETE — READY FOR IMPLEMENTATION AUTHORIZATION)
  → THIS implementation authorization
  → AGENTS.md
```

This document does **NOT** modify or supersede F0, F1 authorization, or the F2
execution-contract authorization. It implements the design and nothing else.

---

## 1. Authority

- **F0** (`docs/EXPERIMENT_J.md`) — frozen scientific authority: T/X/C0 arm
  definitions, treatment identity, fresh-worker definition, contamination
  framework, rediscovery rule, primary endpoint. Not changed.
- **F1** (`docs/EXPERIMENT_J_F1_AUTHORIZATION.md`) — F1 parameters
  (k/n/relevant_file_set/P95). Not changed.
- **F2 execution-contract** (`docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md`):
  every F2 fresh child MUST `sys.path.insert(0, REPO_ROOT)` before repository
  imports; child independently loads and verifies its manifest. Binding here.
- **F2 orchestrator design** (`docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md`,
  DESIGN COMPLETE): the specification this authorization implements.
- **This document:** grants implementation of that specification only.
- **AGENTS.md:** operating rules.

## 2. Exact Scope

Authorize implementation of the F2 orchestrator described in the design
document, covering **only** its defined responsibilities:

1. Governed T creation via `LessonManager.render_active_lessons()`
   (swarm_os/services/lesson_manager.py:358).
2. T freeze via `freeze_artifact` (runtime_v2/services/f2_freeze.py:210) +
   `persist_manifest` (f2_freeze.py:434).
3. Deterministic X derivation (design §2) from frozen ordered records.
4. C0 construction (design §3) — serialized empty-treatment control.
5. Manifest persistence.
6. Fresh child process per arm (design §4) — one fresh process per arm.
7. Repository-root bootstrap (`sys.path.insert(0, REPO_ROOT)`).
8. Child independent manifest load (`load_manifest`) + verify (`verify_manifest`).
9. Replay installation (`install_replay_state`/`install_verified_replay_from_env`).
10. Authoritative delivery via `get_delivery_artifact()` + `is_replay_active()`.
11. Fail-closed behavior (design §8 matrix).
12. Arm execution (single governed arm per process).
13. Arm receipts/evidence (design §7 schema).
14. Process isolation (design §9) — no multi-arm reuse, immutable artifacts.
15. Delivery instrumentation (design §6) — lesson_block_hash, final_prompt_hash,
    delivery_timestamp.
16. Design invariants and validation (design §11 A–H).

No expansion of the scientific design is authorized.

## 3. X Derivation Contract (non-negotiable)

X MUST be derived from the **frozen ordered lesson records**. L MUST be
identified by `(position, lesson_id, lesson_hash)` as represented in the frozen
`FrozenArtifact` (`lesson_l_id`, `lesson_l_hash`, `ordered_lessons`).

X MUST:
- preserve all non-L frozen records;
- preserve original ordering (no renumbering of non-L positions; gap remains);
- remove exactly L;
- preserve frozen metadata;
- never re-query lessons;
- never rerank;
- never refill;
- never backfill;
- never substitute;
- never live-render with `exclude_ids`;
- never identify L by text equality;
- never reparse rendered T text to discover L.

If frozen-record validation fails (L missing, L duplicated, positions
duplicated/malformed/out-of-range, inconsistent `(position, id, hash)`
relationship, inability to map L to exactly one rendered position, or X would
remove anything other than L), the arm MUST fail closed.

## 4. T-before-X Proof

The implementation MUST make it mechanically/evidentially provable that the
order is `render T → freeze T → derive X`, not `render T → derive X → freeze`.
Provenance MUST link X to the frozen T artifact (the design's
`source_t_manifest` provenance link, design §2.3 item 7). The T manifest's
`content_address` MUST be recorded in X's provenance.

## 5. C0

Authorize C0 as the **explicitly serialized empty-treatment control** (design §3):
- same fresh-worker mechanism as T/X;
- `rendered_artifact = ""`, `arm = "C0"`;
- no treatment artifact delivered;
- routes through the same manifest load/verify/install boundary;
- must not inherit live treatment state (replay delivers `""`, not LIVE);
- remains distinguishable from T and X in evidence via `arm` + identity fields.

## 6. Fresh-Process Contract

The F2 execution-contract authorization remains binding and is NOT replaced:
- every fresh Python child MUST explicitly insert the repository root at the
  front of `sys.path` (`sys.path.insert(0, REPO_ROOT)`) before repository
  imports;
- NOT via PYTHONPATH;
- NOT via cwd assumptions;
- NOT via editable-install discovery;
- NOT via implicit import behavior;
- NOT via another undocumented mechanism.

The child MUST independently load and verify the manifest.

## 7. Delivery Boundary

The implementation MUST use the authoritative F2 delivery provider
(`get_delivery_artifact()` / `is_replay_active()`, consumed at
agent_service_v2.py:2667-2668 and stream_runner.py:658-659,770-771). It MUST
detect and fail closed on any delivery bypass (design §5.3, abort
`contamination_delivery_bypass`). Silent modification of legacy bypass paths
(`swarm_os/brain.py:270,280`; `organism_console/core/repair_engine.py:656`) is
NOT authorized unless this document explicitly requires it — it does not; the
delivery-boundary requirement is enforced by construction of the F2 arm and by
fail-closed detection.

Documented F1 facts to preserve:
- `brain.py` is NOT reachable from the F1 worker path
  (`run_curriculum → organism_console --json → stream_runner/agent_service_v2`);
- `repair_engine.py` is NOT reachable from the F1 worker path (reached via
  `/heal`, autonomy/watch-loop, or simulation paths only);
- F2 reachability MUST be verified empirically once the F2 worker exists
  (currently NOT PROVEN).

## 8. Evidence / Receipt Requirements

Implement the receipt schema already defined by the design (design §7) — no
additional scientific metrics. Evidence MUST distinguish: T, X, C0, process
identity, manifest identity, treatment identity, delivery identity,
`lesson_block_hash`, `final_prompt_hash`, `delivery_timestamp`, verification
result, failure reason, exit status. Invalid or incomplete evidence MUST NOT be
silently interpreted as model failure.

## 9. Fail-Closed Requirements

Authorize the design's fail-closed matrix (design §8). No silent fallback to
LIVE lessons. No execution when:
- manifest missing;
- manifest malformed;
- manifest hash invalid;
- treatment identity invalid;
- X derivation invalid;
- L cannot be uniquely identified;
- C0 is contaminated;
- delivery bypass detected;
- child verification fails;
- required evidence cannot be produced.

## 10. Exact Implementation Boundary

### Files that may be newly created or modified

This is a bounded, explicit list. Any implementation outside it requires a new
authorization.

- `qwen_train/f2_arm_orchestrator.py` (NEW) — parent orchestrator: T render →
  freeze → derive X → C0 → persist → spawn → collect evidence → validate
  receipt.
- `qwen_train/f2_arm_worker.py` (NEW) — child worker script: repo-root
  bootstrap → load → verify → install replay → arm validation → delivery →
  instrument → execute → return receipt.
- `qwen_train/f2_arm_primitives.py` (NEW, if needed) — `derive_x_from_frozen`,
  C0 construction, arm-evidence schema helpers, strictly bounded to design §§2-3,6-7.
- `runtime_v2/services/f2_freeze.py` (M) — ONLY if required to expose frozen
  ordered-record helpers to derivation (e.g., exposing position/hash access);
  must not change hashes or scientific semantics.
- `runtime_v2/services/f2_replay.py` (M) — ONLY if required to expose an arm-
  validation helper; must not change delivery or fail-closed semantics.
- `tests/test_f2_orchestrator.py` (NEW) — orchestrator tests (see §11).
- `tests/test_f2_derive_x.py` (NEW) — X-derivation/T-before-X/fail-closed tests.

### NOT authorized

- `docs/EXPERIMENT_J.md` (F0, frozen)
- F0 frozen artifacts
- `docs/EXPERIMENT_J_F1_AUTHORIZATION.md`
- `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md`
- `docs/LEARNING_EXPERIMENT_STATE.md`
- `docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md` (design frozen as authoritative)
- `AGENTS.md`, `WORK_LOG.md`
- unrelated production code (`agent_service_v2.py`, `stream_runner.py`, `brain.py`,
  `repair_engine.py`, `lesson_manager.py` — read-only references unless the
  bounded modifications above require touching f2_freeze/f2_replay only)
- unrelated tests
- unrelated infrastructure
- frontend
- model training
- QLoRA
- promotion
- certification

If repository inspection during implementation shows the exact file list differs
from the design assumptions, **STOP and report the discrepancy** rather than
guessing.

## 11. Verification Boundary

Authorize verification of the implementation only. NOT N=2, NOT Experiment J
execution, NOT evaluation, NOT certification.

Required verification:
- targeted F2 tests;
- fresh-process tests;
- T/X/C0 tests;
- manifest integrity tests;
- fail-closed tests;
- delivery-boundary tests;
- process-isolation tests;
- receipt/evidence validation;
- exact test commands and counts recorded;
- clean diff review.

## 12. Git Discipline

This step authorizes ONLY creation of this authorization document. Do NOT
implement anything in this step. Do NOT modify the design document. Do NOT modify
state documentation. Do NOT stage or commit.

A single logical implementation commit, prefixed per project convention (e.g.,
`ARCH:`), is separately permitted ONLY after (a) this document is committed and
(b) a separate implementation authorization step proceeds. Do NOT commit in this
step.

---

*Authorized: 2026-09-29*
*Operator: Rob (human operator)*
*Scope: F2 orchestrator implementation — bounded file set (§10), scientific design unchanged (§3-§9)*
*Boundaries preserved: F0, F1, F2 execution contract, design document, state documentation*
*Not authorized: N=2, Experiment J execution, evaluation, certification, promotion, QLoRA, frontend*