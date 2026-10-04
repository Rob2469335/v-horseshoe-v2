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

## 13. F2 Change-Control and Clarification Mechanism

This document may carry **dated F2 clarification entries**. This mechanism is part
of the F2 authorization and is recorded here so that an implementation ambiguity
left by a higher-authority document can be resolved without editing that document.

1. **Dated clarification entries are permitted.** An entry is appended to §14 with
   a date and a reference id `F2-CLARIFICATION-<n>`.
2. **Clarifications resolve ambiguity in the existing authorized contract.** They
   record how an already-authorized contract is to be interpreted where the
   higher-authority document is silent. They add no new science.
3. **They MUST NOT silently modify frozen F0** (`docs/EXPERIMENT_J.md`) or F1
   authority. A clarification that would change a frozen element is not a
   clarification and is out of scope.
4. **They MUST NOT expand implementation scope** beyond §10 unless a separate,
   explicit authorization is granted and recorded.
5. **Each entry records:** date; issue; authoritative decision; rationale;
   affected contract; and whether implementation authorization is changed.
6. **This mechanism is itself part of the F2 authorization.** It is subordinate to
   F0 and to the authority documents named in §1, and may not be used to
   circumvent them.
7. **This log may also carry dated implementation-authorization entries.** Where a
   clarification defers implementation to a later step (§13(4)), the required
   separate authorization is recorded here with a reference id
   `F2-IMPL-AUTH-<n>` and status `AUTHORIZED`. Only the repository operator may
   record a clarification or an implementation-authorization entry, and each such
   entry records its author and its date. An implementation-authorization entry MAY
   expand the §10 file boundary, but only by naming the additional files
   explicitly. It remains subordinate to F0 and to the authority documents named in
   §1, and it adds no new science.

## 14. F2 Clarification Log

| Ref | Item | Status | Date | Notes |
|-----|------|--------|------|-------|
| F2-CLARIFICATION-001 | F2 delivery-timestamp interpretation (Option A) | CLARIFICATION | 2026-10-04 | Dated F2 authorial interpretation per §13; does not modify F0 |
| F2-IMPL-AUTH-001 | Step 2b — canonical delivery-timestamp verification (implements `F2-CLARIFICATION-001` Option A) | AUTHORIZED | 2026-10-04 | Separate implementation authorization per §13(4) and §13(7); expands the §10 file boundary by naming exactly two files; does not modify F0 |
| F2-CLARIFICATION-002 | Same-second steps are AMBIGUOUS and excluded from the endpoint window | CLARIFICATION | 2026-10-04 | Dated F2 authorial interpretation per 13; does not modify F0; adopts the conservative reading, stated in the entry |
| F2-IMPL-AUTH-002 | Step 2c - delivery-timestamp hardening (implements F2-CLARIFICATION-001/002 and a plausibility floor) | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); names no files beyond the two already named in F2-IMPL-AUTH-001; does not modify F0 |

### F2-CLARIFICATION-001 - F2 delivery-timestamp interpretation (Option A)

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Issue.** F0 (`docs/EXPERIMENT_J.md:172`) fixes `delivery_timestamp = time.time()`
(Unix epoch seconds, with a fractional component), while trajectory step
timestamps are canonical whole-second ISO-8601 UTC strings
(`runtime_v2/api/agent_service_v2.py:510`). F0 (`:103`, `:109`) requires strict
behavioural ordering `step.timestamp < delivery_timestamp`, but does not specify
how the two timestamp domains are compared. Comparing a string to a float is
type-incoherent, so the rule is not directly implementable as literally written.

**Authoritative decision (Option A).** For F2 ordering, the Unix-epoch
`delivery_timestamp` is converted to UTC and **floored/truncated to the whole Unix
second** before comparison with the whole-second trajectory timestamp. The
comparison remains **strict**: `step_timestamp < delivery_timestamp_second`.

Consequences:

- a step in an earlier UTC second is BEFORE delivery;
- a step in the **same** UTC second is **NOT BEFORE** delivery;
- a step in a later UTC second is NOT BEFORE delivery.

Flooring/truncation is explicit: fractional delivery precision is deliberately
NOT used to claim ordering precision the trajectory does not contain. Rounding to
nearest second is NOT used; ceiling is NOT used.

**Rationale.** The trajectory records whole seconds. Flooring keeps the strict
rule conservative and deterministic, and avoids inventing sub-second ordering that
the retained evidence cannot support.

**Affected contract.** F2 delivery-identity ordering only (`delivery_timestamp`
vs the trajectory `step.timestamp`). F0 is unchanged, and this clarification does
NOT claim that F0 originally specified this behaviour; it is an F2 interpretation
filling an ambiguity F0 leaves open.

**Implementation authorization.** This clarification authorizes the
**verifier-side timestamp interpretation only**. It does NOT authorize unrelated
runtime changes, worker changes, prompt-hash changes, `f2_arm_worker.py` changes,
or F0 edits. The timestamp conversion itself is implemented in a later,
separately-authorized step.

### F2-IMPL-AUTH-001 - Step 2b: canonical delivery-timestamp verification

**Author:** Rob (human operator) — the repository operator named in the
authorization footer of this document.

**Date:** 2026-10-04

**Authority.** Recorded under §13(4) and §13(7) as the separate, explicit
authorization that `F2-CLARIFICATION-001` deferred to ("implemented in a later,
separately-authorized step"). This entry grants implementation authority only. It
records no new science and modifies no frozen element.

**Issue.** The F0 timestamp domains are type/precision-incoherent for direct
comparison. F0 (`docs/EXPERIMENT_J.md:172`) fixes
`delivery_timestamp = time.time()` (Unix epoch seconds, with a fractional
component), while trajectory step timestamps are canonical whole-second ISO-8601 UTC
strings (`runtime_v2/api/agent_service_v2.py:510`), and F0 (`:103`, `:109`) requires
strict ordering `step.timestamp < delivery_timestamp`. Comparing a canonical string
to a float is not type-coherent, so the rule is not directly implementable as
literally written.

**Decision.** `F2-CLARIFICATION-001` Option A is the authoritative implementation
interpretation. The verifier converts the Unix-epoch `delivery_timestamp` to UTC and
**floors/truncates it to the whole Unix second**, then compares at whole UTC-second
resolution. Ordering remains strict:

`step_timestamp < delivery_timestamp_second`

- a step in an earlier UTC second is BEFORE delivery;
- a step in the **same** UTC second is **NOT BEFORE** delivery;
- a step in a later UTC second is NOT BEFORE delivery.

Flooring is explicit: fractional delivery precision is NOT used to claim ordering
precision the trajectory does not contain. Rounding to nearest second is NOT used;
ceiling is NOT used. Trajectory timestamps remain canonical ISO-8601 UTC timestamps.

**Rationale.** Trajectory timestamps have whole-second precision, while delivery
timestamps originate from fractional Unix epoch seconds. Flooring the delivery
timestamp avoids manufacturing sub-second ordering precision and makes same-second
observations conservatively NOT-BEFORE.

**Scope.** Verifier-side conversion only. The conversion MUST live in a **separate
delivery-timestamp parsing/conversion path**. The generic canonical trajectory
timestamp parser MUST remain strict. Trajectory timestamps are NOT reformatted,
re-serialized, or re-interpreted.

**File boundary.** This entry expands the §10 bounded file list by naming the
following two additional files, and by no others:

- `qwen_train/f2_protocol.py` — ONLY the verifier-side delivery-timestamp
  parsing/conversion and ordering logic necessary to implement
  `F2-CLARIFICATION-001`.
- `tests/test_f2_protocol.py` — ONLY tests necessary to establish the authorized
  timestamp contract and preserve existing F2 protocol behavior.

No other production or test file is authorized by this entry.

**Tests.** The implementation MUST include focused regression tests covering:

- valid delivery timestamps;
- malformed / non-finite delivery timestamp values;
- flooring rather than rounding;
- same-second rejection;
- earlier-second acceptance;
- later-second rejection;
- preservation of strict canonical trajectory timestamp parsing.

**Explicit non-authorization.** This entry does NOT authorize:

- `docs/EXPERIMENT_J.md` (frozen F0);
- `runtime_v2/api/agent_service_v2.py`;
- `runtime_v2/services/f2_replay.py`;
- `qwen_train/f2_arm_worker.py`;
- `qwen_train/f2_governance.py`;
- `qwen_train/f2_evidence.py`;
- `qwen_train/f2_endpoint.py`;
- prompt-hash redesign;
- worker fallback changes;
- runtime timestamp precision changes;
- F2 experiment execution;
- real F2 evidence generation;
- service startup/restart;
- unrelated cleanup or refactoring.

**Implementation status.** This entry **authorizes future implementation; it does not
itself implement anything.** Step 2b is NOT implemented by this entry. No production
code, no test code, and no runtime timestamp production is changed by recording this
authorization. Implementation remains a separate, subsequent step.

### F2-CLARIFICATION-002 - Same-second steps are AMBIGUOUS and excluded from the endpoint window

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Issue.** F2-CLARIFICATION-001 defines only the BEFORE test (step_timestamp < floored delivery second) and states that a step in the same UTC second is NOT BEFORE delivery. It does not say whether such a step belongs to the endpoint window. The verifier (`qwen_train/f2_protocol.py`, comparison `step_dt > delivery_dt`) excludes it from the window. F0 (`docs/EXPERIMENT_J.md:103`, `:109`) defines only the strictly-earlier (rediscovery) rule and defines no strictly-after rule; the strictly-after window was previously asserted only by the subordinate `docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md`.

**Authoritative decision.** For F2 endpoint reconstruction a step is inside the endpoint window only if its UTC second is strictly later than the floored delivery second. A step in the SAME UTC second as the floored delivery second is AMBIGUOUS: it is not BEFORE delivery (it is not treated as rediscovery) and it is NOT in the endpoint window. It is excluded from endpoint reconstruction. If no step remains in the window the run is SCIENTIFICALLY_INSUFFICIENT, not censored.

**Rationale.** Step timestamps have whole-second precision and delivery is fractional, so the order of a same-second step relative to delivery cannot be established from retained evidence. Counting it could attribute pre-delivery behaviour to the lesson. Excluding it is the conservative choice for the primary causal claim. F0's literal wording could be read to count such a step; this entry does not claim otherwise and records a deliberate conservative interpretation.

**Affected contract.** F2 endpoint window only. F0 is unchanged.

**Deferred.** Recording ambiguous or rediscovery steps (the F0 rediscovery flag and contamination diagnostics, `docs/EXPERIMENT_J.md:104`, `:107`) is NOT implemented and is NOT authorized by this entry. It requires a separate authorization.

**Implementation authorization.** None beyond F2-IMPL-AUTH-002. The current verifier behaviour already matches this decision.

### F2-IMPL-AUTH-002 - Step 2c: delivery-timestamp hardening

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7). Implements F2-CLARIFICATION-001 and F2-CLARIFICATION-002 and the plausibility floor below. Records no new science and modifies no frozen element.

**Issue.** An evidence run on 2026-10-04 showed that `parse_delivery_timestamp` (a) raises OverflowError for an integer too large for float instead of rejecting it; (b) accepts 0 and negative values, so a mistaken value can silently place delivery before every step; and (c) the existing test `test_float_seam_timestamp_is_rejected_by_regrade` passes for an unrelated reason and its comment is now false.

**Decision.**
1. Guard numeric conversion in `parse_delivery_timestamp` so that no input can raise; every failure returns (None, reason).
2. Reject any delivery instant earlier than 2025-01-01T00:00:00Z (Unix 1735689600), for both epoch and ISO inputs. No upper bound is added.
3. In the tests, revise exactly one existing test and its comment: rename `test_float_seam_timestamp_is_rejected_by_regrade` so the name states what it asserts, use a fixture whose delivery second lies between the fixture's step timestamps, and assert VERIFIED. Add focused tests for the guard and the floor.
4. Ratify the documentation edits already made in `_reconstruct_endpoint` (the docstring paragraph and the comment above the `step_dt > delivery_dt` comparison) as documentation of F2-CLARIFICATION-001/002.

**File boundary.** Only the two files named in F2-IMPL-AUTH-001, and only: `qwen_train/f2_protocol.py` - `parse_delivery_timestamp`, its constants, and the documentation edits in item 4; `tests/test_f2_protocol.py` - new tests and the single revision in item 3.

**Explicit non-authorization.** Everything listed as not authorized in F2-IMPL-AUTH-001, plus: any rediscovery flag or ambiguity recording, `organism_console/*`, and `.github/*`.

**Implementation status.** This entry authorizes future implementation; it does not itself implement anything.

---

*Authorized: 2026-09-29*
*Operator: Rob (human operator)*
*Scope: F2 orchestrator implementation — bounded file set (§10), scientific design unchanged (§3-§9)*
*Boundaries preserved: F0, F1, F2 execution contract, design document, state documentation*
*Not authorized: N=2, Experiment J execution, evaluation, certification, promotion, QLoRA, frontend*