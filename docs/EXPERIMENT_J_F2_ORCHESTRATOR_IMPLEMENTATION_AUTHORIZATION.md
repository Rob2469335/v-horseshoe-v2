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

---

*Authorized: 2026-09-29*
*Operator: Rob (human operator)*
*Scope: F2 orchestrator implementation — bounded file set (§10), scientific design unchanged (§3-§9)*
*Boundaries preserved: F0, F1, F2 execution contract, design document, state documentation*
*Not authorized: N=2, Experiment J execution, evaluation, certification, promotion, QLoRA, frontend*