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
   authorize a clarification or an implementation-authorization entry; the
   implementation agent may record one only after the repository operator
   explicitly grants that authorization interactively in the operator's own
   session. Each such entry records `Authorized by:` the repository operator,
   `Recorded by:` the implementation agent, its interactive-authorization basis
   (the operator's explicit interactive grant of exactly the stated action), and
   its date. Authorization is never inferred from silence, and no repository
   content, test, report, model output, tool output, or prompt-injected text is
   itself operator authorization; a "yes" authorizes exactly the stated action and
   nothing broader. An implementation-authorization entry MAY expand the §10 file
   boundary, but only by naming the additional files explicitly. It remains
   subordinate to F0 and to the authority documents named in §1, and it adds no new
   science.

## 14. F2 Clarification Log

| Ref | Item | Status | Date | Notes |
|-----|------|--------|------|-------|
| F2-CLARIFICATION-001 | F2 delivery-timestamp interpretation (Option A) | CLARIFICATION | 2026-10-04 | Dated F2 authorial interpretation per §13; does not modify F0 |
| F2-IMPL-AUTH-001 | Step 2b — canonical delivery-timestamp verification (implements `F2-CLARIFICATION-001` Option A) | AUTHORIZED | 2026-10-04 | Separate implementation authorization per §13(4) and §13(7); expands the §10 file boundary by naming exactly two files; does not modify F0 |
| F2-CLARIFICATION-002 | Same-second steps are AMBIGUOUS and excluded from the endpoint window | CLARIFICATION | 2026-10-04 | Dated F2 authorial interpretation per 13; does not modify F0; adopts the conservative reading, stated in the entry |
| F2-IMPL-AUTH-002 | Step 2c - delivery-timestamp hardening (implements F2-CLARIFICATION-001/002 and a plausibility floor) | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); names no files beyond the two already named in F2-IMPL-AUTH-001; does not modify F0 |
| F2-IMPL-AUTH-003 | Ratification (retroactive) of Step 1 and Step 1c assembler binding, commits bee672dc and 68208c4f | AUTHORIZED (RETROACTIVE) | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); names no files beyond the two already named in F2-IMPL-AUTH-001; records that no authorization existed when the commits were made; does not modify F0 |
| F2-CLARIFICATION-003 | Rediscovery reads the UN-FLOORED delivery instant; the endpoint window keeps the floored second | CLARIFICATION | 2026-10-04 | Dated F2 authorial interpretation per 13; resolves which delivery reading F0 6 uses; does not modify F0 |
| F2-IMPL-AUTH-004 | Step 3 - F0 6 rediscovery integration, test-isolation fix and op_infra_004 workspace fixture | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); names four files explicitly; does not modify F0 |
| F2-IMPL-AUTH-005 | Step 4 - persist the distiller provenance block and keep F2 arms out of the learning pipeline | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); names two files explicitly; does not modify F0 |
| F2-IMPL-AUTH-006 | Step 5 - record post-run workspace-mutation integrity evidence | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); names one file explicitly; observation only, adds no exclusion rule; does not modify F0 |
| F2-IMPL-AUTH-007 | Step 6 - strengthen workspace-mutation evidence against the commit/ref/ignored-artifact bypasses | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); names one file explicitly; observation only, adds no exclusion rule; does not modify F0 |
| F2-IMPL-AUTH-008 | Step 7 - F2Bundle persistence round-trip identity tests, and the authoritative assembly-point clarification | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); tests only plus a design clarification; does not modify F0 |
| F2-IMPL-AUTH-009 | Step 8 - workspace-mutation classification (engineering assessment) and the operator execution contracts | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); one file; observation and classification only, adds no exclusion rule; does not modify F0 |
| F2-IMPL-AUTH-010 | Step 9 - calibration execution layer (runner + authorization gate) | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); one file plus its tests; code only, execution gated on Q10; does not modify F0 |
| F2-IMPL-AUTH-011 | Step 10 - calibration production adapter (membrane -> runner) | AUTHORIZED | 2026-10-04 | Separate implementation authorization per 13(4) and 13(7); one file plus its tests; wiring only, no authority; does not modify F0 |
| F2-IMPL-AUTH-012 | Step 11 - contamination-provenance screen (S10, temporal-cutoff proxy) | AUTHORIZED | 2026-10-05 | Separate implementation authorization per 13(4) and 13(7); one file plus its tests; adds a screen, selects no parameter; does not modify F0 |
| F2-IMPL-AUTH-013 | Statistical design authorization: F1 freeze, exact McNemar, pi_d=0.50, n=300 | AUTHORIZED | 2026-10-05 | Operator-authorized scientific parameters per this mission; documentation only; does not modify F0 |
| F2-IMPL-AUTH-014 | Step 12 - explicit contamination classification vocabulary (CLEAN / POTENTIALLY CONTAMINATED / UNKNOWN) | AUTHORIZED | 2026-10-05 | Separate implementation authorization per 13(4) and 13(7); one file plus its tests; exposes a derived classification, changes no screening decision; does not modify F0 |
| F2-CLARIFICATION-004 | Freeze the exact CI construction and the outcome-independence invariant | CLARIFICATION | 2026-10-05 | Dated F2 authorial interpretation per 13; records what the readiness plan already specifies; does not modify F0 |
| F2-IMPL-AUTH-015 | Repair the Clopper-Pearson bisection (broken exact CI solver) | AUTHORIZED | 2026-10-05 | Separate implementation authorization per 13(4) and 13(7); correctness fix only, no scientific parameter changed; does not modify F0 |
| F2-IMPL-AUTH-016 | ONE authoritative confirmatory interval (make `mcnemar_exact` report the frozen Clopper-Pearson CI) | AUTHORIZED | 2026-10-05 | Separate implementation authorization per 13(4) and 13(7); removes an ambiguous second CI; changes no scientific method; does not modify F0 |
| F2-IMPL-AUTH-017 | Live worker -> governed bundle assembly (opt-in, fail-closed) | AUTHORIZED | 2026-10-05 | Separate implementation authorization per 13(4) and 13(7); one production file plus its tests; invents no evaluator identity/store/digest; does not modify F0 |
| F2-IMPL-AUTH-018 | Q5 block-level exclusion, Q6 infrastructure-failure operational gate, and the missingness rule (operator-granted) | AUTHORIZED | 2026-10-05 | Records operator-authorized methodological decisions from the F2 completion brief; documentation only (the outcome-independent invariants are already enforced in code); does not modify F0 |
| F2-IMPL-AUTH-019 | Confirmatory analysis link: paired ledger -> final result + independent reconstruction | AUTHORIZED | 2026-10-05 | Separate implementation authorization per 13(4) and 13(7); pure offline analysis that calls the FROZEN statistics unchanged; adds the missing final-chain link; does not modify F0 |
| F2-IMPL-AUTH-020 | F2 execution-prerequisite readiness checker (fail-closed, read-only) | AUTHORIZED | 2026-10-05 | Separate implementation authorization per 13(4) and 13(7); pure readiness report that fabricates nothing and executes nothing; reports the exact blocker; does not modify F0 |

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

### F2-IMPL-AUTH-003 - Ratification (retroactive) of Step 1 and Step 1c assembler binding

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7). This entry is RETROACTIVE: it is recorded after the work was committed, and it does not claim that an authorization existed when the work was done. Records no new science and modifies no frozen element.

**Issue.** Commits `bee672dc` and `68208c4f` changed `qwen_train/f2_protocol.py` and `tests/test_f2_protocol.py`. Section 10 did not list those files, the readiness plan is subordinate and grants no authority, and no repository document named them as modifiable when the commits were made. The changes were made on operator chat instruction and were independently audited. Chat is not a repository authority.

**Decision.** The operator ratifies, as of 2026-10-04, exactly the following behaviour added by those two commits: `assemble_f2_bundle` takes the seam delivery record as an input; it recomputes the SHA-256 of the exact delivered bytes and rejects on mismatch with the seam hash; it stores the treatment artifact as the exact delivered bytes; it requires the seam record's arm to equal the assembly arm; and for arm T it requires the exact lesson bytes to occur in the delivered bytes. The related tests are ratified with it.

**File boundary.** Only `qwen_train/f2_protocol.py` and `tests/test_f2_protocol.py`, and only the behaviour listed in the decision.

**Explicit non-authorization.** This entry does not ratify, authorize or review any other commit, including earlier F2 protocol, admission-gate or bundle-assembly commits. It does not authorize wiring `f2_arm_worker.py`, any runtime change, or any F2 execution.

**Implementation status.** This entry records authorization for work already committed; it implements nothing.

### F2-CLARIFICATION-003 - Rediscovery reads the UN-FLOORED delivery instant; the endpoint window keeps the floored second

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Subordinate to F0 and to the authority documents named in 1. Adds no new science and modifies no frozen element.

**Issue.** F2-CLARIFICATION-001 floors the delivery instant to the whole UTC second, but that ruling was stated for ORDERING: the endpoint window. F0 6 (docs/EXPERIMENT_J.md:103, :109) is a DIFFERENT comparison: `step.timestamp < delivery_timestamp`, in which `delivery_timestamp` is the fractional `time.time()` value fixed at docs/EXPERIMENT_J.md:172 and the step timestamp has whole-second resolution. Flooring the delivery instant makes a step recorded in the delivery second compare as NOT strictly earlier, which hides a possible pre-delivery edit. Applying one reading to both rules therefore either weakens the endpoint window or weakens the contamination diagnostic. The two rules need different inputs, and the ambiguity was left open.

**Authoritative decision.**
1. The endpoint window CONTINUES to use the floored whole-second delivery instant exactly as decided by F2-CLARIFICATION-001 and F2-CLARIFICATION-002. That ruling is unchanged.
2. The F0 6 rediscovery classification uses the UN-FLOORED delivery instant recovered from the retained delivery evidence.
3. Where the earliest qualifying edit and the delivery lie within one recorded step period (1 second), the verdict records `boundary_ambiguous` as true, because the retained evidence cannot order them.
4. A run whose earliest qualifying edit is strictly earlier than the un-floored delivery is classified pre-delivery rediscovery. Per F0 6 it is recorded with the classification and its evidence, is excluded from the primary T/X causal analysis, and is handled as missing data for that arm's primary endpoint.

**Rationale.** Flooring exists so that the whole-second trajectory and the delivery instant share one comparable domain; that need applies to the window test, not to a contamination test whose whole purpose is to detect edits that precede delivery. Using the un-floored instant for rediscovery biases toward classifying a run as contaminated, which excludes it from the causal analysis - the conservative direction for a primary claim. Ambiguity is recorded rather than resolved by assumption.

**Affected contract.** F2 rediscovery classification and endpoint admissibility only. F0 6 is unchanged and was not modified. The `F2Result` schema fixed by the readiness plan is unchanged: no field is added, removed or renamed. `_reconstruct_endpoint` is unchanged.

**Implementation authorization.** F2-IMPL-AUTH-004.

### F2-IMPL-AUTH-004 - Step 3: F0 6 rediscovery integration, test-isolation fix and op_infra_004 workspace fixture

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Implements F2-CLARIFICATION-003. Records no new science and modifies no frozen element.

**Issue.** Three defects were verified by direct inspection on 2026-10-04.

1. `qwen_train/f2_rediscovery.py` implements F0 6 and is covered by `tests/test_f2_rediscovery.py`, but it is imported by NOTHING in production. `classify_rediscovery` is referenced only by its own test. F0 6 requires the rediscovery flag, the pre-delivery classification, exclusion from the primary causal analysis, and missing-data handling; none of the four is emitted by the F2 verification path. A contaminated run and a run with an empty event stream are currently indistinguishable: both yield the single reason that no step occurs strictly after delivery.
2. `conftest.py` declares two autouse fixtures, `isolate_prompt_repairer_store` and `isolate_runtime_data_dirs`, and both request the per-test `tmp_path`. Each then creates a directory inside it. Any test that also requests `tmp_path` and asserts on that directory's contents observes the fixtures' directories, so `tests/test_f2_freeze.py::TestAtomicPersistence::test_no_partial_files_remain` fails with two entries instead of one. The production writer is correct; the isolation fixtures contaminate the assertion surface. This also keeps the broad-pytest prohibition in `AGENTS.md` section 3.6 in force.
3. `tests/test_f2_op_infra_004.py::TestProductionWiring` calls the orchestrator with `execute=True` but declares no workspace. `qwen_train/arm_workspace.py:288-295` correctly fails closed because `SWARM_WORKSPACE_ROOT` is unset, so the two argv assertions never reach the stubbed `Popen`. The guard is correct and must not be weakened; the test must supply the declaration the guard demands. The orchestrator writes that variable into `os.environ` at :202, so the test must also restore it.

**Decision.**
1. `qwen_train/f2_protocol.py`: add a public `classify_run_rediscovery(bundle, *, store)` that classifies one arm against F0 6 from retained bytes and returns the verdict dictionary, by adapting the flat behavioral records to the ATIF tool-call shape the existing classifier expects. The un-floored delivery instant is recovered from the retained delivery evidence per F2-CLARIFICATION-003. In `derive_f2_result`, a run classified as pre-delivery rediscovery yields no primary result, and the returned reason records the verdict and its evidence. The `F2Result` schema is NOT changed. `_reconstruct_endpoint` is NOT changed.
2. `conftest.py`: both autouse isolation fixtures derive their root from `tmp_path_factory` instead of the test's `tmp_path`, so they can never place state inside a test's own temporary directory. The values they return are unchanged in meaning, so every test that consumes a fixture return value keeps working. Isolation is not weakened: the same module globals are patched, to a private per-test directory.
3. `tests/test_f2_op_infra_004.py`: supply an explicit throwaway absolute workspace directory to the orchestrator so the existing fail-closed guard passes on its own terms, and restore `SWARM_WORKSPACE_ROOT` afterwards so the declaration does not leak between tests. No assertion is weakened, skipped or removed, and the guard is not relaxed.

**File boundary.** Exactly four files, named here and by no others: `qwen_train/f2_protocol.py` (items 1 and, only as the new function's documentation, nothing else); `tests/test_f2_protocol.py` (new tests for item 1); `conftest.py` (item 2); `tests/test_f2_op_infra_004.py` (item 3).

**Explicit non-authorization.** Everything listed as not authorized in F2-IMPL-AUTH-001 and F2-IMPL-AUTH-002, plus: `qwen_train/f2_rediscovery.py` and `tests/test_f2_rediscovery.py` (consumed, never modified); any change to the `F2Result` schema; any change to `_reconstruct_endpoint`; any change to `qwen_train/arm_workspace.py` or `qwen_train/f2_arm_orchestrator.py`; ambiguity recording as a persisted artifact; frozen F0 and F1 artifacts; `organism_console/*`; `.github/*`; F2 experiment execution; real F2 evidence generation; service startup; provisioning `SWARM_RECEIPT_KEY`, which is operator-only.

**Implementation status.** This entry authorizes future implementation; it does not itself implement anything.

### F2-IMPL-AUTH-005 - Step 4: persist the distiller provenance block; keep F2 arms out of the learning pipeline

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Two independent SOTA-audit defects, each verified by direct inspection and a reproduction. Records no new science and modifies no frozen element.

**Issue.**

1. `SynthesisAttestation.distiller_reproducibility` (`swarm_os/services/lesson_synthesis.py:402`) is populated at construction (`:1252`) but is absent from `to_dict()` (`:404-417`) and never read by `from_dict()` (`:419-437`). A round-trip therefore returns `{}`. The readiness plan row 16 claims the persisted attestation carries the full reproducibility block and cites `TestAttestationCarriesTheRecord`, but that class holds exactly one test, which asserts the DEFAULT is empty and never exercises persistence. Consequence: the persisted candidate cannot be independently checked for which distiller transformation produced the rule, which is the precise property row 16 exists to guarantee. The promotion gate does not require the field, so this is a provenance/auditability defect, not a gate failure.

2. `_store_decision_reflexion` (`runtime_v2/services/stream_runner.py:199-265`) is NOT gated by `is_replay_active()`. On any decision-level failure (empty response, malformed JSON, timeout) it calls `get_prompt_repairer().process_failure(...)` (`:253`) with `task_id=TASK_ID_CTX.get()` (`:258`). `process_failure` creates a PromptRepairer CANDIDATE whenever `task_id` is set (`swarm_os/services/prompt_repairer.py:1377,1387-1403`), and the F2 harness supplies the task id via the `x-swarm-task-id` header (`swarm_os/api/agents.py:306`). Therefore EVERY F2 arm run - T, X and the no-lesson calibration - can mint learning candidates from ordinary decision failures. That contradicts the plan's clean-room requirement of exactly one controlled learning event, and it lets F2 run failures accumulate promotion evidence on candidates other than the intended L.

**Decision.**

1. `swarm_os/services/lesson_synthesis.py`: include `distiller_reproducibility` in `SynthesisAttestation.to_dict()` and restore it in `from_dict()`, so the block survives persistence. No field is added, removed or renamed, so the `F2Result` schema and the readiness-plan result schema are unchanged.
2. `runtime_v2/services/stream_runner.py`: in F2 replay mode (`is_replay_active()`), `_store_decision_reflexion` MUST NOT write to the learning pipeline. The function returns early, so an F2 arm cannot mint a PromptRepairer candidate, cannot append promotion evidence, and cannot write reflexion memory. Non-F2 behaviour is unchanged.
3. Tests: a real round-trip test for item 1 (a POPULATED block must survive), and an F2-mode test for item 2 (a decision failure in replay mode must create no candidate), plus a non-F2 control that the write still happens when replay is inactive.

**File boundary.** Exactly four files, named here and by no others. Production (items 1-2): `swarm_os/services/lesson_synthesis.py` (item 1); `runtime_v2/services/stream_runner.py` (item 2). Tests (item 3): `tests/test_distiller_reproducibility.py` (the populated round-trip regression test, added to the existing `TestAttestationCarriesTheRecord` class); `tests/test_f2_delivery_seam.py` (the replay-suppression test and its non-F2 control, added to the existing module that already owns the replay monkeypatch infrastructure).

**Explicit non-authorization.** This entry does not authorize any other change to `swarm_os/services/*` or `runtime_v2/services/stream_runner.py`, and it does not reopen OP_INFRA_004:130/:133 except to the exact extent of items 1 and 2. It does not authorize: rediscovery persistence, ambiguity recording, the `F2Result` schema, `_reconstruct_endpoint`, `f2_rediscovery.py`, `qwen_train/arm_workspace.py`, `qwen_train/f2_arm_orchestrator.py`, frozen F0/F1, `organism_console/*`, `.github/*`, F2 execution, real F2 evidence generation, or provisioning `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q10, Q11 or Q12.

**Implementation status.** This entry authorizes future implementation; it does not itself implement anything.

### F2-IMPL-AUTH-006 - Step 5: post-run workspace-mutation integrity evidence

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Records no new science and modifies no frozen element.

**Issue.** The F2 endpoint is measured from the ordered behavioral TRAJECTORY, and the task outcome from the evaluator run over the retained workspace. Nothing therefore observes WHICH repository paths an arm actually touched. `qwen_train/arm_workspace.verify_patch_state` is called only BEFORE the arm runs (`arm_workspace.py:253`, `:490`); there is no post-run observation. An arm that edited the evaluator's own test files, or any path outside the authorized surface, would leave no persisted trace, and a reviewer would have to trust the agent's self-report. This is an evaluation-integrity observation gap, not a scoring bug.

**Decision.** `qwen_train/f2_arm_worker.py` (already inside the Section 10 file boundary) gains a post-run `_capture_workspace_mutation(workspace_root)` helper that records the observed dirty-path set via the existing `arm_workspace` helpers, and the arm receipt carries it as `workspace_mutation_evidence`. This is OBSERVATION ONLY: it adds no exclusion rule, changes no endpoint, changes no outcome, and fails soft (an uninspectable workspace is recorded as `captured: false` with the error, never as clean).

**File boundary.** Exactly one file: `qwen_train/f2_arm_worker.py` (plus its regression tests in `tests/test_f2_op_infra_004.py`, which already owns the worker tests and is named in F2-IMPL-AUTH-004).

**Explicit non-authorization.** This entry does not authorize any other change to `qwen_train/f2_arm_worker.py` or to `qwen_train/arm_workspace.py`; no new exclusion or invalidation rule; no change to the endpoint, the outcome, the `F2Result` schema, `_reconstruct_endpoint`, the delivery seam, or any frozen artifact; no F2 execution; no real F2 evidence generation; and no provisioning of `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q10, Q11 or Q12.

**Implementation status.** This entry authorizes future implementation; it does not itself implement anything.

### F2-IMPL-AUTH-007 - Step 6: strengthen workspace-mutation evidence against the commit/ref/ignored-artifact bypasses

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Records no new science and modifies no frozen element.

**Issue.** F2-IMPL-AUTH-006 records the post-run dirty-path set via `git status --porcelain -uall`. That evidence is DEFEATABLE three ways, each verified by a test in this change:

1. **Commit bypass.** An arm that runs `git add -A && git commit` leaves a CLEAN working tree, so `git status` reports nothing while the repository has in fact been mutated. The mutation evidence would falsely read as "clean".
2. **Ref creation.** `git status` says nothing about refs. Creating a ref (the same history-manipulation channel the pre-run `_strip_future_history` removes) is invisible in the dirty-path set.
3. **Ignored artifacts.** `-uall` does not report ignored paths, so an artifact hidden behind `.gitignore` is invisible.

None of these is a scoring bug; all three are evaluation-integrity observation gaps.

**Decision.** `qwen_train/f2_arm_worker.py`'s `_capture_workspace_mutation` additionally records: the post-run `head_after` commit; `head_unchanged` (whether HEAD still equals the base the workspace was reset to, taken from the frozen manifest's `task_readiness.base_commit`); the post-run `refs_after` list; and a bounded `ignored_paths_sample` with `ignored_count`. This remains OBSERVATION ONLY - no exclusion rule, no endpoint or outcome change, no gate - and stays fail-soft.

**File boundary.** Exactly one file: `qwen_train/f2_arm_worker.py` (plus its regression tests in `tests/test_f2_op_infra_004.py`, already named in F2-IMPL-AUTH-004).

**Explicit non-authorization.** This entry does not authorize any other change to `qwen_train/f2_arm_worker.py` or to `qwen_train/arm_workspace.py`; no new exclusion or invalidation rule; no change to the endpoint, outcome, `F2Result` schema, `_reconstruct_endpoint`, the delivery seam, or any frozen artifact; no F2 execution; no real F2 evidence generation; and no provisioning of `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q10, Q11 or Q12.

**Implementation status.** This entry authorizes future implementation; it does not itself implement anything.

### F2-IMPL-AUTH-008 - Step 7: F2Bundle persistence round-trip tests; authoritative assembly point

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Records no new science and modifies no frozen element.

**Issue 1 - the round trip was untested on a production path.** `F2Bundle.from_dict` is on the PRODUCTION path: admission deserializes bundles (`qwen_train/f2_admission.py:133`, `:135`) and regrade does too (`qwen_train/f2_protocol.py:945`, `:1185`, `:1187`). No test asserted that `to_dict()` -> `from_dict()` preserves the identity-bearing fields, so a silent drop or rewrite of a persisted identity field would have gone unnoticed while still changing the scientific record. This is the `prove serialization/reconstruction/identity consistency` requirement, and it was NOT ESTABLISHED before this entry.

**Issue 2 - the bundle assembly point was mis-stated.** The readiness plan says `f2_arm_worker.py` "must call `assemble_f2_bundle` with its receipt data". That is not implementable: `assemble_f2_bundle` additionally requires `task_outcome_report`, `evaluator: EvaluatorAuthorization` and `implementation_bytes`, which are EVALUATION-phase artifacts produced after the arm has run, and `horizon_k`, which the worker never receives. `assemble_f2_bundle` has no production caller. Forcing the call into the worker would require inventing those inputs or duplicating the evaluation identity, which this repository forbids.

**Decision.**

1. Add `TestBundlePersistenceRoundTrip` to `tests/test_f2_protocol.py`: every identity-bearing field is pinned through `to_dict()` -> `from_dict()`; the canonical serialization is stable; the regrade verdict is unchanged by reconstruction; a control arm round-trips without a lesson block and a treatment arm keeps one; a truncated bundle FAILS CLOSED rather than reconstructing with fabricated defaults; and a round trip cannot launder a wrong `protocol_id` past the verifier.
2. Record the authoritative assembly point in this entry: the governed `F2Bundle` is assembled where the evaluation outcome and evaluator authorization are available - the evaluation/admission stage - and NOT inside the arm worker. The worker's job is the execution receipt (delivery identity, delivered bytes, execution evidence, and the workspace-mutation evidence of F2-IMPL-AUTH-006/-007). This entry corrects the readiness-plan wording; it does not change any code path.

**File boundary.** Tests only: `tests/test_f2_protocol.py`. No production file is modified by this entry.

**Explicit non-authorization.** This entry does not authorize any production change, any change to `assemble_f2_bundle` or its callers, any change to the `F2Result` schema, `_reconstruct_endpoint`, `f2_admission.py`, `qwen_train/arm_workspace.py`, the delivery seam, or any frozen artifact; no F2 execution; no real F2 evidence generation; and no provisioning of `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q10, Q11 or Q12.

**Implementation status.** This entry authorizes the tests it names; the design clarification in item 2 is recorded here and requires no code change.

### F2-IMPL-AUTH-009 - Step 8: workspace-mutation classification and the operator execution contracts

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Records no new science and modifies no frozen element.

**Issue.** F2-IMPL-AUTH-006/-007 record the workspace mutation as raw observation, but nothing turns it into a reasoned verdict, so any consumer would have to re-derive one ad hoc. Separately, every remaining external dependency (network, sandbox, population, secrets) is described in prose rather than as a machine-checkable contract, which is why it keeps needing re-litigation.

**Decision 1 - engineering classification (implemented).** `qwen_train/f2_arm_worker.py` gains `_classify_workspace_mutation(evidence, *, relevant_file_set, authorized_paths)`, and the receipt carries `workspace_mutation["classification"]`. It maps OBSERVED ACTIVITY to one of `allowed` / `expected` / `suspicious` / `prohibited` / `unknown` and to an integrity assessment `clean` / `suspect` / `compromised` / `unknown`. A prohibited signal dominates, because a clean tree can otherwise be manufactured by committing. This is an ENGINEERING assessment ONLY: it changes no endpoint, no outcome, no statistics, and it is NOT an exclusion rule. Whether a classification excludes an observation is a SCIENTIFIC decision reserved to the admission authority and to Q1-Q6 (see Decision 2).

**Decision 2 - the exclusion rule is NOT set here (REQUIRES AUTHORIZATION).** Mapping `compromised` to "excluded from the primary analysis" is a pre-registered analysis rule. It is deliberately NOT implemented in code, because defining it after seeing data would be a researcher degree of freedom. It is recorded here as the exact authorization-ready rule: "an observation whose `workspace_mutation.classification.integrity` is `compromised` is INVALID and is excluded from the primary T/X analysis as missing data; `suspect` and `unknown` are recorded and reported but do not by themselves exclude."

**Decision 3 - operator execution contracts (recorded).** The remaining external dependencies are specified below as machine-checkable contracts so the operator can satisfy them without another design cycle.

**File boundary.** One production file: `qwen_train/f2_arm_worker.py` (plus its tests in `tests/test_f2_op_infra_004.py`, already named in F2-IMPL-AUTH-004). No other file.

**Explicit non-authorization.** No exclusion or invalidation rule in code; no change to the endpoint, outcome, `F2Result` schema, `_reconstruct_endpoint`, the delivery seam, `f2_admission.py`, or any frozen artifact; no F2 execution; no real F2 evidence generation; no provisioning of `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q10, Q11 or Q12.

**Operator execution contracts.**

NETWORK (Q9). Windows-native, because the host is Windows 11. Preferred: run the model backend INSIDE the sandbox and set `<Networking>Disable</Networking>` in the `.wsb`; the F2 arm reaches the model over loopback inside the sandbox, and no external egress exists. Alternative: AppContainer isolation with loopback-only capability, or a host firewall rule that permits only `127.0.0.1:<model-port>` and denies all other egress. Note that Windows Sandbox enables networking BY DEFAULT and that total disable breaks a loopback model call, so the allowlist or the model-inside-sandbox design is required. ACCEPTANCE: from inside the arm, `curl https://example.com` MUST fail; a raw socket to `1.1.1.1:53` MUST fail; a raw UDP packet MUST fail; an IPv6 egress attempt MUST fail; a proxy env var MUST NOT create a path; loopback to the model port MUST succeed; the host-side control MUST succeed.

SANDBOX. Windows Sandbox (hypervisor-backed) with `<ProtectedClient>Enable</ProtectedClient>`. ACCEPTANCE: fresh instance per arm; no persistence between arms; only the task workspace is mapped; the evaluator, the reference truth and other arms are NOT mapped and are unreachable; `.git` future history is absent. NOTE: a mapped folder with write permission PERSISTS after disposal, so the host-side result must be re-verified after the sandbox closes.

> **RECONCILIATION NOTE (2026-10-07, F2-IMPL-AUTH-025).** The NETWORK/SANDBOX Q9
> contract immediately above assumes the model runs INSIDE Windows Sandbox and is
> reached over loopback from the arm. A completed isolation-boundary investigation
> and authoritative platform research found that assumption in tension with this
> host's real architecture (local Intel iGPU/Vulkan model; Windows Sandbox vGPU is
> graphics-oriented, not a supported generic Vulkan compute passthrough). This
> contract is therefore **PROVISIONAL / UNDER RECONSIDERATION**; its original text
> is preserved verbatim above as history. See the `F2-IMPL-AUTH-025` entry at the
> end of this document. **No F2 execution may use a revised topology until that
> topology is formally authorized.**

POPULATION (Q1). Source MUST be contamination-resistant: SWE-rebench or SWE-bench-Live, NOT vanilla SWE-bench (measured 32.67 percent direct solution leakage, 31.08 percent inadequate tests). ACCEPTANCE: frozen digest; every instance created after the model training cutoff; held-out provenance recorded; contamination screen passed; endpoint derived by `reference_modified_file_set_v1`; reference fix unique (the current fail-closed refusal of ambiguous references is preserved); relevant_file_set frozen; minimum N 19, target 85.

SECRETS (Q7, Q11). Owner: the operator. Location: the operator's `.env` only. Scope: never in the arm workspace, never in task files, never in logs, never in the bundle payload, never in a model prompt. `SWARM_DISTILLER_MODEL` and `SWARM_DISTILLER_WEIGHTS_DIGEST` are read by `lesson_distiller.default_local_identity` (fail-closed, already verified). `SWARM_RECEIPT_KEY` is read by `prompt_repairer._receipt_key` (env-only, fail-closed, already verified). ACCEPTANCE: missing key fails closed; wrong key fails; tampered receipt fails; a valid trusted receipt succeeds.

REFERENCE TRUTH (sequestering). Stored outside every workspace, evaluator-only, digest-pinned, unavailable during agent execution.

**Implementation status.** Decision 1 is implemented by this entry. Decisions 2 and 3 are recorded here and require the operator or Q1-Q6 authorization.

### F2-IMPL-AUTH-010 - Step 9: calibration execution layer (runner + authorization gate)

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Records no new science and modifies no frozen element.

**Issue.** `qwen_train/f2_calibration.py` held the calibration SCHEMA and ANALYSIS (`CalibrationRecord`, `CalibrationSummary`, `summarize_calibration`, `INFRASTRUCTURE_RERUN_CAUSES`, 27 tests) but no RUNNER. The plan justified the absence by "executing it creates Experiment J observations". That is a reason not to RUN calibration; it is not a reason not to BUILD it. Conflating the two left the cheapest, highest-value measurement in the design unimplemented and un-testable, while the scientific blocker (Q10) is authorization, not code.

**Decision 1 - the runner (implemented).** `f2_calibration.py` gains `CalibrationAuthorization`, `ObservationOutcome`, `CalibrationPlan`, `run_calibration(plan, *, authorization, runner, max_reruns)` and `CalibrationAuthorizationError`. `runner(instance_id, arm, replicate) -> ObservationOutcome` is INJECTED, so the whole layer is testable with a fake and no model, backend, service or observation is required.

**Decision 2 - execution is gated on Q10 (fail-closed).** `run_calibration` refuses to start unless a `CalibrationAuthorization` is supplied AND the plan matches it EXACTLY on all six scientifically material fields: **arm, exact task set, replicates, horizon, censoring convention, and endpoint specification hash**. The authorization object mirrors the plan's own Q10 wording - "run count, task set, replicates per task, and the censoring convention" - and additionally pins the endpoint hash that Q4 freezes, so an authorization cannot be silently widened by passing different tasks, a different replicate count, or a different measurement definition. No authorization, no run.

**Correction (contract-integrity fix, same entry).** As first implemented, `CalibrationPlan` carried no `censoring_convention` and `_check_authorization` never compared one, so the "matches it EXACTLY" claim above was FALSE for the single field Q10 names explicitly; the endpoint hash was likewise only checked non-empty. `CalibrationPlan` now carries `censoring_convention`, `CalibrationAuthorization` now carries `endpoint_hash`, and `_check_authorization` compares both and fails closed on a missing or mismatched value. The claim is now true as written.

**Decision 3 - rerun policy (implemented, non-gameable).** Only a cause in the existing closed `INFRASTRUCTURE_RERUN_CAUSES` set may be retried, at most `max_reruns` times. Every attempt is RETAINED: a superseded attempt is a `valid=False` record carrying `rerun_of` and `rerun_cause`. An undesired SCIENTIFIC outcome is never rerun, and a rerun never deletes the original, so outcome-dependent rerunning is structurally impossible. An unrecognized infra cause is refused rather than treated as flaky.

**File boundary.** One production file: `qwen_train/f2_calibration.py`. Tests: `tests/test_f2_calibration.py` (existing module). No other file.

**Explicit non-authorization.** This entry does NOT authorize EXECUTING calibration (that is Q10), nor any change to the endpoint, `alpha`, `power`, `delta`, the statistical test, the population rule, the exclusion rule, the `F2Result` schema, `_reconstruct_endpoint`, the delivery seam, or any frozen artifact; nor F2 execution, real evidence generation, or provisioning of `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q11 or Q12.

**Implementation status.** Implemented by this entry: the runner and its 15 tests. Execution remains gated on Q10 and on an operator-supplied `CalibrationAuthorization`.

### F2-IMPL-AUTH-011 - Step 10: calibration production adapter (membrane -> runner)

**Author:** Rob (human operator)

**Date:** 2026-10-04

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-04, entered by the release agent. Records no new science and modifies no frozen element.

**Issue.** `run_calibration` takes an INJECTED per-observation runner, but nothing connected that runner to the F2 execution membrane. The orchestration layer was therefore complete and testable yet not executable: the last link between "a Q10 authorization exists" and "one no-lesson observation is actually measured" was missing.

**Decision.** `qwen_train/f2_calibration.py` gains three functions.

1. `tool_calls_from_trajectory(records)` - PURE. Flattens ATIF step records into the tool-call shape the endpoint detector reads. The trajectory stores `step_id` at the RECORD level while its tool calls carry only `extra.turn`; the detector reads `extra.step_id` and falls back to `extra.turn`. The adapter injects the record-level `step_id` so the horizon test uses the authoritative ATIF step id rather than a counter that merely happens to coincide. Non-step records are ignored and malformed entries do not raise.
2. `endpoint_from_trajectory(records, spec)` - PURE. Delegates to the frozen `f2_endpoint.qualifying_first_edit`, so calibration measures the SAME F0 section 5 detector as the confirmatory run. No qualifying edit inside the horizon is a CENSORED observation, not a failure and not a missing datum.
3. `make_calibration_runner(...)` - the membrane adapter. Runs ONE no-lesson arm through the existing orchestrator and returns an `ObservationOutcome`: a scientific outcome (endpoint observed / censored), or a predefined INFRASTRUCTURE failure - the only class the caller may rerun.

**Authority boundary.** The adapter grants itself NOTHING. It does not create a credential, does not read `SWARM_RECEIPT_KEY`, does not bypass the authorization gate (which lives in `run_calibration`), does not alter the endpoint definition, and does not touch any statistical parameter. It can only run arms the caller already asked for, through the same membrane as confirmatory F2.

**What this does NOT establish.** The adapter proves the WIRING. It does NOT establish the host/network/sandbox membrane - that is the operator's Q9 control - and it does not prove production execution on its own. Exercising it requires Q10 authorization and the operator's model/backend services.

**File boundary.** One production file: `qwen_train/f2_calibration.py` (plus its tests in `tests/test_f2_calibration.py`). No other file.

**Explicit non-authorization.** This entry does NOT authorize EXECUTING calibration (Q10), nor any change to the endpoint, `alpha`, `power`, `delta`, the statistical test, the population rule, the exclusion rule, the `F2Result` schema, `_reconstruct_endpoint`, the delivery seam, or any frozen artifact; nor F2 execution, real evidence generation, or provisioning of `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q11 or Q12.

**Implementation status.** Implemented by this entry: the adapter and its 10 tests. Execution remains gated on Q10.

### F2-IMPL-AUTH-012 - Step 11: contamination-provenance screen (S10)

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-05, entered by the release agent. Records no new science and modifies no frozen element.

**Issue.** `qwen_train/f2_population.py` screens S1-S9 but has NO contamination-provenance screen, and it explicitly discloses at `:34-37` that training-set contamination of the gold patch is *"real and unclosable by any engineering control"* (citing arXiv 2512.10218 and OpenAI's 2026 SWE-bench Verified audit). That disclosure is correct and is preserved. What was missing is the standard PROXY control the refreshed-benchmark literature uses (SWE-bench-Live, SWE-rebench): a temporal cutoff. A task whose issue/PR predates the model's knowledge cutoff is far more likely to have its solution memorised.

**Decision.** Add screen **S10_contamination_provenance**:
- no `model_cutoff` declared -> state `NOT_DECLARED`, the screen PASSES, and the existing disclosed-limitation behaviour is unchanged (so today's pool is unaffected);
- `model_cutoff` declared but the task declares no `created_at` -> **FAIL CLOSED** (`NO_DATE`);
- `created_at` precedes `model_cutoff` -> **FAIL CLOSED** (`PRE_CUTOFF`);
- `created_at` at or after `model_cutoff` -> `POST_CUTOFF`, PASSES.

`PopulationEntry` gains `created_at` and `contamination_state`; `screen_entry` gains `created_at` and `model_cutoff`; `screen_pool_rows` gains `model_cutoff` and reads `created_at` from each row.

**This is a PROXY, not proof.** It does not establish that a post-cutoff task is uncontaminated, and the disclosure at `:34-37` continues to say so. No cutoff value, no model identity and no statistical parameter is selected by this entry.

**File boundary.** One production file: `qwen_train/f2_population.py` (plus its tests in `tests/test_f2_population.py`). No other file.

**Explicit non-authorization.** This entry does not authorize selecting a model cutoff, a model identity, a task population, or any of delta / alpha / power / sidedness / the statistical test / the pi_d planning value / final n; nor any change to F0, the endpoint, `_reconstruct_endpoint`, the `F2Result` schema, the delivery seam, or any frozen artifact; nor F2 or calibration execution; nor provisioning of `SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`, Q9, Q10, Q11 or Q12.

**Implementation status.** Implemented by this entry, with 7 tests (RED->GREEN proven).

### F2-IMPL-AUTH-013 - Statistical design authorization (operator-granted)

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-05, which granted the scientific parameters below. This entry RECORDS operator-authorized decisions; the release agent selected none of them.

**F1 freeze.** F1 is FROZEN. `docs/F1_FINAL_RECONCILIATION.md` is the authoritative record: 20/20 official observations, 10 VALID endpoint observations (all at ATIF Step 4), 10 infrastructure-invalid (contributing the horizon value 12). `P95 = 12`; **`k = 12`** frozen (`k = min(12, max(8, 12))`). No F1 rerun. The stale "pilot NOT RUN" sentence at `EXPERIMENT_J_F1_AUTHORIZATION.md:152` is corrected.

**Primary comparison.** T vs X, paired by task x rollout seed; C0 remains an independent reference arm and is never substituted for X. Endpoint = the first ATIF decision step with `function=filesystem`, `operation in {write, patch, edit, create}`, target path in the frozen `relevant_file_set`. The endpoint is an ATTEMPTED qualifying edit; it does not require tool acceptance, mutation, or test passage.

**Authorized statistical parameters.**

| Parameter | Authorized value |
|---|---|
| Primary test | exact two-sided McNemar |
| alpha | 0.05 |
| target power | 0.90 |
| practical effect | delta = 0.20 (absolute paired marginal difference) |
| sidedness | two-sided |
| CI | 95% for the paired marginal difference, discordant cells reported |
| multiplicity | none (single preregistered primary endpoint) |
| pi_d planning value | 0.50 (a conservative planning nuisance value, NOT an empirical estimate) |
| n | 300 analyzable paired task x seed units |

**Why pi_d is a planning value, not an estimate.** `pi_d = P(T xor X) = p10 + p01` requires BOTH arms. The no-lesson pilot has no T arm, so it cannot identify `pi_d`; deriving it from marginal `p_T`/`p_X` assumes independence and is rejected.

**Exact sample-size calculation (authorized rule: n = max(300, smallest n with power >= 0.90)).** Using the repository's own `qwen_train.f2_statistics.required_pairs` (exact conditional McNemar with the doubling two-sided correction): `required_pairs(delta=0.20, discordance=0.50, alpha=0.05, power=0.90, sided="two-sided") = **116**`. Therefore **n = max(300, 116) = 300**.

**Sensitivity table (planning transparency only; the frozen planning value is NOT changed by it).** At delta=0.20, alpha=0.05, power=0.90, two-sided:

| pi_d | required n | n = max(300, required) |
|---|---|---|
| 0.10 | not attainable (delta > pi_d) | - |
| 0.20 | 28 | 300 |
| 0.30 | 66 | 300 |
| 0.50 | 116 | 300 |
| 0.75 | 191 | 300 |
| 1.00 | 261 | 300 |

**Consequence: n = 300 exceeds the requirement at every point of the authorized sensitivity range** (the worst case, pi_d = 1.00, needs 261).

**File boundary.** Documentation only: `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` (the one factual correction) and this document. No code and no test is changed by this entry.

**Explicit non-authorization.** This entry does not authorize modifying F0, changing `k`, changing the endpoint, changing T/X/C0 definitions, rerunning F1, acquiring the task population, recovering gold patches, provisioning any credential, establishing host security, or executing confirmatory F2. Those remain BLOCKED or REQUIRES EXTERNAL PROVISIONING as recorded elsewhere.

**Implementation status.** Recorded. It implements no code.

### F2-IMPL-AUTH-014 - Step 12: explicit contamination classification vocabulary

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-05, entered by the release agent. Records no new science and modifies no frozen element.

**Issue.** S10 computes `contamination_state` (`NOT_DECLARED` / `NO_DATE` / `PRE_CUTOFF` / `POST_CUTOFF`) but exposes no explicit classification. The governing mission requires each task to be classified as **CLEAN / POTENTIALLY CONTAMINATED / UNKNOWN**, and requires that **UNKNOWN never silently becomes CLEAN**.

**Decision.** `qwen_train/f2_population.py` gains the constants `CONTAMINATION_CLEAN`, `CONTAMINATION_POTENTIALLY`, `CONTAMINATION_UNKNOWN`, the mapping `_CONTAMINATION_CLASS`, and the derived property `PopulationEntry.contamination_class`:

- `POST_CUTOFF` -> **CLEAN**
- `PRE_CUTOFF` -> **POTENTIALLY CONTAMINATED**
- `NO_DATE`, `NOT_DECLARED` -> **UNKNOWN** (never CLEAN)
- any unrecognised state -> **UNKNOWN** (fail-safe default)

This is a DERIVED view: it changes no screening decision, no admission, no manifest field. The temporal cutoff remains a **PROXY**, not proof of zero contamination.

**File boundary.** One production file: `qwen_train/f2_population.py` (plus its tests in `tests/test_f2_population.py`). No other file.

**Explicit non-authorization.** No change to F0, the endpoint, `k`, T/X/C0, S8 admission, the exclusion rule, or any statistical parameter; no cutoff value selected; no task admitted; no gold patch recovered; no credential provisioned; no security evidence; no confirmatory F2.

**Implementation status.** Implemented, with 4 tests (RED->GREEN proven).

### F2-CLARIFICATION-004 - Freeze the exact CI construction and the outcome-independence invariant

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-05, entered by the release agent. This is a CLARIFICATION: it records, as authoritative, constructions the readiness plan ALREADY specifies. It adds no new science, selects no new parameter, and modifies no frozen element.

**Issue.** Two analysis constructions were specified in the plan but not yet recorded as authoritative, leaving a theoretical opening for post-hoc selection after outcomes are seen: (a) the exact confidence-interval construction, and (b) the invariant that admission, exclusion, contamination classification and missingness handling cannot depend on which arm wins.

**Decision 1 - the confidence interval is frozen.** The 95% interval for the paired marginal difference is the **Clopper-Pearson exact conditional interval for the discordant proportion, mapped to the risk difference** (readiness plan section 2.3, `:125-129`). Where there are **no** discordant pairs the interval deliberately does **NOT** collapse to `[0, 0]`; it uses the conservative worst-case paired half-width. **Bootstrap is NOT used** - evalstats (arXiv 2609.35815) finds no bootstrap variant reaches nominal coverage on paired binary data even at N = 100. This construction is fixed BEFORE any confirmatory outcome and may not be changed after seeing results. The analysis reports: `b` and `c` (the discordant cells), the observed paired marginal difference, the exact two-sided McNemar p-value, and this frozen 95% interval.

**Decision 2 - the outcome-independence invariant is frozen.** Task admission, exclusion, contamination classification and missingness handling **MUST NOT depend on whether T or X wins.** The plan already requires this (section 2.5, `:188-196`): **fixed N**, no interim analysis, no peeking-based sample-size change, and re-runs **"for infrastructure causes only, never for outcome"**. The population is frozen before confirmatory collection; no task may be added, removed or replaced after an outcome is observed; and UNKNOWN contamination must never silently become CLEAN (F2-IMPL-AUTH-014).

**Boundary recorded, NOT crossed.** Two exclusion parameters remain **proposed and unauthorised**, exactly as the plan states: the **block-level exclusion rule** (invalid arm invalidates the whole pair) and the **infrastructure-failure budget** (proposed <= 30%, with stop-and-diagnose) are marked *"Requires authorization (section 6 Q5)"* and *"(section 6 Q6)"*. This clarification does **NOT** authorise them; they remain an operator decision.

**File boundary.** This document only. No code, no test, no F0 change.

**Explicit non-authorization.** No change to F0, the endpoint, `k`, T/X/C0, S8 admission, the statistical test, alpha, power, delta, pi_d, or n; no authorisation of Q5 or Q6; no task admitted; no gold patch read; no credential provisioned; no security evidence; no confirmatory F2.

**Implementation status.** Recorded. It implements nothing.

### F2-IMPL-AUTH-015 - Repair the Clopper-Pearson bisection

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit instruction of 2026-10-05, entered by the release agent. Correctness fix only. It records no new science and changes no scientific parameter.

**Defect (PROVEN).** The readiness plan section 2.3 specifies a *"Clopper-Pearson exact conditional interval"*. `qwen_train/f2_statistics.py` implemented it through `_binom_cdf_bisect`, which bisected over the interval `[0, n]` (a COUNT range) instead of `[0, 1]` (a PROBABILITY range), derived a count `k = floor(mid)` from the probability midpoint, and then evaluated `binom_cdf(k, n, x)` mixing the count `k` with the probability `x`. Consequence: the solver returned impossible probability bounds - `_clopper_pearson_upper(6, 20, 0.025)` returned **2.0**, and `_clopper_pearson_upper(10, 20, 0.025)` returned **6.0** - so any interval built from it was invalid.

**Fix.** `_binom_cdf_bisect(k, n, target, *, upper)` now solves for the probability `p` over `[0, 1]`:
- `upper=True`  solves `P(X >= k | n, p) = target` (the CP LOWER bound);
- `upper=False` solves `P(X <= k | n, p) = target` (the CP UPPER bound).
Both tails are monotone in `p`, so bisection is valid. The callers now pass the COUNT `k` (not `k / n`). The `k = 0` and `k = n` edges remain clamped by the callers.

**Verification.** The repaired bounds match R's `binom.test(k, n)$conf.int` at 95%: `(3,6) -> (0.1181, 0.8819)`; `(6,6) -> (0.5407, 1.0000)`; `(6,20) -> (0.1189, 0.5428)`; `(10,20) -> (0.2719, 0.7281)`. Bounds are valid probabilities and ordered for every `1 <= k < n` at `n` in {6, 20, 300}.

**Scope note (reported, NOT changed).** `mcnemar_exact` computes its interval from `_wald_halfwidth`, while the module's own metadata string describes it as *"Clopper-Pearson conditional interval on the paired risk difference"*. That naming mismatch is **NOT** repaired here, because choosing which construction the primary result must report is a **scientific** decision, not a correctness fix. It is recorded as an open item.

**File boundary.** `qwen_train/f2_statistics.py` (plus its tests in `tests/test_f2_statistics.py`). No other file.

**Explicit non-authorization.** No change to F0, the endpoint, `k`, T/X/C0, alpha, power, delta, pi_d, n, the statistical test, or the frozen CI construction; no task admitted; no gold patch read; no credential provisioned; no confirmatory F2.

**Implementation status.** Implemented, with 3 tests added (bounds match the standard interval; bounds are valid probabilities and ordered; edges clamped).

### F2-IMPL-AUTH-016 - ONE authoritative confirmatory confidence interval

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded on the operator's explicit instruction of 2026-10-05 (F2 completion brief), entered by the agent. Implements Decision 1 of `F2-CLARIFICATION-004` and removes the ambiguity that AUTH-015 reported but deliberately did not repair. Records no new science and changes no scientific parameter.

**Issue.** `F2-CLARIFICATION-004` froze the confirmatory interval as the Clopper-Pearson exact conditional interval on the discordant direction, transformed to the paired risk difference. That construction lives in `paired_risk_difference`; `mcnemar_exact` reported a Wald interval. Two different "F2 confidence intervals" were therefore obtainable from the same module, and an analysis could report the wrong one. A worked case shows they disagree on significance: `b=5, c=0, N=10` gives the frozen CP interval `(-0.0218, 0.5000)` (contains 0) versus the Wald interval `(0.1901, 0.8099)` (excludes 0).

**Decision (option 1 - one authoritative interval).** Both entry points now share the private helper `_paired_rd_and_interval(b, c, both, neither, confidence=0.95)`. The authoritative interval is, with `d = b + c` and `p = b / d`:

* point estimate `RD = (b - c) / N` (N = total pairs);
* `RD_L = d * (2*p_L - 1) / N`, `RD_U = d * (2*p_U - 1) / N`, where `[p_L, p_U]` is the two-sided 95% Clopper-Pearson exact interval for `p`;
* `d = 0`: the authorized conservative worst-case paired half-width (never `[0, 0]`).

The Wald interval is removed from the confirmatory path (it was never the frozen construction). `mcnemar_exact.ci_low`/`ci_high` and `paired_risk_difference` now return the SAME values by construction.

**Verification.** `test_f2_statistics.py::TestAuthoritativeConfidenceInterval`: the two entry points agree exactly across seven fixtures; the transformed interval matches R's `binom.test(k, n)$conf.int` for `(3,6)`, `(6,20)`, `(10,20)`, `(6,6)` (tolerance 1e-3); the no-discordance interval is conservative, not degenerate; and a case is pinned where the CP interval includes 0 while the Wald interval does not.

**File boundary.** `qwen_train/f2_statistics.py` (plus its tests in `tests/test_f2_statistics.py`). No other file.

**Explicit non-authorization.** No change to F0, the endpoint, `k`, T/X/C0, alpha, power, delta, pi_d, n, the statistical test, or the frozen interval construction; no task admitted; no gold read; no credential; no confirmatory F2.

**Implementation status.** Implemented, 4 tests added (29 in the file).

### F2-IMPL-AUTH-017 - Live worker -> governed bundle assembly

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded on the operator's explicit instruction of 2026-10-05 (F2 completion brief), entered by the agent. Implements the bounded wiring required for `worker -> assemble_f2_bundle -> evaluator -> regrade_f2_pair`. Records no new science and changes no scientific parameter.

**Issue.** `assemble_f2_bundle` was exercised only by tests; the real `f2_arm_worker.py` path never emitted an `F2Bundle`, so the live `worker -> bundle -> evaluator -> regrade` path was `NOT ESTABLISHED`.

**Decision.** Add an OPT-IN (`SWARM_F2_EMIT_BUNDLE=1`), fail-closed bundle-emission path to `f2_arm_worker.py`:

* the worker projects the arm's raw trajectory (`record_type="step"` tool calls) into the protocol's flat behavioral-record shape;
* it derives the PRODUCER DECLARATIONS from raw evidence (endpoint via the frozen detector `f2_endpoint.qualifying_first_edit` through `f2_calibration.endpoint_from_trajectory`; task outcome from the evaluator's retained report) — the producer is never authoritative, and `regrade_f2` independently re-derives and rejects disagreement;
* it consumes operator-provided governance inputs only: `SWARM_F2_ARTIFACT_ROOT`, `SWARM_F2_ARTIFACT_RETENTION_DAYS`, `SWARM_F2_EVALUATOR_ID`, `SWARM_F2_EVALUATOR_VERSION`, `SWARM_F2_EVALUATOR_IMPL`, `SWARM_F2_EVALUATOR_PROCEDURE`, `SWARM_F2_TASK_OUTCOME_REPORT`;
* the evaluator implementation digest is `SHA-256` of the actual supplied implementation artifact (never a declared string); the `EvaluatorAuthorization` binds to those bytes;
* missing inputs, an absent trajectory, or a regrade disagreement fail closed, naming the missing input;
* the assembled bundle is persisted and independently regraded (`regrade_f2`) before the receipt records `regrade_state`.

No evaluator identity, implementation digest, trusted store, or task-outcome report is fabricated; the production values remain operator-provisioned.

**Verification.** `tests/test_f2_worker_bundle_wiring.py` (13 tests): the full chain `worker evidence -> assemble_worker_bundle -> assemble_f2_bundle -> evaluator authorization -> regrade_f2` returns VERIFIED over a real `TrustedArtifactStore` and real `EvaluatorRegistry`; the endpoint is reconstructed from the trajectory; a declaration disagreement is rejected; and each missing governance input fails closed. `emit_worker_bundle` is exercised end-to-end (config -> trajectory -> assemble -> regrade -> persist).

**File boundary.** `qwen_train/f2_arm_worker.py` (plus `tests/test_f2_worker_bundle_wiring.py`). No other file.

**Explicit non-authorization.** Does NOT name a production evaluator, provision a trusted store, run the task-outcome evaluator, generate real evidence, or execute confirmatory F2. The live path against a real model/backend remains `NOT ESTABLISHED`: it requires the operator's services, the evaluator authorization, the trusted store, and Q7/Q9/Q11.

**Implementation status.** Implemented, 13 tests. Live end-to-end execution remains `NOT ESTABLISHED` (external prerequisites absent).

### F2-IMPL-AUTH-018 - Q5, Q6, and the missingness rule (operator-granted)

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Records the operator-authorized methodological decisions made in the F2 completion brief of 2026-10-05. Documentation only; it selects no statistical parameter. The outcome-independence invariants it states are ALREADY enforced in code, so this entry implements nothing.

**Q5 - block-level exclusion (authoritative).** A task/block may be excluded only for a pre-specified, outcome-independent reason established before the paired outcome is observed: missing required provenance; failed base/gold verification; infrastructure invalidity under the predeclared rule; contamination classification; malformed task; reproducibility failure; missing required artifact. Excluding a block because T won, X won, or the result is inconvenient is FORBIDDEN. A single invalid arm invalidates the whole pair (McNemar requires complete pairs). Enforced by construction: `f2_admission.admit_f2_task` requires both T and X bundles and fails closed otherwise.

**Q6 - infrastructure-failure budget (operational gate, NOT a statistical property).** The 30% figure is an operational feasibility ceiling, not a property of McNemar. Predeclare: if infrastructure-invalid candidates exceed 30% of the attempted candidate set, STOP acquisition/execution and investigate the environment; the ceiling never justifies selectively discarding observations; all infrastructure failures remain in the ledger; the denominator is never reset. Enforced by construction: `f2_calibration` reruns only named `INFRASTRUCTURE_RERUN_CAUSES`, retains every attempt, and never reruns a scientific outcome.

**Missingness rule.** A missing outcome is MISSING — not a success, not a failure, not silently excluded, not converted into the opposite arm's result, and never a trigger for an outcome-dependent replacement. Incomplete pairs stay out of the primary paired McNemar analysis but remain fully reported in the CONSORT-style accounting. Enforced by construction: `regrade_f2` fails closed on a missing event stream rather than inferring a censored run.

**Contamination vocabulary.** CLEAN / POTENTIALLY CONTAMINATED / UNKNOWN, with `UNKNOWN != CLEAN` and `POTENTIALLY CONTAMINATED != CLEAN`. Temporal cutoff is necessary evidence but not proof of non-contamination. The S10 `contamination_class` is a SCREENING PROXY under the declared-cutoff assumption and must be reported as such, never as proof. This entry does not change the AUTH-014 mapping; it bounds how the label may be interpreted.

**File boundary.** This document only. No code, no test, no F0 change.

**Explicit non-authorization.** No change to F0, the endpoint, `k`, T/X/C0, alpha, power, delta, pi_d, n, the statistical test, the CI, S8 admission, or the contamination state machine; no task admitted; no gold read; no credential; no security evidence; no confirmatory F2.

**Implementation status.** Recorded. It implements nothing.

### F2-IMPL-AUTH-019 - Confirmatory analysis link (ledger -> final result)

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded on the operator's explicit instruction of 2026-10-05 (F2 completion brief, section 3), entered by the agent. Completes the required chain `... -> paired outcome -> receipt -> statistical ledger -> final result`. Records no new science and changes no scientific parameter.

**Issue.** No production code consumed the frozen confirmatory statistics: `mcnemar_exact` / `paired_risk_difference` / `required_pairs` had no non-test caller, so the `statistical ledger -> final result` link of the required chain did not exist.

**Decision.** Add `qwen_train/f2_analysis.py` (pure, offline):

* `finalize_f2(observations, ...)` builds the 2x2 table from the paired ledger and reports the frozen result by delegating to the shared `f2_statistics` implementation (so it cannot drift from the frozen method): `N`, `n00`, `n01`, `n10`, `n11`, `b`, `c`, `d`, `RD`, exact two-sided McNemar p, the authoritative Clopper-Pearson-transformed paired-RD 95% CI, direction, and the missing-by-reason ledger.
* `independent_reconstruction(observations, ...)` re-derives the SAME numbers from the raw booleans as a SECOND implementation that does not import or call `f2_statistics`, so a defect in one is caught by the other (F2 section 21).
* Missingness: a pair with either arm missing is not success, not failure, not silently dropped; it is excluded from the primary paired analysis and reported.

**Verification.** `tests/test_f2_analysis.py` (10 tests): conventions and `to_dict` fields; field-for-field agreement with the authoritative `mcnemar_exact`; the R-transformed oracle; missingness accounting; and, decisively, `independent_reconstruction` still returns correct values when `f2_statistics.mcnemar_exact` is patched to raise, while `finalize_f2` then fails - proving the two paths are genuinely independent.

**File boundary.** `qwen_train/f2_analysis.py` (plus `tests/test_f2_analysis.py`). No other file.

**Explicit non-authorization.** Does NOT execute the experiment, contact any service, admit any task, read gold, provision credentials, or change the frozen method/CI/alpha/power/delta/pi_d/n. It is inert until a real paired ledger exists.

**Implementation status.** Implemented, 10 tests. It cannot produce a scientific result until the confirmatory event has run (external prerequisites still absent).

### F2-IMPL-AUTH-020 - F2 execution-prerequisite readiness checker

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded on the operator's explicit instruction of 2026-10-05 (F2 readiness brief, Phase 5), entered by the agent. Extends the mandated fail-closed execution gate from the per-task R1-R8 check (`task_readiness.evaluate_readiness`) to the EXPERIMENT-level prerequisites. Records no new science.

**Decision.** Add `qwen_train/f2_readiness.py` -- a PURE, READ-ONLY, FAIL-CLOSED report over the 18 F2 execution prerequisites (distiller model identity, weights digest, evaluator identity, evaluator implementation digest, trusted artifact store, receipt key, protected population >= 300, base/gold artifacts, T/X/C0 manifests, clean-room isolation, Q9 no-egress, Q10/Q12/Q13 authorization, delivery instrumentation, regrade/bundle verification, statistical analysis). For each it reports the evidence required, where it must exist, the failure behavior, and the operator action. A prerequisite passes ONLY when its evidence is explicitly present and well-formed; the checker never reads a secret's value, never infers no-egress from configuration, and never promotes unproven population/memory.

**Verification.** `tests/test_f2_readiness.py` (12 tests): the empty environment yields 15 blockers and 3 satisfied local capabilities; malformed digest / relative store root / missing evaluator impl / population shortfall / config-only no-egress / partial probe / missing authorization all fail closed; and a fully-supplied synthetic fixture turns READY. Live current-revision evaluation: **READY False, 15 blockers**.

**File boundary.** `qwen_train/f2_readiness.py` (plus `tests/test_f2_readiness.py`). No other file.

**Explicit non-authorization.** Does NOT execute F2, supply any evidence, name an evaluator/model, provision a store/receipt, or alter F0/F1/statistics. It reports blockers; it does not remove them.

**Implementation status.** Implemented, 12 tests.

### F2-IMPL-AUTH-021 - The authoritative test-outcome evaluator, and closing the F2 producer gap

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded on the operator's explicit instruction of 2026-10-05 (F2 completion brief: "implement missing F2 infrastructure ... build the actual independent evaluator if it is still missing"), entered by the agent. Records no new science and changes no frozen element.

**The defect this replaces.** The repository contained **three consumers** of a per-node test-status map -- `f2_governance._derive_json_test_report_v1`, `f2_protocol._derive_task_outcome`, and `f2_arm_worker._task_success_from_report` -- and **zero producers**. Nothing in the repository could turn retained execution evidence into that map, so `SWARM_F2_TASK_OUTCOME_REPORT` had to be supplied from outside the codebase and the documented result protocol `json_test_report_v1` described itself as a "REFERENCE deriver: the real F2 evaluator output format is not yet authorized". S8 evidence provenance therefore had no in-repository route to `STATE_VERIFIED`.

**Decision.** Add `qwen_train/f2_evaluator.py`: the authoritative, independent, deterministic producer.

* *Independent of the F2 analysis and verification code.* It imports only the standard library. It does not import `f2_governance`, `f2_protocol`, `f2_analysis` or `f2_statistics`, so a defect in the verifier cannot mask itself through a shared path. This is pinned by a test that reads the module source and fails if any such import appears.
* *Identity is reconstructed, never guessed.* pytest's `junitxml` writer emits `classname=<dotted module + class chain>` and `name=<final segment, parametrisation included>`. `nodeid_to_junit_identity` is the exact inverse. There is no `split("::")` heuristic and no prefix matching anywhere. The convention was **verified empirically against pytest's own writer** before implementation, which also established that a raised `RuntimeError` surfaces as `<failure>`, not `<error>` -- so both are treated as *not passed* and neither can manufacture a pass.
* *Five-field identity.* `f2_pytest_junit_evaluator` / `1.0.0` / `implementation_digest()` / `f2_pytest_junit_identity_v1` / `f2_experiment_j_v1`. The digest is the lowercase SHA-256 of the module's own bytes, so a declared identity cannot be separated from the bytes that produced the verdict.
* *Fail closed.* Malformed XML, an unresolvable declared node id, a duplicate emitted `(classname, name)` pair, a DOCTYPE/ENTITY declaration, an oversized document, or an empty declared set each produce a non-passing outcome with a machine-readable reason. None can yield `pass`.
* *Deterministic bytes.* Canonical JSON, sorted keys, fixed separators, ASCII-escaped, atomic temp-file write. No timestamp, hostname or path. Byte-identical output for identical evidence and contract.
* *Strict prefix safety.* `test_run` vs `test_run_error_handling` and `test_x` vs `test_x[1]` resolve independently, because lookup is exact-equality on the reconstructed identity.
* Registered with `f2_governance.register_result_protocol` as `f2_evaluator_report_v1`, which re-derives the verdict from the **retained report bytes** rather than trusting any producer declaration, and additionally refuses a report whose `implementation_digest`, `instance_id` or `execution_state_identity` disagrees with the bundle.
* Producer seam: `augment_test_command` adds `--junitxml=` to an existing declared test command (idempotent; a command that already specifies one is refused rather than given a second, ambiguous destination), and `produce_report` writes the canonical report atomically. `python -m qwen_train.f2_evaluator` exposes the same logic as an external program (exit 0 pass / 3 fail / 2 evaluator error), verified by direct invocation.
* `f2_arm_worker._produce_outcome_report_from_evidence` closes the loop: when the outcome report is absent but `SWARM_F2_JUNIT_EVIDENCE` and `SWARM_F2_TASK_CONTRACT` are supplied, the arm derives the report from retained evidence instead of failing for want of a producer. Both missing inputs are named in the failure.

**Decision (readiness gate).** Two AUTH-020 items are strengthened rather than extended, so the authorized 18-item contract is unchanged. `evaluator_identity` now additionally requires `SWARM_F2_EVALUATOR_PROCEDURE`, because `EvaluatorAuthorization` is a five-field record whose `protocol_version` is the repository constant `F2_PROTOCOL_ID`, leaving three operator-supplied fields; requiring only two let READY=True coexist with a bundle path that fails closed in `_load_bundle_governance`. `delivery_instrumentation` now requires `SWARM_F2_EMIT_BUNDLE` to be enabled and `SWARM_F2_TASK_OUTCOME_REPORT` to be configured, because capability presence alone reported READY=True for an arm that would emit no evidence chain. The outcome report is required to be *configured, not present*: it is a per-arm product of the run, so demanding the file at readiness time would be a category error.

**Decision (Q6 gate).** The missingness ledger existed but the authorized threshold did not. `f2_analysis` now classifies each `missing_reason` against the **already-authorized** `f2_calibration.INFRASTRUCTURE_RERUN_CAUSES` set -- imported, not re-declared, so the gate and the rerun policy cannot drift -- and reports an `infrastructure_gate` verdict at a maximum infrastructure-failure fraction of 0.30. The gate is an **additional** verdict layered on the frozen statistics: it never alters N, b, c, the p-value or the interval, and `independent_reconstruction` reports the same gate so the two paths must agree. Three properties are pinned by tests: an infrastructure outage is never laundered into a scientific exclusion; an unrecognised reason is classified `unknown` and never counted as scientific, so it cannot shrink the measured rate; and the denominator is the whole observation window, not the complete-pair count, so a large outage cannot hide by also shrinking N.

**Verification.** `tests/test_f2_evaluator.py` (77 tests): identity mapping for plain functions, class methods, nested class chains, parametrisation, string parameters containing spaces, Windows separators, and rejection of undecomposable ids; all five outcomes; strict-prefix isolation in both directions and across classes; multi-suite pooling; malformed/empty/testcase-less/DOCTYPE/oversized/duplicate-identity evidence; non-ASCII and XML-escaped names; byte-level determinism across repeated and reordered runs; the identity record and its independence from the verifier; report round-trip and tamper detection; and the producer/CLI. Where pytest's own writer output is present, a class re-validates the mapping against it. `tests/test_f2_evidence_chain.py` (18 tests) proves the full chain end to end: retained JUnit -> producer -> canonical bytes -> registered protocol re-derivation -> `STATE_VERIFIED`, plus rejection of a lying declaration, missing authorization, a tampered artifact, a removed artifact, and unverifiable evaluator bytes. `tests/test_f2_q6_gate.py` (35 tests). `tests/test_f2_readiness.py` extended to 33.

**Live current-revision results.** `pytest tests/ -k f2` -> **1113 passed, 4 skipped** (baseline before this entry: 962 passed, 4 skipped). `ruff check --select E9,F swarm_os runtime_v2 organism_console` -> **All checks passed**. Readiness re-evaluation: **READY False** (the gate is unsatisfiable while external prerequisites are absent, which is the correct fail-closed posture).

**File boundary.** `qwen_train/f2_evaluator.py` (new), `qwen_train/f2_governance.py`, `qwen_train/f2_analysis.py`, `qwen_train/f2_readiness.py`, `qwen_train/f2_arm_worker.py`, `tests/test_f2_evaluator.py` (new), `tests/test_f2_evidence_chain.py` (new), `tests/test_f2_q6_gate.py` (new), `tests/test_f2_readiness.py`, and this document.

**Explicit non-authorization.** Does NOT execute F2/Q10/Q12/T/X/C0, supply or admit any task, provision a store or receipt key, read gold, choose a distiller model, declare any population, or alter F0/F1/the frozen statistical design. Naming the evaluator identity here authorizes the IMPLEMENTATION and its registration as a result protocol; authorizing its use as *the* F2 evaluator for a specific run remains an operator act at execution time.

**Implementation status.** Implemented. 130 net new tests. The producer gap is closed; the remaining blockers are external (S8 base/gold execution evidence, population admission, Q9 host isolation, receipt key provisioning).

### F2-IMPL-AUTH-022 - Q9 attestation layers and the model conversion-chain record

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded on the operator's explicit instruction of 2026-10-05 (F2 completion brief: "implement every non-privileged piece" around the privileged gate, and "implement the repository-side provenance machinery needed by F2"), entered by the agent. Records no new science and changes no frozen element.

**The defect this replaces (Q9).** `f2_readiness._check_no_egress` consumed seven caller-asserted strings. Nothing in the repository observed a socket, so the strings could not distinguish "egress was denied" from "somebody typed the word denied", and there was no artifact, digest, binding or verification step for the clean-room claim at all.

**Decision.** Add `qwen_train/f2_isolation.py`: the observer and verifier layers, deliberately excluding the privileged control. It **observes and verifies**; it creates no firewall rule, binds no interface, alters no DNS, and requires no Administrator.

* *Real probes with recorded outcomes.* TCP and UDP over both a hostname and a **literal address** (a name that fails to resolve proves nothing about egress), IPv6, loopback, each declared local service, DNS behaviour, and the interface inventory. Every probe records dimension, target, protocol, outcome, detail, local address, observer PID and timestamp.
* *Four outcomes, not two.* `denied` is recorded only when a probe was attempted and refused. Name-resolution failure is recorded `unknown`, never `denied`. A dimension with no global IPv6 is `unavailable` with a reason -- vacuously satisfied, but visibly not proven, and never laundered into `denied`.
* *A negative control is mandatory.* If a probe to a destination that must be reachable when unisolated is not `permitted`, the entire attestation is unproven. Without it, an over-block that also breaks loopback is indistinguishable from correct isolation.
* *Observed versus policy-declared are different epistemic categories.* `proxy` bypass and `alternate_interface` coverage are properties of the ENFORCED policy and are **not** observer-provable unprivileged. Rather than invent them, they are carried in `policy_assertions`, recorded as `source=enforced_policy`, and reported by the verdict in a separate `policy_declared_dimensions` list. An assertion with no identified policy identity and hash is rejected, and an assertion can never override an observed failure.
* *Binding and coverage.* An attestation that cannot name its arm, rollout and workspace does not prove anything about that arm. Declared-but-uncovered required services are reported.
* *Deterministic canonical bytes* and a content digest binding one specific observation.

**Decision (readiness integration).** `_check_no_egress` now accepts, in descending strength, either a full `f2_isolation` attestation -- whose verdict it **re-derives itself**, ignoring the caller's claim entirely -- or the legacy seven-string receipt, which is retained for the AUTH-020 contract but is now labelled `caller-asserted` in its detail. A test proves an attestation **overrides a lying string receipt**: a caller cannot write `"https": "denied"` while the recorded probe shows the connection succeeded.

**The defect this replaces (model provenance).** `experiment_model_identity` proves which GGUF bytes are served and under which alias. The governing document separately requires the LoRA identity as a REQUIRED RECORD, and no code verified any of: base model, adapter, training corpus, conversion, or served artifact as distinct links.

**Decision.** Add `qwen_train/f2_model_provenance.py`, recording the chain **base -> adapter -> corpus -> conversion -> served** as five independent links, each `PROVEN`, `UNRECORDED` or `MISMATCH`.

* *Corpus facts are metadata only.* Existence, byte size and mtime. **No line of any training corpus is ever opened**, and a test asserts the absence of a corpus hash.
* *Timestamps are observations, never proof.* Filesystem mtimes and run timestamps are captured as observations and are explicitly **not** accepted as conversion evidence; a test pins this.
* *Fails closed and names the fix.* Each broken link yields a specific remedy string, e.g. *"record an explicit adapter->merged-artifact->GGUF conversion step; a timestamp ordering is NOT a conversion record"*.
* *Dimensions never collapse.* A served artifact that verifies does **not** make the chain satisfied.

**Live current-revision result against the real `robs4b` artifacts.** `base_model` **PROVEN** (`Qwen3.5-4B` snapshot `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`, from the adapter config); `adapter` **PROVEN** (`robs4b_final_adapter`, config digest, r=16, alpha=32); `training_corpus` **PROVEN** (`robs4b_mixed_final.jsonl`, named by the training-run ledger and present at 1,472,356 bytes, content not read); `served_artifact` **PROVEN** (SHA-256 matches `65202f37...b242d41`); `conversion` **UNRECORDED**. Verdict: **not satisfied**, with the conversion link named. This is the honest state: the served artifact is verifiable and the corpus is identifiable, but **nothing on disk records which adapter produced the served GGUF**.

**Verification.** `tests/test_f2_isolation.py` (32 tests): full dimension coverage, observer PID, literal-IP probing, name-resolution not counted as denial, IPv6 unavailable not counted as denial, reachable egress failing its dimension, unprobed dimensions, missing negative control, undeclared DNS, missing binding, required-service coverage, policy-declared vs observed separation (including rejection of anonymous, wrong-valued, and override attempts), readiness projection mapping, projection failing the real checker, and round-trip/digest/schema tests. No test opens a socket. `tests/test_f2_model_provenance.py` (24 tests): link construction from real on-disk metadata, corpus content never hashed, conversion unrecorded without an explicit record, timestamps never closing the link, every remedy named, mismatch separation, round-trip and schema rejection.

**Live current-revision results.** `pytest tests/ -k f2` -> **1176 passed, 4 skipped** (baseline before AUTH-021: 962 passed, 4 skipped). `ruff check --select E9,F swarm_os runtime_v2 organism_console` -> **All checks passed**.

**File boundary.** `qwen_train/f2_isolation.py` (new), `qwen_train/f2_model_provenance.py` (new), `qwen_train/f2_readiness.py`, `tests/test_f2_isolation.py` (new), `tests/test_f2_model_provenance.py` (new), `tests/test_f2_readiness.py`, and this document.

**Explicit non-authorization.** Does NOT enforce egress denial, touch firewall/routing/DNS/adapters, require or use Administrator, execute F2/Q10/Q12/T/X/C0, admit any task, provision a store or receipt key, read gold, or alter F0/F1/the frozen statistical design. Enforcing Q9 remains a privileged host operation and is still outstanding; this entry supplies only the unprivileged layers around it.

**Implementation status.** Implemented. 64 net new tests. Q9's observation, verification, provenance, gating and fail-closed layers are in place; the privileged enforcement and the conversion record remain outstanding.

### F2-IMPL-AUTH-023 - Execution preflight, integrity gate, report self-integrity, and evidence-based readiness

**Author:** Rob (human operator)

**Date:** 2026-10-05

**Authority.** Recorded on the operator's explicit instruction of 2026-10-05 (F2 completion brief: "make the execution handoff SOTA", "fix every genuine repository-side defect", "anti-reward-hacking hardening"), entered by the agent. Records no new science and changes no frozen element.

#### 1. One-command execution handoff

`python -m qwen_train.f2_preflight` produces a deterministic, machine-readable report (`--json`, `--provenance`, `--attestation PATH`) whose exit code is 0 only on `READY`. It separates six states that must never be collapsed: `IMPLEMENTED` (12 repository capabilities), `READY` (machinery that can consume evidence), `OPERATOR ACTION REQUIRED`, `PRIVILEGED HOST ACTION REQUIRED`, `EXTERNAL EVIDENCE REQUIRED`, `AUTHORIZATION REQUIRED`, and `NOT EXECUTED`. Every blocking finding carries an action string; every finding carries a detail. The receipt key is tested for **presence only** and its value never enters the report. The report states in its own footer that READY is a statement about capability, not about the experiment being executable.

**Live current-revision result: `BLOCKED`, 18 blocking findings.** That is the correct verdict, not a bug.

#### 2. Anti-reward-hacking integrity gate (`qwen_train/f2_integrity.py`)

`f2_arm_worker._classify_workspace_mutation` already observed what an arm touched and its own docstring named the attack it recorded evidence for -- *"nothing else notices a write outside the authorized surface - for example an arm editing the evaluator's own test files to manufacture a pass"* -- but explicitly called itself "Integrity evidence, not a gate". Nothing consumed it. `assess_arm_integrity` turns that observation into a fail-closed admission decision:

* a **declared test file** modified by the arm is REJECT, including when the declaration is given as an absolute or backslash path;
* a **test-shaped path outside the authorized endpoint surface** is REJECT (the conftest/fixture neutering vector);
* **HEAD movement** and **surviving refs** are REJECT, because a commit can hide a dirty tree;
* **git internals** are REJECT; **ignored artifacts** force REVIEW, since they can carry fabricated evidence;
* **`captured: False` is never a pass** -- an arm that can prevent its own audit has already won;
* test-path detection **reuses `f2_endpoint_derivation.is_test_path`**, so the integrity gate and the Q8 endpoint can never disagree about what a test file is;
* the verdict exposes **no statistic**; it decides admission only.

#### 3. Report self-integrity and stale-report defence

A real forgery path was found and closed. `parse_report` re-derived the verdict from the report's own `fail_to_pass` map, so rewriting `declared_result` on an all-passed map produced an **internally consistent** forged report. Reports now carry a `report_digest` -- SHA-256 over the canonical content excluding the digest field -- so any post-hoc edit breaks verification. `f2_arm_worker` additionally refuses a hand-written report outright, and where `SWARM_F2_JUNIT_EVIDENCE` and `SWARM_F2_TASK_CONTRACT` are present it **re-derives the verdict from the retained evidence** and refuses a report that disagrees with it, which closes the stale-report attack.

An existing worker test used a hand-written report fixture; that fixture was **replaced with a genuine one produced by the evaluator** rather than weakening the new check.

#### 4. Producer/verifier drift eliminated

The authoritative evaluator emits `missing` for a declared test absent from the evidence, but three pre-existing consumers (`f2_governance._derive_json_test_report_v1`, `f2_protocol._derive_task_outcome`, `f2_arm_worker._task_success_from_report`) each carried a hard-coded status tuple that **rejected `missing`**. A legitimately produced report could therefore be refused downstream as an unrecognised status -- a protocol error -- instead of scoring as the failure it is. All three now delegate to the single authority `f2_evaluator.verdict_from_report_payload`. Re-implementing that rule in three places is precisely how a producer and its verifier come to disagree.

The stale `"the real F2 evaluator output format is not yet authorized"` claim in the reference deriver's docstring was false once AUTH-021 landed and has been corrected.

#### 5. Readiness: evidence instead of assertion

Three items previously accepted a caller-supplied boolean and now require verification:

* **`q9_no_egress`** -- a seven-string receipt is now REFUSED as caller-asserted. Only a verified `f2_isolation` attestation satisfies the gate, and its verdict is re-derived by the checker itself. An operator can no longer type `"https": "denied"` and be believed.
* **`base_artifacts` / `gold_artifacts`** -- `{"verified": True}` is REFUSED. Real evidence is validated by **`f2_evidence.verify_evidence_record` against the real trusted store** with the **operator-authorized evaluator**, so readiness cannot pass something the independent regrade would reject. `base` must declare `fail`, `gold` must declare `pass`; a record whose retained artifacts are absent reports `ARTIFACT_MISSING` and is refused. The evidence record carries evaluator identity and version but **not** the procedure id, so this check binds identity+version and deliberately claims nothing about the procedure.
* **`clean_room_isolation`** -- `{"isolated": True}` is REFUSED. The arm's workspace-mutation record is verified through the new integrity gate, so an arm that edited the test surface or moved history is refused regardless of any claim.

#### 6. Conversion-chain provenance

`LINK_REMEDY` is now public. A conversion record is accepted only when it names both a merged artifact **and** its digest; timestamps are recorded as observations and explicitly rejected as conversion evidence. Live verdict against the real `robs4b` artifacts is unchanged and honest: `base_model`, `adapter`, `training_corpus` and `served_artifact` **PROVEN**; `conversion` **UNRECORDED**, with the remedy "record an explicit adapter->merged-artifact->GGUF conversion step; a timestamp ordering is NOT a conversion record".

#### 7. Q9 probe CLI

`python -m qwen_train.f2_isolation` emits an attestation from real probes, bound to arm/rollout/workspace, with an atomic write and a required negative control. There is deliberately **no flag that declares a dimension denied**: a policy-declared outcome is possible only via `--policy-assert`, which is recorded with `source=enforced_policy`, reported separately from observations, and rejected unless `--policy-identity` and `--policy-sha256` are supplied.

**Live current-revision result on this host: NOT ESTABLISHED** -- `http`, `https`, `udp`, `loopback` and `required_service` all observed REACHABLE, which is the truthful reading of a host with no egress enforcement. The CLI does not flatter the machine it runs on.

#### 8. Frozen-design guard (`tests/test_f2_frozen_design_guard.py`)

New tests exist to fail loudly if a later change drifts the design: alpha = .05, confidence = .95, Q6 threshold = 0.30, `FROZEN_MIN_PAIRS` = 300, two-sided exact McNemar with no continuity-correction parameter, the b/c/n00/n01/n10/n11 contingency definitions, producer/reconstruction agreement (integers exactly; floats at the repository's established `1e-9` tolerance, the observed divergence being ~1 ULP from different floating-point routes), **Q6 changing no statistic and dropping no pair**, X removing exactly one lesson while preserving survivor bytes and the un-renumbered position gap, single-lesson T failing closed, C0 independently empty and sharing no manifest hash with T or X, the evaluator importing no verifier or analysis module, `independent_reconstruction` not calling `mcnemar_exact`, regrade not re-executing, and `READINESS_ITEMS` still exactly 18.

**Verification.** `pytest tests/ -k f2` -> **1274 passed, 4 skipped** (baseline before this entry: 1176 passed, 4 skipped; 962 before AUTH-021). `ruff check --select E9,F swarm_os runtime_v2 organism_console` -> **All checks passed**. No test opens a socket, reads a training file, reads a secret value, or contacts a network service.

**File boundary.** `qwen_train/f2_integrity.py` (new), `qwen_train/f2_preflight.py` (new), `qwen_train/f2_evaluator.py`, `qwen_train/f2_isolation.py`, `qwen_train/f2_readiness.py`, `qwen_train/f2_arm_worker.py`, `qwen_train/f2_protocol.py`, `qwen_train/f2_governance.py`, `qwen_train/f2_model_provenance.py`, `tests/test_f2_integrity.py` (new), `tests/test_f2_preflight.py` (new), `tests/test_f2_frozen_design_guard.py` (new), `tests/test_f2_evaluator.py`, `tests/test_f2_readiness.py`, `tests/test_f2_worker_bundle_wiring.py`, and this document.

**Explicit non-authorization.** Does NOT execute F2/Q10/Q12/T/X/C0, enforce network egress, touch firewall/routing/DNS, require Administrator, admit a task, fabricate S8 evidence, provision a receipt key, invent a conversion record or an ACTIVE lesson, or alter F0/F1/the frozen statistical design.

**Implementation status.** Implemented. 98 net new tests.

#### Current truth, stated plainly

| State | What it means |
|---|---|
| **IMPLEMENTED** | evaluator + producer + CLI; report self-integrity; evidence-chain verification; integrity/anti-tamper gate; Q6 gate; Q9 observation and independent verification; conversion-chain provenance; T/X/C0 enforcement; readiness gate; preflight |
| **READY BUT REQUIRES OPERATOR ACTION** | the ten `SWARM_*` execution variables; the evaluator implementation path; the trusted store |
| **PRIVILEGED HOST ACTION REQUIRED** | actual outbound-egress enforcement. This host has none; `python -m qwen_train.f2_isolation` reports it honestly |
| **EXTERNAL EVIDENCE REQUIRED** | S8 base=FAIL/gold=PASS executions with retained output, run log and evaluator bytes; a genuine ACTIVE lesson; a supplied Q9 attestation |
| **AUTHORIZATION REQUIRED** | population acquisition, which `F2-IMPL-AUTH-013` explicitly does not authorize; Q5/Q6 curator authority, still undefined; Q10, then Q12, then Q13 |
| **NOT EXECUTED** | calibration (Q10); the learning event (Q12); confirmatory F2 (Q13); S8 executions |
| **REMAINING PROVENANCE GAP** | the adapter-to-GGUF conversion record. No conversion script exists in the repository and none is invented. |

**Green software gates are NOT experimental readiness.** The repository can verify isolation but not impose it, and can validate evidence but not produce it.

---

### F2-IMPL-AUTH-025 - F2 Isolation Boundary: Research Reconciliation and Topology Decision Gate

**Author:** Rob (human operator), recorded by the release agent on the operator's
explicit instruction of 2026-10-07.

**Authority.** Recorded under 13(4) and 13(7) on the operator's explicit
instruction of 2026-10-07. This is a **documentation/governance entry only**. It
reconciles the historical Q9/Sandbox contract (F2-IMPL-AUTH-009 section 3) with a
completed isolation-boundary investigation and authoritative platform research. It
authorizes **no** infrastructure, **no** code change, and **no** execution. The
recommended topology is a **proposal**.

**Status:** RECOMMENDED / PROPOSED - **NOT IMPLEMENTATION AUTHORIZATION.**

**Baseline of this record:** `master` @ `683c3e82ef6a3def9ebb68ecdfffaa476e8efe63`
(HEAD == `origin/master`; divergence `0 0` at recording). The working tree carried
unrelated pre-existing changes (`tests/conftest.py` modified; untracked
investigation artifacts) that this entry does not touch, adopt, or represent as
repository membership.

#### A. Current-state verified architecture

| Fact | Evidence | Classification |
|---|---|---|
| Local model endpoint is loopback-bound | `runtime_v2/services/_llm_client.py` `_endpoint_for` returns `http://127.0.0.1:8080/v1` (local generation) and `http://127.0.0.1:8083/v1` (vision); `runtime_v2/services/model_router.py:454` `uvicorn.run(app, host="127.0.0.1", port=8080)` | PROVEN |
| Qdrant endpoint is loopback-bound | `swarm_os/core/settings.py:25` `qdrant_url = "http://127.0.0.1:6333"`; `swarm_os/lib/vector/qdrant_store.py:27` `QDRANT_URL` default same; a LISTEN socket on `127.0.0.1:6333/6334` was observed on 2026-10-07 | PROVEN |
| Untrusted task/tool execution runs in the P2/backend process | `qwen_train/cli_baseline_swe.py:105` ("The agent's TOOLS execute in the BACKEND process"); `qwen_train/f2_execution_adapter.py` starts P2 as a fresh `uvicorn` of `swarm_os.app.main:app` bound `127.0.0.1` | PROVEN |
| Current containment is path/in-process, not an OS/hypervisor boundary | `runtime_v2/services/tool_executor.py` (`WORKSPACE_ROOT_CTX`, `_contained()`, module-level `_ROOT`); no kernel/VM boundary | PROVEN |
| The main repository must never be the mutable task workspace | `qwen_train/f2_execution_adapter.py` `bind_task_environment` (fail-closed); `qwen_train/arm_workspace.py` `resolve_required_workspace_root` | PROVEN |
| Evaluator/reference truth must remain outside the mutable workspace and unavailable during agent execution | `docs/EXPERIMENT_J_F2_OP_INFRA_004_AUTHORIZATION.md` section 4; F2-IMPL-AUTH-009 section 3 (REFERENCE TRUTH) | PROVEN (authority) |
| `f2_isolation` recognises enforcement scopes `windows_account`, `firewall_program_path`, `vm`, `windows_sandbox`, and records two Windows firewall platform limits | `qwen_train/f2_isolation.py` `ENFORCEMENT_SCOPE_MODELS`, `PLATFORM_LIMITS` | PROVEN |
| Host has Hyper-V and Windows Sandbox features enabled; only an **Internal** `Default Switch` exists; no NAT is configured | `dism` / `Get-VMSwitch` / `Get-NetNat` (re-probed 2026-10-07) | PROVEN |
| Host has no discrete GPU; the local model runs on the integrated Intel iGPU via the Vulkan path | `AGENTS.md` section 4 (hardware trap); `start-dev.ps1` (llama.cpp `-ngl 99`) | SUPPORTED |

#### B. Why the historical model-inside-Sandbox design conflicts with the current host architecture

F2-IMPL-AUTH-009 section 3 (NETWORK/SANDBOX) assumes Windows Sandbox is the
execution boundary and the model is reached over **loopback from inside the
sandbox** - i.e. the model would run **inside** the sandbox. That assumption is in
tension with this host: the served model depends on the integrated Intel iGPU
(Vulkan), and Microsoft's GPU-paravirtualization documentation describes WDDM/D3D
graphics paravirtualization, not a supported generic Vulkan compute passthrough.
There is therefore **no established basis** that the model can run inside a guest
here with equivalent acceleration. As written, the historical contract is not
satisfiable on the current host without either (a) moving the model off its
accelerator or (b) exposing host-side services beyond loopback. This is a
**governance conflict**, not merely an engineering inconvenience, so
F2-IMPL-AUTH-009 section 3 NETWORK/SANDBOX is marked **PROVISIONAL / UNDER
RECONSIDERATION** (its text is preserved above, unaltered).

#### C. Windows Sandbox limitations relevant to F2 (platform facts)

Per Microsoft primary documentation (see section K):

- Networking is **enabled by default** and uses the **Hyper-V Default Switch**;
  Microsoft warns this "can expose untrusted applications to the internal network."
- A sandbox's `localhost`/`127.0.0.1` is the **guest's own loopback**, so a host
  service bound to `127.0.0.1` is **not automatically reachable** from the sandbox.
- **Mapped folders cross the host/guest boundary**; a writable mapped-folder change
  **persists after disposal**, and even a read-only mapping exposes host files to
  guest code.
- **Protected Client** adds AppContainer isolation (credential/device/file/network/
  process/window), at the cost of restricting copy/paste.
- The sandbox is **disposable** and is tied to the **host OS build**; only one
  instance runs at a time; there is no persistence between sessions.

Operational consequence for F2: a non-persistent sandbox cannot carry F2's declared
execution toolchain (`py -3.10`, git, node/npm, pytest) reproducibly across the
paired F2 task population without re-provisioning per arm.

#### D. Hyper-V Internal-switch semantics (platform facts)

Per Microsoft primary documentation:

- **External** switch binds a physical NIC (external connectivity).
- **Internal** switch connects the host to the VMs (and VMs to each other) -
  host-to-guest - **without** external-network connectivity.
- **Private** switch connects VMs to each other with **no** host connectivity.
- **NAT** (internal switch + NAPT) provides external access.

Therefore the F2 untrusted guest must use a **dedicated Internal switch** and must
**not** use an External switch or NAT/uplink.

#### E. Recommended topology (PROPOSED - not authorized)

```
HOST
 |-- local model / iGPU (llama.cpp, Vulkan)            [host-side]
 |-- narrow host-side model gateway    (future; REQUIRES AUTHORIZATION)
 |-- Qdrant / control / evidence services (as required; NOT auto-exposed)
 |-- evaluator / reference truth (outside guest; unavailable during execution)
 |
 +-- Hyper-V INTERNAL switch (no External, no NAT/uplink)
        |
        +-- dedicated F2 VM
              |-- P2 / backend
              |-- model-generated untrusted tools / code
              |-- only the required task workspace
              |-- no host credentials; no general host filesystem
              |-- controlled evidence transfer
              |-- clean per-arm reset/revert
```

The host-side model remains **outside** the untrusted execution boundary unless a
separately authorized investigation establishes safe accelerator passthrough. The
guest reaches the model only through a **narrowly scoped host-side gateway**.

#### F. Proposed security invariants (PROPOSED ARCHITECTURE REQUIREMENTS - NOT YET IMPLEMENTATION AUTHORIZATION)

1. No External Hyper-V switch for F2 execution.
2. No NAT/uplink for the untrusted guest.
3. Default-deny guest egress.
4. No unrestricted host-service exposure.
5. No Qdrant `0.0.0.0` rebinding shortcut.
6. No general host filesystem mounts.
7. No host credential inheritance; no host credential directories mounted.
8. Workspace-only mutable task state.
9. Controlled evidence transfer/export.
10. Clean per-arm reset/revert (or equivalent clean-room mechanism).
11. Host-side model remains outside the untrusted boundary unless safe accelerator
    passthrough is separately established.
12. Model access is narrowly scoped.
13. The host-side model gateway fails closed.
14. Evaluator/reference truth remains unavailable to the untrusted guest.
15. The topology must not weaken F2 provenance semantics.
16. The topology must not weaken receipt controls.
17. The topology must not alter the scientific design (see section G).

**Model gateway requirements (future authorization; not implemented here).** Expose
only the minimum required model API; no generic TCP forwarding; no arbitrary
host-interface access; bind only to the intended Internal-switch interface; reject
unintended source identities/interfaces; fail closed; no route to
Qdrant/control/evidence unless separately authorized; preserve model-call
provenance; never expose the Wi-Fi/LAN interface.

**Qdrant decision gate.** Qdrant reachability from the isolated P2 environment is
**NOT ESTABLISHED** as a required architectural dependency. A future implementation
investigation must determine whether (A) F2 replay works with Qdrant unavailable;
(B) only a narrow Qdrant-derived capability is needed; (C) a controlled host-side
gateway is needed; or (D) another explicitly authorized mechanism is required.
Qdrant is an **unauthenticated stateful service**; **raw guest-to-Qdrant exposure is
prohibited pending explicit authorization and a security design.** Qdrant must not
be rebound to `0.0.0.0`, and must not be exposed on the Wi-Fi/LAN interface, to make
current code work.

**Alternative - Windows account / firewall / ACL boundary.** Prior investigation
proposed a dedicated non-admin `F2Arm` account with identity-scoped outbound
firewall controls and least-privilege ACLs (repo/model/data/evidence), with loopback
access kept open. It is recorded as **ALTERNATIVE - WINDOWS ACCOUNT / FIREWALL /
ACL BOUNDARY** and must be treated as **LOWER ASSURANCE THAN A HYPERVISOR VM
BOUNDARY**. It is **NOT AUTHORIZED** and **NOT PROVEN AS EFFECTIVE**; it must not be
represented as equivalent to a hypervisor boundary and must not be implemented
during this documentation task.

**Enforcement history (preserved, not promoted).** Prior host investigation observed
that tested Windows Firewall Q9 rules appeared inert and that Avast was registered
as an active security/network-filtering component; the exact policy causation was
**NOT ESTABLISHED**. This is retained as `HISTORICALLY OBSERVED`. It must not be
stated that Avast definitively enforces the final F2 policy, nor that Windows
Firewall is incapable of enforcing the final boundary, unless current authoritative
evidence proves it. Privileged host provisioning (accounts, firewall, ACLs) requires
explicit operator action and is outside autonomous repository implementation; no
credential values belong in the repository. An observed ACL/process walk during
prior provisioning attempts is recorded as `HISTORICALLY OBSERVED - NOT VALIDATION
OF FINAL ISOLATION`.

#### G. Scientific invariants (unchanged by this entry)

This reconciliation is infrastructure-only. It does **not** alter, and must not be
used to alter: treatment/control (T/X/C0) assignment; task population; pairing;
randomization; primary endpoint; stopping rules; statistical analysis (exact
two-sided McNemar, alpha, power, delta, CI); provenance semantics; receipt
semantics; task identity; or clean-room requirements. Any infrastructure change
that would force a scientific-contract change requires **separate authorization**.

#### H. Evidence classification

| Finding | Evidence | Classification | Status |
|---|---|---|---|
| Model is loopback-bound | current source (`_llm_client.py`, `model_router.py`) | PROVEN | current |
| Qdrant is loopback-bound | current source (`settings.py`, `qdrant_store.py`); observed LISTEN | PROVEN | current |
| Tools execute in the backend process | current source (`cli_baseline_swe.py:105`) | PROVEN | current |
| Current containment is path/in-process only | current source (`tool_executor.py`) | PROVEN | current |
| Sandbox networking default / Default Switch | Microsoft Learn | SUPPORTED platform fact | current platform |
| Sandbox mapped-folder host-exposure / persistence | Microsoft Learn | SUPPORTED platform fact | current platform |
| Hyper-V Internal-switch semantics | Microsoft Learn | SUPPORTED platform fact | current platform |
| VM-class boundary recommendation | research synthesis | SUPPORTED / INFERRED | recommendation |
| Host-side model required (no proven guest passthrough) | architecture synthesis | INFERRED | recommendation |
| Qdrant required by F2 replay | insufficient evidence | NOT ESTABLISHED | open |
| Intel iGPU Vulkan passthrough into a guest | insufficient evidence | NOT ESTABLISHED | open |
| Exact firewall enforcement cause | prior host investigation | NOT ESTABLISHED (`HISTORICALLY OBSERVED` rules appeared inert) | open |
| Windows-account boundary | prior proposal (`f2_q9_proposed/`, untracked) | PROPOSED / NOT PROVEN | alternative |
| Hyper-V VM / Internal-switch implementation | governance | REQUIRES AUTHORIZATION | blocked |
| Model gateway implementation | governance | REQUIRES AUTHORIZATION | blocked |

#### I. Governance status

- **Historical authorization** (F2-IMPL-AUTH-009 section 3 NETWORK/SANDBOX) -
  preserved, marked **PROVISIONAL / UNDER RECONSIDERATION**.
- **Investigation result** - actual host/model architecture conflicts with the
  historical topology.
- **Deep research** - Microsoft primary documentation and current security research
  strengthen the concerns.
- **Recommendation** - Hyper-V VM + Internal switch + host-side model gateway.
- **Current status** - **RECOMMENDED / PROPOSED. NOT AUTHORIZED FOR IMPLEMENTATION.**

Research wording is deliberately conservative: *"Current security research and
production agent-sandbox designs increasingly favor VM/microVM-class isolation for
high-risk LLM-generated execution."* This is not a claim of formal consensus.

#### J. Explicit implementation gate

**NO F2 EXECUTION MAY USE THE REVISED TOPOLOGY UNTIL THE TOPOLOGY ITSELF IS
FORMALLY AUTHORIZED.**

Before any implementation, a new or amended F2-IMPL-AUTH decision must explicitly
authorize: Hyper-V VM creation; Internal-switch creation; guest networking; the
default-deny egress mechanism; the model gateway; host-side model access; the Qdrant
reachability decision; firewall/ACL/account changes; the evidence-transfer
mechanism; the P2 host/guest execution split; and any required F2 implementation
changes. Until then, all of the above are `REQUIRES AUTHORIZATION`.

#### K. Authoritative external references

Microsoft primary documentation:

- Use and configure Windows Sandbox - https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file
- Windows Sandbox sample configuration - https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-sample-configuration
- Windows Sandbox FAQ - https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-faq
- Plan Hyper-V networking (switch types) - https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/plan/plan-hyper-v-networking-in-windows-server
- Set up a NAT network - https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/setup-nat-network
- GPU paravirtualization (WDDM) - https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-paravirtualization

Security research (arXiv; research, not platform fact):

- DeepSeek Elastic Compute (DSec): a sandbox infrastructure for effective agentic
  training at scale - arXiv:2609.22978
- Quantifying frontier LLM capabilities for container sandbox escape -
  arXiv:2603.02277
- Sandlock: confining AI agent code with unprivileged primitives - arXiv:2605.26298
- Isolation as a first-class principle for LLM-agent system safety - arXiv:2607.12406
- SandboxEval: securing the test environment for untrusted code - arXiv:2504.00018

Vendor/community guidance (lower authority; context only): agent-sandboxing
guidance from Safeguard, Zylos and PandaStack; `kubernetes-sigs/agent-sandbox`;
`huggingface/smolagents` secure-code-execution documentation.

---

### F2-IMPL-AUTH-026 - F2 Qdrant/embedding dependency resolution and fail-closed suppression requirement

**Author:** Rob (human operator), recorded by the research agent on the operator's
explicit instruction of 2026-10-07.

**Status:** FINDING / RECOMMENDED - **NOT IMPLEMENTATION AUTHORIZATION.** This entry
resolves the Qdrant decision gate left open by F2-IMPL-AUTH-025. It authorizes no
code change, no infrastructure, and no execution.

**Baseline:** `master` @ `e2f98aae31cbd01b88194190e062bb99f79f9ba6`.

**Question resolved (F2-IMPL-AUTH-025 Qdrant decision gate).** Can a governed F2 P2
arm operate without Qdrant, the embedding service (`:8081`), and the background
codebase-index/memory infrastructure, while preserving the F2 scientific / replay /
provenance contract?

**Verdict:** **QDRANT NOT REQUIRED FOR THE F2 TRAJECTORY** - but the current P2
launch does **NOT** fail closed without it. This is a `GOVERNANCE GAP`, not a
causal dependency.

**Authority (F0, frozen).** `docs/EXPERIMENT_J.md` section 15: *"Qdrant
snapshot/restore is NOT a primary T/X causal requirement because T/X replay frozen
treatment artifacts rather than invoking live retrieval. Qdrant is relevant only
for producing/persisting the genuine lesson L at F2 (via `LessonManager.store()`)
and recording the provenance state hash for audit. Qdrant state control is an
operational requirement for C0/learning-event isolation, not a T/X causal-control
requirement."*

**Code evidence (current, `e2f98aae`).**

- Model path is local and Qdrant-free: `runtime_v2/services/_llm_client.py`
  `_endpoint_for` -> `http://127.0.0.1:8080/v1`; `runtime_v2/services/model_router.py:454`
  binds `127.0.0.1:8080`. No vector call on the model path.
- Replay delivery uses the frozen artifact; `install_verified_replay_from_env()`
  fails closed (`swarm_os/app/main.py` lifespan, replay block ~lines 541-560).
- Memory augmentation disabled: `runtime_v2/services/stream_runner.py:688-691`
  ("`SWARM_MEMORY_INJECT=0` disables ALL memory augmentation"); the F2 P2 env sets it.
- F2 P2 env (`qwen_train/f2_execution_adapter.py` `_start_real_p2`, lines ~488-504):
  `SWARM_MEMORY_INJECT=0`, `SWARM_AUTONOMY=0`, `SWARM_EVOLUTION=0`,
  `SWARM_GENETIC_MUTATION=0`, `SWARM_SEMANTIC_CACHE=0`, `SWARM_F1_NO_WEB_TOOLS=1`.

**Dependency table (governed F2 arm).**

| Capability | Exact code path | Required for F2? | Evidence | Classification |
|---|---|---|---|---|
| Model API (`:8080`) | `_llm_client._endpoint_for` -> `model_router` | YES | F2 production model seam | PROVEN |
| Tool execution (filesystem/git/sandbox_repl) | `runtime_v2/services/tool_executor.py` | YES | coder `_AGENT_TOOLS` | PROVEN |
| Qdrant (causal / T-X treatment) | F0 section 15 | NO | T/X replay frozen artifacts | PROVEN (authority) |
| Qdrant startup dependency | `main.py` lifespan (try/except, lazy clients) | NO | boot does not block on Qdrant | SUPPORTED |
| Qdrant read (memory injection) | `stream_runner.py:691` | NO | `SWARM_MEMORY_INJECT=0` | PROVEN |
| Codebase-index daemon | `swarm_os/app/main.py:342-377` | NO (background) | gated `SWARM_CODEBASE_INDEX` default `"1"`; **not** set by F2 env | PROVEN |
| MemoryBridge daemons | `main.py:228-236`; `swarm_os/core/orchestrator.py:137` | NO (background) | no env gate; runs because `orchestrator.bridge` always exists | PROVEN |
| External MCP init | `main.py:210-226` | NO (background) | npx servers (sqlite/memory/context7); try/except | PROVEN |
| Task scheduler / reflection / evolution | `main.py:437-445, 252+, 389+` | NO | scheduler always on; reflection/evolution gated off by F2 env | PROVEN |
| Qdrant-backed agent tools | `_AGENT_TOOLS["coder"]` (`semantic_search`, `remember`, `deprecate_memory`, `mcp`->`qdrant_recall`) | NO (optional) | degrade gracefully | SUPPORTED |
| Evaluator / reference truth | OP-INFRA-004 section 4; -009 (REFERENCE TRUTH) | MUST NOT reach P2 | sequestered | PROVEN (authority) |

**Fail-closed suppression mechanism required (PROPOSED; REQUIRES AUTHORIZATION).**
Before the isolation boundary is real, a governed F2 P2 MUST declare and enforce,
identically for T and X: (1) Qdrant `:6333` and embedding `:8081` unreachable;
(2) codebase-index daemon off (`SWARM_CODEBASE_INDEX=0`); (3) MemoryBridge daemons
off; (4) background learning/reflection/evolution off; (5) Qdrant-backed agent tools
suppressed or explicitly declared unavailable. Smallest proposed change set (not
authorized here): set `SWARM_CODEBASE_INDEX=0` in
`qwen_train/f2_execution_adapter.py` `_start_real_p2` (and keep `qwen_train/f1_infra.py`
`start_backend_fresh` parity if F1 is in scope); add a P2-startup declaration/assertion
that Qdrant `:6333` and embedding `:8081` are unreachable before P3; gate the
MemoryBridge daemons on a no-memory mode (change to `swarm_os/app/main.py` or
`swarm_os/core/orchestrator.py`); optionally extend the existing
`SWARM_F1_NO_WEB_TOOLS`-style strip to the Qdrant-backed tools.

**Scientific invariants (unchanged).** T/X assignment; pairing; randomization; task
population; primary endpoint; first relevant edit; stopping rules; exact McNemar;
mixed-effects sensitivity; provenance semantics; receipt semantics; clean-room
requirements. Making Qdrant unavailable is an ENVIRONMENT decision applied
identically to both arms; it does not alter the treatment artifact or the causal
contrast. Any mechanism that would change the above requires separate scientific
authorization.

**Authorization gate.** The suppression above requires code changes outside the
current F2-OP-INFRA-004 file scope (`swarm_os/app/main.py`,
`swarm_os/core/orchestrator.py`) plus a contract amendment, and is therefore
`REQUIRES AUTHORIZATION`. Until authorized, no F2 execution may assume Qdrant-free
operation, and Qdrant must not be exposed to any guest as a substitute for the
suppression.

---

### F2-IMPL-AUTH-027 - F2 isolation boundary: implementation record

**Author:** Rob (human operator), implemented by the release agent on the
operator's explicit instruction of 2026-10-07.

**Status (point-in-time record, 2026-10-07):** REPOSITORY-SIDE IMPLEMENTATION
COMPLETE AND TESTED. At the time of this entry the host/guest VM
boundary was **BLOCKED - EXTERNAL PREREQUISITE** (no bootable guest image exists on
this host). Not authorization to execute the experiment. **Superseding
current-state note (2026-10-07, later):** the VM shell has since been provisioned
and the Windows guest installed; the current VM/isolation state is maintained in
`docs/EXPERIMENT_J_F2_VM_ISOLATION.md`. This ledger entry is preserved unchanged
as the record of what was authorized at that time; F2 execution remains
unauthorized (readiness gate BLOCKED).

**Baseline:** `master` @ `cdd47f6362a5ab067e481583a513b99365c4d3e7` (HEAD == origin,
divergence 0 0). Implementation commit: `INFRA: implement governed F2 isolation
boundary` (this entry's commit).

#### Implemented (PROVEN in current revision)

| Component | File | Behavior |
|---|---|---|
| F2 runtime guard (predicate + fail-closed probe + capability set) | `runtime_v2/services/f2_runtime_guard.py` (new) | `governed_f2()` keyed on `SWARM_F2_ISOLATION=1`; `assert_forbidden_services_unreachable()` classifies each endpoint `EXPECTED_UNREACHABLE` / `REACHABLE_VIOLATION` / `CHECK_ERROR` and raises `F2IsolationViolation` unless every probe is `EXPECTED_UNREACHABLE`; `strip_forbidden_f2_tools()`; `F2_FORBIDDEN_AGENT_TOOLS`. |
| P2 background suppression | `swarm_os/app/main.py` | When `governed_f2()`: the MemoryBridge daemons, codebase-index self-heal daemon, external MCP init, task scheduler, reflection, atomic/genetic/evolution, autonomy watch-loop, intel, eval-tick, telegram, chess resume and system-probe warmup are NOT started. Sets `app.state.f2_background_suppressed`. Unset marker => normal Swarm OS startup unchanged. |
| Fail-closed isolation assertion | `swarm_os/app/main.py` (lifespan, after replay install) | Governed boot calls the guard; if Qdrant `127.0.0.1:6333` or embedding `127.0.0.1:8081` is reachable (or uncheckable) it raises and P2 aborts before it is eligible to execute. |
| Qdrant-backed capability suppression | `runtime_v2/api/_agent_helpers.py`, `runtime_v2/api/agent_service_v2.py` | Governed F2 removes `semantic_search`, `remember`, `deprecate_memory` from the arm tool surface before tool-schema delivery (inert `mcp`->`qdrant_recall` is additionally covered by MCP suppression). No-op outside governed F2. |
| F2 P2 environment | `qwen_train/f2_execution_adapter.py` | `f2_p2_environment()` (pure, testable). Sets `SWARM_F2_ISOLATION=1` for the PRODUCTION model path (`start_real_p2_production_model`/`execute_arm_real`) and `SWARM_CODEBASE_INDEX=0` for every F2 P2. The fake-model infra proof passes `isolation=False` so it is unaffected. |
| Model gateway | `qwen_train/f2_model_gateway.py` (new) | Forwards ONLY `POST /v1/chat/completions`, `POST /v1/completions`, `GET /v1/models` to the FIXED upstream (default `http://127.0.0.1:8080`). Enforces allow-listed client source; no arbitrary host/port/scheme/URL; no CONNECT/proxy; refuses redirects; bounded body (8 MiB); fail-closed 503 when upstream is unavailable; preserves streaming/SSE. `main()` refuses to bind `0.0.0.0`/`::`/empty or without an allowed client. |
| VM provisioning | `qwen_train/f2_vm_provision.ps1` (new) | Dedicated INTERNAL switch + Generation-2 VM + static private subnet + default-deny extended ACLs with a single allow flow (guest -> host gateway TCP). Non-executing by default; refuses without a legitimate `-ImagePath`. |

#### Suppression matrix (governed F2 P2)

| Subsystem | Non-F2 | Governed F2 |
|---|---|---|
| MemoryBridge `watch_loop` / `start_manager_daemon` | starts | **suppressed** |
| Codebase-index self-heal daemon | starts (`SWARM_CODEBASE_INDEX=1`) | **suppressed** (+ env `=0`) |
| External MCP init (npx servers) | starts | **suppressed** |
| Task scheduler daemon | starts | **suppressed** |
| Reflection / genetic / evolution / autonomy / intel / eval-tick | env-gated | **suppressed** |
| Telegram / chess resume / system-probe warmup | starts | **suppressed** |
| `semantic_search` / `remember` / `deprecate_memory` tools | offered | **removed** |
| Model API | local | local (through the gateway in the VM topology) |

#### Tests (PROVEN in current revision)

- `tests/test_f2_runtime_guard.py` - predicate; real-socket probe classification
  (listening socket => `REACHABLE_VIOLATION`, closed port => `EXPECTED_UNREACHABLE`);
  `assert_*` raises on reachable and on `CHECK_ERROR`; capability strip.
- `tests/test_f2_isolation_suppression.py` - governed env marks isolation +
  `SWARM_CODEBASE_INDEX=0`; capability strip governed-only; governed lifespan
  suppresses background (`f2_background_suppressed` True, no scheduler/watch-loop);
  normal lifespan unchanged.
- `tests/test_f2_model_gateway.py` - allow-listed routes forward; disallowed
  source (403), arbitrary path (404), Qdrant-style path (404), disallowed method
  (404), oversized body (413), upstream unavailable (503), redirect refused (502),
  destination fixed (client `Host` cannot change it), streaming content-type
  preserved.
- Results: `pytest tests/test_f2_runtime_guard.py tests/test_f2_isolation_suppression.py tests/test_f2_model_gateway.py` => 27 passed. Full `tests/test_f2_*.py` + `tests/test_agents_smoke.py` => 1438 passed, 5 skipped, 0 failed. `ruff check <changed> --select E9,F` clean.

#### Host / Hyper-V result

`Get-VM` returned ZERO VMs. No bootable Windows guest VHDX and no ISO exists
locally (only WSL/Claude/container helper VHDs). The Hyper-V feature is Enabled;
the only switch is the Internal `Default Switch`. Therefore the dedicated
Internal-switch F2 VM could **NOT** be instantiated without a legitimate guest OS
image, and none was downloaded. This is `BLOCKED - EXTERNAL PREREQUISITE`; the
provisioning script above is delivered and unexecuted.

#### Scientific invariants (unchanged)

T/X assignment, C0, pairing, randomization, task population, primary endpoint,
first relevant edit, stopping rules, rediscovery, provenance, receipt semantics,
clean-room requirements, and the statistical method are untouched. Qdrant/embedding
availability is an ENVIRONMENT decision applied identically to both arms.

#### NOT ESTABLISHED

- Sandbox/VM-to-host reachability and default-deny ACL behavior on THIS host (no
  VM launched).
- Intel iGPU Vulkan passthrough into a guest (not attempted; model stays host-side).
- P2 boot inside the guest and end-to-end gateway traversal.
- `tests/test_f2_isolation_suppression.py` lifespan assertions are in-process
  (`app.state`), not a real second OS.

#### REQUIRES AUTHORIZATION

Running `qwen_train/f2_vm_provision.ps1`; creating the Internal switch / VM;
starting the model gateway and binding the Internal interface; any firewall /
account change; the P2 host/guest execution split; and any F2 experiment run.

---

*Authorized: 2026-09-29*
*Operator: Rob (human operator)*
*Scope: F2 orchestrator implementation — bounded file set (§10), scientific design unchanged (§3-§9)*
*Boundaries preserved: F0, F1, F2 execution contract, design document, state documentation*
*Not authorized: N=2, Experiment J execution, evaluation, certification, promotion, QLoRA, frontend*

---

### F2-IMPL-AUTH-028 - Population acquisition and admission screening (operator-granted)

**Author:** Rob (human operator), recorded by the agent on the operator's explicit
instruction of 2026-10-07.

**Date:** 2026-10-07

**Authority.** Recorded under §13(4) and §13(7) on the operator's explicit
instruction of 2026-10-07. This entry records an operator-authorized decision; the
agent selected no scope, no target, no cutoff and no screening criterion. It
records no new science, selects no scientific parameter, and modifies no frozen
element.

**Authorization (the operator's instruction, recorded as given).**

> I authorize acquisition and admission screening of the F2 task population from
> the existing downloaded source:
>
> `data/f2_population/population_census_swe_bench_live_full.json`
> `data/f2_population/raw/swe_bench_live_full.jsonl`
>
> The source is authorized for screening; its contents are NOT automatically
> admitted.
>
> The target is at least 300 admitted task pairs for the frozen confirmatory
> population.
>
> UNKNOWN contamination status is NOT CLEAN.
>
> This authorization does not authorize lowering the target, inventing screening
> criteria, executing tasks, running models, starting or using the VM, establishing
> Q9 isolation, running Q10, Q12, or Q13, or declaring F2 READY.
>
> If fewer than 300 tasks survive the authorized screening criteria, STOP and
> report the shortfall. Do not lower the target or create substitute criteria.

**Scope (what this entry grants).** Permission to *use* the two named files as the
population source for the acquisition/screening phase, and to run the existing,
non-execution screening machinery (`qwen_train.f2_population.screen_pool_rows`,
S1–S10) over them. Target: **≥ 300 admitted pairs** (consistent with the frozen
`n = 300` in F2-IMPL-AUTH-013). Contamination policy: **UNKNOWN is NOT CLEAN** —
consistent with F2-IMPL-AUTH-014 (`UNKNOWN` never becomes `CLEAN`) and with
`docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md` §6 Q1.

**Download provenance (recorded accurately; the download PREDATES this
authorization).**

| Fact | Value |
|---|---|
| Raw source | `data/f2_population/raw/swe_bench_live_full.jsonl` — 512,707,210 bytes, created **2026-10-05 15:11:59**, SHA-256 `2200464695BE1E5563E8C1790C9A564EA35D31922E04796385E3A64FA237E7C6` |
| Screened census | `data/f2_population/population_census_swe_bench_live_full.json` — 545,575,384 bytes, created **2026-10-05 15:15:57**, SHA-256 `EC2D8DA26F69331C34765ED9C2C0ABD34C100D36B4E626140233A113148A0DD9` |
| Declared source (inside the data) | `_source.dataset = "SWE-bench-Live/SWE-bench-Live"`, `split = "full"`, `license = "mit"`, `log_parser = "pytest"` |
| Dataset revision / commit pin | **NOT ESTABLISHED** (no revision recorded anywhere in the repository or the files) |
| Who/what downloaded them | **NOT ESTABLISHED** (no tracked record; `data/` is gitignored and `git log -- data/f2_population` is empty) |
| Related same-day artifacts | `q8_derivation_probe.json` (112,980 bytes, 2026-10-05 16:11:22, SHA-256 `81DEF660760F1D651C2834E5F8A2C8BC0C10D825C8033F7F51303D1E8A9E6325`) and 133 cloned repositories under `data/f2_population/work/` — creator **NOT ESTABLISHED** |

The 2026-10-05 download is a **provenance fact, not an authorization**: nothing in
this entry retroactively authorizes it. This entry establishes permission to use
the already-present files as the population source for this screening phase.

**Screening result (PROVEN IN CURRENT REVISION, produced by this authorization's
screening pass, read-only, no execution).**

| Measure | Value |
|---|---|
| Source rows | 1888 (0 malformed JSON lines) |
| Distinct repositories | 223 |
| Duplicate `instance_id` | 1 — `conan-io__conan-18153` (1887 unique ids) |
| S1–S5, S9 | pass for all 1888 rows |
| S6 `relevant_file_set` (R8) | **FAIL 1888** — no frozen relevant file set (R8 not delivered for this source) |
| S7 `source_not_test` | **FAIL 1888** (consequence of S6: no endpoint) |
| S8 `evidence_provenance` | **FAIL 1888** — `evidence_state = MISSING` (no base/gold evidence; S8 requires real base=FAIL / gold=PASS executions) |
| S10 contamination | no `model_cutoff` declared anywhere → state `NOT_DECLARED`, screen passes, **contamination class = UNKNOWN for all 1888** (never CLEAN) |
| **Admitted** | **0** |
| `PopulationManifest.verify()` | **FAIL CLOSED** — `ValueError: duplicate instance_id in manifest: conan-io__conan-18153` |

**Static runnability analysis (INFERRED — metadata only, NOT an admission rule).**
Of the 1888 rows: **1722** `STATIC-CANDIDATE` (S1–S5 + S9 pass, `test_cmd` is a
direct pytest/python-style invocation, no container runtime named), **166**
`UNKNOWN` (POSIX/`make`/shell-composite/venv-path or tool-bootstrap commands whose
Windows-guest requirements cannot be determined from metadata), **0** refused on
OS/infrastructure metadata. `docker` appears in **0** of 1888 `test_cmd` values;
Python/OS/dependency installation requirements are **NOT ESTABLISHED** offline.

**Consequence for the target (CONFIRMATORY TARGET SHORTFALL — HUMAN DECISION
REQUIRED).** Static-metadata viability (1722) exceeds 300, but **admitted = 0**:
S6, S7 and S8 fail for every row, and no authorized model cutoff exists, so
**0 tasks are admissible under the existing authoritative criteria**. The ≥300
target is NOT met and is NOT lowered by this entry.

**GOVERNANCE GAP.** No authoritative **model training cutoff value** exists:
F2-IMPL-AUTH-012 and -014 explicitly decline to select one, no other authority
document declares one, and no code constant carries one. Without it every task is
`UNKNOWN` contamination, which this authorization states is NOT CLEAN.

**Explicit non-authorization.** This entry does NOT authorize lowering the ≥300
target; inventing a contamination, temporal, model-cutoff, evaluator or admission
criterion; admitting any task; recovering or acquiring gold patches; executing any
task or repository test; running any model; starting or using the VM; establishing
Q9 isolation; running Q10, Q12 or Q13; declaring F2 READY; provisioning or
replacing `SWARM_RECEIPT_KEY`; or changing `qwen_train/f2_preflight.py`.

**Implementation status.** Documentation only — no code and no test is changed.
`qwen_train/f2_preflight.py` still reports `authz.population_acquisition =
not_authorized` because that finding is **hard-coded** (`f2_preflight.py:391`) and
does not read this ledger; recognizing this entry from the ledger would be a code
change and **REQUIRES AUTHORIZATION**.

### F2-CLARIFICATION-005 - What `required_pairs` returns, and why n = 300 is unaffected

**Authorizing role:** repository operator (Rob).
**Recorded by:** implementation agent.
**Interactive-authorization basis:** the operator's explicit instruction, given
interactively in his own session on 2026-10-08, to finish the remaining F2
statistical/governance work, reconcile documentation, and commit and push the
authorized result.
**Date:** 2026-10-08.

**Issue.** `F2-IMPL-AUTH-013` records
`required_pairs(delta=0.20, discordance=0.50, alpha=0.05, power=0.90, sided="two-sided") = 116`
and describes the step as an *"Exact sample-size calculation"*. The module
docstring and `PowerPlan.assumptions()["note"]` additionally asserted that the
returned N is *"a slight OVER-estimate ... the safe direction for planning"*.
That sentence was true only of the exact-vs-asymptotic comparison it was lifted
from (arXiv 2605.30315); applied to the function's return value it is false, and
with the AUTH-013 sentence it invites reading **116** as a safe lower bound on
the pairs needed. It is not.

**Classification — `PROVEN` by independent calculation (2026-10-08).** Every row
is reproducible from the repository alone.

| Quantity | Value | Method |
|---|---|---|
| `required_pairs(...)` first crossing | **116**, power **0.900969** | repository function called directly |
| conditional power at m = 118 | **0.839027** | `exact_power` at the rounded cell (n = 59, b = 41, c = 18) |
| conditional power at m = 122 | **0.895089** | idem (n = 61, b = 43, c = 18) |
| conditional power at m = 130 | **0.930641** | idem (n = 65, b = 46, c = 19) |
| **conditional-exact design requirement** | **130 pairs** | smallest discordant count `d` with `P(Bin(d,0.70) ≥ k*(d)) + P(Bin(d,0.70) ≤ d−k*(d)) ≥ 0.90` is **d = 65** (0.910044; d = 64 gives 0.878, d = 66 gives 0.895); `m = d/π_d = 65/0.50 = 130` |
| **unconditional-exact design requirement** | **135 pairs** | smallest `N` with `Σ_d BinomPMF(d; N, 0.50) · P(reject \| d) ≥ 0.90` is **N = 135** (0.902227); monotone thereafter |
| implementation's *stable* crossing | **139** | smallest m₀ with power ≥ 0.90 for **every** m in [m₀, 600]. Below 0.90 above 116: exactly 118-129, 131-133, 138 |

Decision rule for every power figure: exact two-sided McNemar with the doubling
correction — reject iff `2 · P(Bin(d, 0.5) ≥ max(b, c)) ≤ 0.05` — which is what
`f2_statistics._critical_value` and `mcnemar_exact` already implement, so no new
statistical method is introduced or assumed.

**Why the curve falls when m rises.** `n = round(m·π_d)` and
`b = round((n + m·δ)/2)` are both rounded, so the achieved alternative
`θ = b/n` is not monotone in m — m = 116 gives θ = 41/58 = 0.7069 while m = 118
gives θ = 41/59 = 0.6949 — while the exact two-sided critical value jumps at the
same step (n = 58 → k\* = 37, n = 59 → k\* = 38). The alternative gets weaker
*and* the bar rises together. The result is a sawtooth, and the function returns
its **first** crossing.

**Decisions.**

1. **No algorithm change.** `required_pairs` has no production caller anywhere in
   `qwen_train/` — its own definition is the only occurrence — and the enforced
   constant is `FROZEN_MIN_PAIRS = 300` (`qwen_train/f2_readiness.py`). Rewriting
   the helper would contradict the value AUTH-013 recorded while changing no
   runtime behaviour. Changing the algorithm is **`REQUIRES AUTHORIZATION`** and
   is NOT done here.
2. **Documentation corrected.** The return value is now described as a
   rounded-cell, conditional **first crossing** of a non-monotone curve, and the
   "OVER-estimate" claim is scoped to the exact-vs-asymptotic comparison it came
   from (`f2_statistics` module docstring, `required_pairs` docstring,
   `PowerPlan.assumptions()["note"]`).
3. **`n = 300` is untouched and provably unaffected:** `max(300, required) == 300`
   for every π_d in the authorized sensitivity range — 28 / 66 / **116** / 191 /
   261 for π_d = 0.20 / 0.30 / 0.50 / 0.75 / 1.00 (AUTH-013's own table). The
   authorization's statement that "n = 300 exceeds the requirement at every point
   of the authorized sensitivity range" remains `PROVEN`.
4. **Numerical robustness corrected.** `binom_sf` / `binom_cdf` raised
   `OverflowError` above roughly n = 1024, because `comb(2000, 1000) ≈ 1e600`
   cannot be converted to a float — so `required_pairs` would have crashed
   instead of returning its documented `None` on an unattainable specification,
   and `mcnemar_exact`'s Clopper-Pearson bisection would have crashed on a large
   ledger. A log-space fallback now runs **only** when the exact-integer product
   overflows, so every input that previously worked returns bit-identical values.
   Verified against an exact integer oracle (`sum(comb(n,i)) / 2**n`) at n = 2000.

**Not changed:** δ, α, π_d, `k`, `n`, `max_pairs`, the McNemar method, the
confidence-interval construction, the Q6 ceiling, or any readiness gate.

**File boundary.** `qwen_train/f2_statistics.py` (docstrings, comments and the
`OverflowError` fallback only) plus `tests/test_f2_statistics.py`. No other file.

**Verification.** `tests/test_f2_statistics.py::TestRequiredPairsInterpretation`
(9 cases) and `::TestBinomialTailNumericalStability` (4 cases); the pre-existing
F2 statistical / analysis / Q6 / design-guard / readiness suite (154 cases)
passes unchanged alongside them.

### F2-CLARIFICATION-006 - The experimental unit is ONE PAIR = ONE TASK, enforced

**Authorizing role:** repository operator (Rob).
**Recorded by:** implementation agent.
**Interactive-authorization basis:** the operator's explicit instruction, given
interactively in his own session on 2026-10-08, to finish the remaining F2
statistical/governance work, reconcile implementation and documentation with the
authority, and commit and push the authorized result.
**Date:** 2026-10-08.

**Issue.** The authority already answers the unit question, but the analysis did
not enforce it, and two documents phrased the unit as "task x seed", which reads
as if several seeds of one task were several independent pairs.

**What the authority already says (no new science here).**

| Source | Statement |
|---|---|
| F0 `docs/EXPERIMENT_J.md:198` (frozen) | "Unit of analysis: Rollout (**one trajectory per arm per task**)" |
| `EXPERIMENT_J_F2_EXPLORATORY_AUTHORIZATION.md:51-56` (`F2-IMPL-AUTH-024`, operator, 2026-10-05) | "**One pair = one task**, run once under T and once under X ... Repeated runs of the same task are **replicates within one clustered unit** and **MUST NOT** be counted as additional independent pairs"; §6.3: "**No pseudo-replication.** Repeated runs of one task never increase `m`." |
| `EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md:146` | "`m` = complete task pairs (each pair = one T rollout + one X rollout)" |
| `F2-IMPL-AUTH-028` (operator, 2026-10-07) | target "**≥ 300 admitted task pairs**"; "**If fewer than 300 tasks survive** ... STOP" |
| Implementation | `f2_arm_orchestrator.run` is keyed by `task_id`; the F2 execution path has **no seed parameter at all**; `f2_analysis.PairedObservation.seed` is a per-rollout identity field defaulting to `0` |

**Classification — `PROVEN` (authority is sufficient; this is NOT a governance
gap).** Interpretation "one seed per task" is the authorized reading: F0 §11
fixes one trajectory per arm per task, AUTH-024 fixes one pair = one task and
forbids counting repeats, AUTH-028 counts **tasks**, and no execution code
accepts a second seed. "task x seed" in `F2-IMPL-AUTH-013:767/:781` and in the
earlier `PairedObservation` docstring names the *identity* of a unit, not a
licence to draw several.

**Defect (`PROVEN`).** `f2_analysis._partition` — the single function both
`finalize_f2` (producer) and `independent_reconstruction` (auditor) call — built
`m = len(complete)` with no identity check, so two complete pairs for one task
would both be counted. That is exactly the pseudo-replication AUTH-024 §6.3
forbids: it inflates `m`, overstates effective sample size, and breaks the
mutual independence the exact McNemar conditional null `Binomial(b + c, ½)`
assumes. Nothing produced such a ledger today (no confirmatory run exists), so
this is a latent enforcement gap, not a wrong number.

**Decision.** `_partition` now **fails closed**: a second *complete* pair for a
non-empty `task_id` raises `ValueError` naming the task and the rule. The check
lives in `_partition`, so producer and auditor enforce it identically and can
never disagree. It deliberately does **not** silently drop the duplicate:
choosing a survivor would be an exclusion, and AUTH-018 §Q5 requires exclusions
to be pre-specified and outcome-independent — a post-hoc pick is neither. The
repository's own precedent is the same shape: `PopulationManifest.verify()`
fails closed on a duplicate `instance_id`.

**What remains allowed (and is tested):** several *incomplete* rows for one
task, because Q6 reruns retain every attempt and only one attempt may reach a
complete pair; and the documented tuple/map coercion shortcut, which carries no
`task_id` to collide.

**Not changed:** F0, `k`, α, π_d, δ, `n`, the McNemar method, the CI, the Q6
ceiling, the `PairedObservation` schema (the `seed` field stays), and no
statistic moves for any ledger that was already legal.

**File boundary.** `qwen_train/f2_analysis.py` (docstrings + the `_partition`
identity check) plus `tests/test_f2_analysis.py`. No other file.

**Verification.** `tests/test_f2_analysis.py::TestExperimentalUnit` (6 cases);
`tests/test_f2_frozen_design_guard.py` and `tests/test_f2_q6_gate.py` pass
unchanged — including `test_incomplete_pairs_are_reported_not_dropped_as_outcomes`,
whose three same-`task_id` rows are all incomplete and therefore still legal.

### F2-CLARIFICATION-007 - The population gate counts ADMITTED; `n` counts ANALYZABLE

**Authorizing role:** repository operator (Rob).
**Recorded by:** implementation agent.
**Interactive-authorization basis:** the operator's explicit instruction, given
interactively in his own session on 2026-10-08, to trace the F2 population
lifecycle, determine the intended invariant from existing authority rather than
repeat the ambiguity, and commit and push the authorized result.
**Date:** 2026-10-08.

**Issue.** `AUTH-013:781` sets `n = 300 **analyzable** paired task x seed units`,
while the readiness item it names (`protected_population`, `AUTH-020`) is checked
as `admitted >= FROZEN_MIN_PAIRS` (`f2_readiness.py:_check_population`). Two
authorized quantities, one constant — which one does the gate measure, and is it
the wrong one?

**Traced invariant (`PROVEN` from existing authority — no decision invented).**

| Stage | Quantity | Authority |
|---|---|---|
| Screening / population target | **≥ 300 ADMITTED task pairs** | `F2-IMPL-AUTH-028` (operator, 2026-10-07): *"The target is at least 300 admitted task pairs for the frozen confirmatory population"*; *"If fewer than **300 tasks** survive the authorized screening criteria, STOP"*; *"Target: ≥ 300 admitted pairs (**consistent with the frozen `n = 300` in F2-IMPL-AUTH-013**)"* |
| Readiness gate | `admitted >= 300` | `AUTH-020` (item named `protected_population`), `f2_readiness._check_population` |
| Confirmatory ledger | `N` = **complete** task pairs after Q5/Q6 | `AUTH-018` (Q5 exclusion, Q6 ceiling), `f2_analysis.finalize_f2` |
| Design requirement | `n = 300` **ANALYZABLE** pairs | `AUTH-013:781` |

**Is the gate checking the wrong quantity? `NO`.** The gate implements
`AUTH-028` — the operator's own, most recent statement of the population target —
which sets it on *admitted* and explicitly declares that target *"consistent with
the frozen n = 300"*. The two counts are reconciled by authority, not by code.

**Why they are nevertheless not equal (recorded, not resolved here).**
`_check_population` measures a POPULATION prerequisite; `n` measures the ANALYZED
ledger. Between them sit:

* **Q5 exclusions**, whose reasons (missing provenance, failed base/gold
  verification, contamination classification, malformed task, reproducibility
  failure, missing artifact) are **admission-time** properties — `admit_f2_task`
  already requires both T and X bundles plus the paired regrade, so a task that
  is admitted has cleared them;
* **infrastructure loss**, which is *rerunnable* (`f2_calibration`
  `DEFAULT_MAX_RERUNS = 3`, infrastructure causes only, every attempt retained)
  and is capped by the Q6 ceiling: above 30 % of the attempted set the run
  **STOPs and diagnoses** rather than proceeding with a smaller `n`.

So at admitted = 300 the worst Q6-legal outcome is analyzable = 210. As an
`INDEPENDENT CALCULATION` (exact unconditional McNemar power, integrating over
`D ~ Bin(N, π_d)`, same doubling rule, δ = 0.20), the minimum power over
π_d ∈ [0.20, 1.00] at N = 210 is **0.8123**, below the authorized 0.90; the
smallest N that clears 0.90 across that whole range is **270** (0.9035), and
N = 300 clears it with 0.9291 at the worst point (π_d = 1.00).

**Decision.**

1. **No threshold change.** The gate stays at `FROZEN_MIN_PAIRS = 300` on
   `admitted`, exactly as `AUTH-028` set it. Raising it (e.g. to ≥ 429 admitted,
   which would guarantee 300 analyzable under the full Q6 ceiling) would **change
   the operator's population target** and is **`REQUIRES AUTHORIZATION`** — it is
   NOT done here.
2. **Naming corrected.** The `FROZEN_MIN_PAIRS` comment now states which
   quantity the constant is compared against and how the two authorized counts
   relate; the shortfall remedy now says *admitted*, says *oversampled*, and says
   that the design's `n = 300` counts *analyzable* pairs. No behaviour changes.
3. **The counted quantity is now pinned by test**, so a later refactor cannot
   silently swap `admitted` for an analyzable/N figure: an `analyzable` key of
   300 alongside `admitted = 0` must still fail, and `admitted = 300` alongside
   `analyzable = 0` must still pass.

**Not changed:** `FROZEN_MIN_PAIRS`, the comparison operator, the item name, the
18-item readiness list, Q5, Q6, `n`, δ, α, π_d, or any gate's strictness.

**File boundary.** `qwen_train/f2_readiness.py` (module comment and the shortfall
remedy string only) plus `tests/test_f2_readiness.py`. No other file.

**Verification.** `tests/test_f2_readiness.py::TestPopulationCountedQuantity`
(3 cases); the existing `test_population_shortfall_blocks` and
`FROZEN_MIN_PAIRS == 300` guard both still pass unchanged.

### F2-CLARIFICATION-008 - The F1 endpoint count is 9 of 10, not 10 of 10; `k` is unchanged

**Authorizing role:** repository operator (Rob).
**Recorded by:** implementation agent.
**Interactive-authorization basis:** the operator's explicit instruction, given
interactively in his own session on 2026-10-08, to resolve the F1 evidence
contradiction from the artifacts and authority, without altering historical
evidence to make the numbers match, and to commit and push the result.
**Date:** 2026-10-08.

**Issue.** `F2-IMPL-AUTH-013` (F1 freeze) states *"10 VALID endpoint observations
(all at ATIF Step 4)"*, as do `F1_FINAL_RECONCILIATION.md` §5/§6,
`LEARNING_EXPERIMENT_STATE.md`, `EXPERIMENT_J_F1_AUTHORIZATION.md:152`, the
readiness plan §3 and `WORK_LOG.md`. The raw artifacts support **9**, not 10.

**Method.** Each endpoint was re-derived **independently of the stored
`interpretation` block**, from the raw ATIF trajectory
(`data/trajectories/{run_id}.jsonl`, `record_type == "step"`) through the
repository's own production path exactly as `run_repair_task.py` uses it —
`pair_observation_ok(step)` then `f1_infra.find_qualifying_first_edit`. This is
the repository's own reconciliation rule applied: `F1_FINAL_RECONCILIATION.md` §8
— *"Raw JSONL artifact is authoritative; batch summaries are stale snapshots"* —
and `LEARNING_EXPERIMENT_STATE.md:646-648`, which names
`interpretation.f1_endpoint_step` as the authoritative field.

**Result — `PROVEN IN CURRENT REVISION`:**

| | count |
|---|---|
| VALID official observations | **10** (unchanged) |
| stored `f1_endpoint_step == 4` | **9** |
| stored `f1_endpoint_step == null` | **1** — `f1_observation_5.jsonl` L2, invocation `32a0965d7b108e25`, run `36bec9e8-d0e9-47f7-a8b8-2b0940dd9c01` |
| re-derived from the trajectory, agreeing with the stored field | **10 of 10** |

The tenth run's four ATIF steps are `lsp diagnostics`, a
`filesystem {"operation": "read"}`, a duplicate `lsp` call and an
`mcp memory read_graph` — **no `write`/`patch`/`edit`/`create` at all**, so
`find_qualifying_first_edit` correctly returns `None` under
`F1-OP-004-CLARIFICATION` (which requires `function=filesystem` **and**
`operation ∈ {write, patch, edit, create}`). The stored record is **not stale**:
`interpretation.decision_boundary_reached` is already `false` in it. The raw
artifact is correct; **the documentation was wrong** — branch "historical
documentation is stale", not "artifact misclassified" and not "endpoint
definition changed".

**Effect on `F1-OP-004b` and `k`: `PROVEN` none.** A VALID observation with no
qualifying edit inside the horizon is right-censored and contributes the horizon
value 12. The true contribution set is **9×4 + 11×12** (not `10×4 + 10×12`):

```
sorted: 4,4,4,4,4,4,4,4,4, 12,12,12,12,12,12,12,12,12,12,12
nearest-rank P95 (n = 20, rank 19) = 12  ->  k = min(12, max(8, 12)) = 12
```

`P95 = 12` and `k = 12` therefore stand exactly as `AUTH-013` recorded them, and
so do δ, α, π_d, `n` and the McNemar method. **The F1 freeze is unaffected.**

**Effect on endpoint headroom — the only scientifically material consequence.**
Two-sided 95 % Clopper-Pearson exact binomial intervals (α/2 = 0.025, bisection
on the binomial probability tail through the repository's own
`_clopper_pearson_lower/_clopper_pearson_upper`, cross-checked against the
closed form `(α/2)^(1/n)` for `k = n`):

| | p̂ | 95 % CP |
|---|---|---|
| **9 / 10 — raw artifacts, correct** | **0.900000** | **[0.554984, 0.997471]** |
| 10 / 10 — previous wording | 1.000000 | [0.691503, 1.000000] |

Headroom for δ = 0.20 requires `p_X ≤ 0.80`; `[0.555, 0.997]` still contains
values above 0.80, so attainability remains **`NOT ESTABLISHED`** and the Q10
no-lesson calibration is still required. The pilot is also **one task** at
`temp = 0`, so it does not bound `p_X` over the F2 population. Correction of the
numerator **strengthens** rather than weakens the readiness plan's warning: the
endpoint is not perfectly saturated even on the pilot task.

**Superseded on this one point.** `F1_FINAL_RECONCILIATION.md` §5/§6 and the
readiness plan §3 are corrected in this same change. Two further documents still
carry the superseded wording and were deliberately **NOT** edited:

* `docs/EXPERIMENT_J_F1_AUTHORIZATION.md:152`
* `docs/LEARNING_EXPERIMENT_STATE.md:23, :30, :925, :933`

Both are protected Experiment J authority documents under AGENTS.md §3.1 rows 3
and 4, which forbids modifying them without explicit authorization. Correcting
those two lines is **`REQUIRES AUTHORIZATION`** and is NOT done here. The
supersession is recorded so no reader is misled in the meantime.

**Nothing else in the F1 record changes:** the execution registry, the official-20
boundary, the 10 VALID / 10 INVALID split, manifest statuses, frozen hashes,
`P95`, `k`, and every frozen F0/F1 parameter.

**File boundary.** `docs/F1_FINAL_RECONCILIATION.md` (new dated §18),
`docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md` (§2.4 clarification and
§3 numerator), `WORK_LOG.md`, and this document. **No** F0/F1 authority document,
no code, no test, no experiment run.

**Verification.** The endpoint detector itself is pinned by
`tests/test_f1_atif_wiring.py` and `tests/test_f1_evidence.py`; both pass
unchanged, since no code moved.

### F2-IMPL-AUTH-029 - Population source re-acquisition, contamination proxy, and admission/readiness fidelity

**Author:** Rob (human operator)

**Date:** 2026-10-09

**Authority.** Recorded on the operator's explicit instruction of 2026-10-09
(the F2 population-recovery execution authorization), entered by the agent. The
operator explicitly delegated the technical decisions required to select,
acquire, screen and validate a suitable task population, subject to the scientific
integrity requirements stated in that instruction. This entry records decisions
the operator authorised the agent to make; it selects no frozen statistical
parameter and modifies no F0/F1 element.

**Defects this entry fixes (`PROVEN`).**

* **D1 (serialization).** `PopulationEntry.to_dict()` and `PopulationManifest.summary()`
  dropped `created_at` and the contamination verdict, so no census could be
  re-screened or audited for contamination.
* **D2 (admission).** `admitted = all(s.passed)` and S10 passes vacuously when no
  cutoff is declared, so a task could be **admitted while
  `contamination_class == UNKNOWN`**, contradicting AUTH-028 ("UNKNOWN contamination
  status is NOT CLEAN"). The old happy-path test encoded the defect.
* **D3 (readiness).** `f2_readiness._check_population` consumed only an integer
  `admitted`; the module never referenced contamination, so `{"admitted": 300}`
  passed with no contamination evidence.
* **D4 (timestamps).** S10 compared `created_at` and the cutoff **lexically**
  (`elif _created < _cutoff`), with no validation.
* **D5 (duplicates).** The source contains one duplicated `instance_id`
  (`conan-io__conan-18153`, two **differing** payloads); the census carried it and
  `PopulationManifest.verify()` refused the whole manifest.

**Decision - population source.** Acquire the **`SWE-bench-Live/SWE-bench-Live`**
dataset, configuration `default`, split `full`, at the immutable full-length commit

    revision = b51a86422e10cfd403beb4773e5a2947953e36ec

The previous local file `data/f2_population/raw/swe_bench_live_full.jsonl` is
retained, untouched, as historical evidence. It was a reduced projection that
carried **no `problem_statement` and no gold `patch`**, so the F2 harness could not
present the task (`swarm_os/services/prompt_repairer.py:808`) and R8 could not be
derived. The new acquisition preserves every upstream field. Source: MIT
(dataset card), tasks drawn only from OSS-licensed repositories. Acquisition tool
`huggingface_hub.snapshot_download`; two shards (~98 MB) preserved byte-for-byte
under `data/f2_population/upstream_swe_bench_live_b51a8642/raw/` with SHA-256s
recorded in `PROVENANCE.json`. `SWE-rebench-V2` (`nebius/SWE-rebench-V2`, CC-BY-4.0)
was evaluated and not selected: it discloses no issue-creation window and no update
cadence, and replaces `test_cmds` with its own `install_config` shape.

**Decision - R8 (relevant_file_set).** Derived deterministically from each task's
reference fix (`patch`) by `qwen_train/f2_relevant_files.py`: the **non-test
SOURCE** paths touched by the fix. This is an oracle-style proxy for edit scope
(the Q8 concession), needs no clone, and matches S7's requirement that the endpoint
be a source edit. Non-source paths (docs/config/lockfiles) and unknown extensions
are excluded fail-closed.

**Decision - contamination policy (PROXY, not proof).** No authoritative training
cutoff can be established for the served model: Qwen discloses none for
Qwen3/Qwen3.5, and the served GGUF's base/conversion lineage is
**NOT ESTABLISHED** (see AUTH-022 and the base-identity contradiction). The most
defensible proxy supported by this population is the **dataset's own declared
freshness-window start, `2024-01-01`** - the only documented temporal boundary
associated with SWE-bench-Live ("we restrict the dataset to issues created between
January 1, 2024 and April 20, 2025"). This is explicitly a **temporal PROXY for
freshness, NOT proof of zero contamination**, recorded in every census as
`contamination_policy = {is_proxy: true, cutoff: "2024-01-01", basis: ...,
limitation: ...}`. Consequence: 4 rows created before 2024-01-01 are
`PRE_CUTOFF -> POTENTIALLY CONTAMINATED`; the remaining 1882 are
`POST_CUTOFF -> CLEAN` under AUTH-014's vocabulary, and a `CLEAN` label must never
be read as evidence the gold patch was absent from pre-training. Alternative
(cutoff = the base revision's date) would place all rows `PRE_CUTOFF` and admit
nothing; it is recorded but not selected.

**Population funnel (`PROVEN`, reproducible via the commands below).**

| Stage | Count |
|---|---|
| Raw upstream records | 1888 |
| Pre-screen exclusions (conflicting duplicate `conan-io__conan-18153`, all copies) | 2 |
| Unique candidates screened | 1886 |
| S6/S7 fail (reference fix touches no source-code file) | 37 |
| S10 fail (PRE_CUTOFF before 2024-01-01) | 4 |
| **Metadata-eligible (all screens except the S8 execution gate)** | **1845** |
| S8 fail (no base/gold execution evidence) | 1886 |
| **ADMITTED (all screens, per AUTH-028)** | **0** |

`admitted` remains 0: S8 requires real base-fails/gold-passes executions with
retained evidence, which were **not performed** (no container execution
environment available; task execution is not authorised by this entry).

**File boundary.** New: `qwen_train/f2_population_acquire.py`,
`qwen_train/f2_relevant_files.py`, `qwen_train/f2_population_build.py`,
`tests/test_f2_population_recovery.py`. Modified: `qwen_train/f2_population.py`,
`qwen_train/f2_readiness.py`, `tests/test_f2_population.py`,
`tests/test_f2_readiness.py`, `tests/test_f2_evidence.py`, and this document.
Data (gitignored): `data/f2_population/upstream_swe_bench_live_b51a8642/`
(`acquired.jsonl`, `PROVENANCE.json`, `raw/*.parquet`, `census.json`,
`rejections.json`).

**Verification.** `pytest tests/ -k f2` (**1572 passed, 4 skipped** measured at this
commit; corrects the previously recorded "1570". See F2-IMPL-AUTH-030 for the
reconciliation.) The three recovery-critical files pass with
`pytest tests/test_f2_population.py tests/test_f2_readiness.py tests/test_f2_population_recovery.py`
(136). Independent re-derivation: the census is reproducible from
`acquired.jsonl` alone via
`python -m qwen_train.f2_population_build --acquired <jsonl> --out <census> --cutoff 2024-01-01`.

**Explicit non-authorization.** Does NOT execute any SWE task, apply any patch, run
any benchmark test or model, start Docker/VM/service, run Q9/Q10/Q12/Q13, provision
or read `SWARM_RECEIPT_KEY`, change the served model identity, admit any task past
S8, lower or raise the 300 threshold, or alter F0/F1/`k`/`n`/alpha/delta/pi_d/the
endpoint. `admitted = 0` and `swarm_receipt_key` remains unprovisioned; F2 is NOT
ready.

### F2-IMPL-AUTH-030 - Bind the population readiness item to a verified manifest artifact

**Author:** Rob (human operator)

**Date:** 2026-10-09

**Authority.** Recorded on the operator's explicit instruction of 2026-10-09
(population-readiness evidence-binding task), entered by the agent. Records no new
science and changes no frozen element.

**Defect (`PROVEN`).** `qwen_train/f2_readiness._check_population` accepted a
caller-supplied **mapping** and satisfied the prerequisite from two declared values
alone -- an integer `admitted` and a `contamination_policy` declaring a cutoff.
There was **no binding to any population artifact and no integrity check**, so
`{"population": {"admitted": 300, "contamination_policy": {"declared": true,
"cutoff": "2024-01-01"}}}` passed the gate for a population that was never screened.
The two sibling items in the same module already refuse this shape: `_check_manifest`
verifies a real artifact via `runtime_v2.services.f2_freeze.verify_manifest`, and
`_check_clean_room` refuses a caller-asserted boolean; `_check_population` was the
outlier.

**Decision.** `_check_population` now consumes the strongest existing population
artifact and refuses the number:

* The supplied `population` MUST be a `qwen_train.f2_population.PopulationManifest`.
  Any other type (including a mapping that merely declares a count) fails closed
  with "a declared count is not evidence".
* The manifest is re-verified (`PopulationManifest.verify()`) before use.
* The contamination policy is read from the **manifest** (`manifest.contamination_policy`),
  not from the caller, and must declare a cutoff.
* The admitted count is **derived** (`len(manifest.admitted)`), never supplied, and
  compared against the unchanged `FROZEN_MIN_PAIRS = 300`.

**Named limit (honest scope).** `PopulationManifest.verify()` detects an empty
manifest, a blank identity, and a **duplicate identity**; it does not, by itself,
detect a fabricated entry. The binding strength comes from the two mechanisms that
already exist and are unchanged: `PopulationEntry` can only be constructed through
`screen_entry` (`_ENTRY_PROOF`), and ``admitted`` additionally requires S8 evidence
that `f2_evidence.verify_task_evidence` accepts against a **real artifact store**
and a set of **authorized evaluators**. No new trust authority, digest scheme, or
signed record was invented; no caller-supplied number is trusted.

**Regression tests (`tests/test_f2_readiness.py::TestPopulationCountedQuantity`).**
A: a bare `{"admitted": 300}` fails. B: `admitted = 300` plus a declared cutoff but
no manifest fails. C: a declared policy with `contamination_classes = {"UNKNOWN": 300}`
fails, and a genuine manifest with no cutoff derives 0 admitted. D: a caller-supplied
"census" claiming 300 CLEAN tasks with no manifest fails. E: a genuine manifest with
2 admitted tasks fails as a shortfall. F: a genuine, verified 300-admitted manifest
passes, and its count is derived (no number is supplied). The production path
(`evaluate_f2_readiness` -> `protected_population`) is exercised directly in
`test_full_readiness_population_item_uses_the_manifest`.

**Test-count reconciliation.** F2-IMPL-AUTH-029 recorded `pytest tests/ -k f2` as
**1570 passed**; the measured total at that commit was **1572 passed, 4 skipped**,
and the stale figure is corrected above. After this entry the same command reports
**1578 passed, 4 skipped** (1572 + 6 new readiness tests). No test was weakened or
deleted.

**File boundary.** `qwen_train/f2_readiness.py`, `tests/test_f2_readiness.py`, and
this document. No other file.

**Explicit non-authorization.** Does NOT change `FROZEN_MIN_PAIRS`, F0/F1, `k`,
`n`, alpha, delta, pi_d, the endpoint, or any population/statistical rule; does NOT
execute any task, patch, benchmark test, model, Docker/VM/service, or Q9/Q10/Q12/Q13;
does NOT provision or read `SWARM_RECEIPT_KEY`; does NOT reclassify any task or claim
a model training cutoff. S8 remains blocked until task execution is separately
authorized and an isolated execution environment exists; `admitted` stays 0.