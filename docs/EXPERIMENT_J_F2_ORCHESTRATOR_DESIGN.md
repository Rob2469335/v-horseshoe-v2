# Experiment J F2 — Fresh-Process T/X/C0 Arm Orchestrator Design Contract

**Status:** DESIGN ONLY — implements nothing.
**Date:** 2026-09-29
**Authority:** `docs/EXPERIMENT_J.md` (frozen F0) · `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` · `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md` (accepted `bb38ed85`) · `docs/LEARNING_EXPERIMENT_STATE.md` §10.2 · `AGENTS.md`

This document completely specifies a future F2 orchestrator that satisfies the
already-frozen scientific requirements of F0 **without changing F0, F1, the F2
execution/import authorization, any implementation, or any delivery path.**

Nothing in this document authorizes implementation. Every future implementation
scope requiring separate authorization is listed in §12.

---

## 1. T Creation and Freeze

### 1.1 Scientific requirement (F0 §3, §4)

- **T** = the exact `active_block` string produced by
  `LessonManager.render_active_lessons()` at F2, **including lesson L**
  (F0 §3:52).
- Treatment identity = ordered lesson IDs + ordered lesson content hashes +
  rendered `active_block` + `treatment_set_hash = SHA256(active_block)`
  (F0 §4:76). Treatment identity is **not** rendered text alone (F0 §4:80).

### 1.2 Primitive already implemented (evidence, not authorization)

`runtime_v2/services/f2_freeze.py` provides:

- `FrozenArtifact` dataclass (schema_version, experiment_id, protocol_version,
  git_sha, model_name, task_id, arm, ordered_lessons, lesson_l_id,
  lesson_l_hash, rendered_artifact, treatment_set_hash, lesson_set_hash,
  manifest_hash, freeze_timestamp, promotion_proof_ref, content_address).
- `freeze_artifact(*, rendered_artifact, arm="T", ordered_lessons,
  lesson_l_id, lesson_l_hash, git_sha, model_name, task_id, ...)` — computes
  the three hashes.
- `serialize_manifest` / `manifest_to_dict` / `dict_to_manifest` — canonical
  deterministic JSON.
- `verify_manifest` — integrity verification, **fail closed** on any mismatch.
- `persist_manifest(artifact, directory)` / `load_manifest(path)` — atomic
  persistence / load.
- `FreezeVerificationError`.

### 1.3 T creation source — exact definition

The future orchestrator **parent** MUST obtain T as follows (a **future caller**,
not yet present):

```
active_block = await LessonManager().render_active_lessons(
    task_context=<task_identity_query>, max_chars=<frozen budget>, eval_id=None)
```

- `render_active_lessons` is THE governed seam (lesson_manager.py:358-373);
  it reads the ACTIVE lesson set (versioned, deduped, budgeted,
  contradiction-checked) and renders **rule text only**.
- The block MUST contain lesson L (the genuine, governed ACTIVE lesson). If L is
  not present in the rendered set, T MUST NOT be frozen (rejection; see §2.6).
- **Ordering, lesson IDs, lesson hashes** come from the ACTIVE set as returned by
  the governed seam. The orchestrator records `ordered_lessons` (lesson_id,
  lesson_hash, position) directly from the retrieval result.
- `treatment_set_hash` = SHA256(`active_block`) (F0 §4:76).
- `artifact_hash` — the exact delivered bytes hash (state §10.2D). In F2 this is
  the hash of the actual `active_block` text that will be delivered.
- `manifest_hash` — canonical provenance manifest hash, excluding itself
  (state §10.2D; f2_freeze.py:39-42).

### 1.4 Freeze timing, immutability

- `freeze_artifact(arm="T", rendered_artifact=active_block, ...)` MUST be called
  **before any X derivation**, and the frozen T manifest persisted.
- After freeze, T is **immutable**: the `FrozenArtifact` is never mutated. X is
  derived **from** the frozen T (`FrozenArtifact.rendered_artifact` +
  `FrozenArtifact.ordered_lessons`), never from a live re-render.
- **Proof T was frozen before X exists:** the manifest must contain (a) the frozen
  T `rendered_artifact`, (b) `freeze_timestamp`, and (c) either a pointer to or
  embedded copy of the T manifest content-address from which X was derived.
  X's manifest MUST reference the T manifest's `content_address` as its
  provenance source. The ordering guarantee (freeze before derive) is enforced at
  implementation time by requiring the X-derivation function to accept a
  `FrozenArtifact` (never a live render result).

### 1.5 Evidence required to prove the caller executed correctly

- The orchestrator's T-step produces a **T receipt** whose integrity is proven by:
  1. `verify_manifest(t_artifact)` passing before X derivation;
  2. a persisted T manifest file whose `manifest_hash` matches recomputation;
  3. `ordered_lessons` and `lesson_l_id`/`lesson_l_hash` present;
  4. `treatment_set_hash == SHA256(frozen rendered_artifact)`.

---

## 2. X Derivation — Resolved Scientific Semantics

### 2.1 Authority conclusion (fixed)

Per state §10.2A:998-999, X MUST be derived deterministically from the **frozen**
T artifact. It MUST NOT be produced by re-running live retrieval. This document
adopts that conclusion as fixed. `exclude_ids` is **NOT** used for X derivation.

### 2.2 Authoritative X definition (F0 §3:54-61)

> X = the exact same frozen treatment artifact with ONLY L removed — L's rule
> text deleted from the numbered list.

Prohibited for X: reretrieval, reranking, refill, backfill, substitution, budget
recomputation.

### 2.3 Exact algorithm (Design B — pure derivation from frozen T)

Define a future function (not implemented here):

```
derive_x_from_frozen(t: FrozenArtifact) -> FrozenArtifact
```

Guaranteed behavior:

1. **Input:** `t` = frozen T `FrozenArtifact` with `t.lesson_l_id` set and present
   in `t.ordered_lessons`.
2. **Ordering:** X preserves `t.ordered_lessons` **in the original order**,
   deleting exactly the entry whose `lesson_id == t.lesson_l_id`. No reordering.
3. **Non-L lessons preserved:** every other `LessonEntry` kept byte-for-byte
   (same `lesson_id`, `lesson_hash`, `position` retained from T — positions must
   NOT be renumbered; the deleted L position leaves a numbering gap, matching F0
   "the gap remains", §3:60).
4. **Block text transformation:** X is derived from the frozen ordered lesson
   records. L is identified by its frozen identity `(position, lesson_id,
   lesson_hash)` as recorded in `t.ordered_lessons`. The rendered X block is
   then constructed from the remaining frozen records in their original order,
   with L's rendered entry removed from the numbered list per F0 §3:55 and a
   numbering gap left where L stood (positions of non-L entries are NOT
   renumbered, matching F0 §3:60 "the gap remains"). No re-parsing of the
   rendered T block is used to locate L; no live re-rendering, no re-retrieval,
   no reranking, no refill, no backfill, no substitution, and no budget
   recomputation occur. The operation is a pure derivation from the frozen
   records.
5. **No live call:** the function performs NO `render_active_lessons` call, NO
   retrieval, NO rerank, NO budget recompute. It is a pure function of `t`.
6. **Hashes:**
   - `treatment_set_hash` of X = SHA256(X.rendered_artifact).
   - `artifact_hash` of X = SHA256(X rendered block bytes to be delivered).
   - `lesson_set_hash` of X = SHA256(arm="X" + protocol + non-L lesson
     identity, per the existing `_canonical_lessons_payload` semantics).
   - `manifest_hash` of X = canonical manifest hash excluding itself.
7. **Provenance link T→X:** X manifest MUST set `promotion_proof_ref`-style
   provenance to the T manifest `content_address` (a dedicated field, e.g.
   `source_t_manifest`), so any auditor can reconstruct X from T.
8. **Canonical serialization:** same deterministic JSON (sorted keys, UTF-8) as
   T.

### 2.4 `exclude_ids` — explicit resolution

`exclude_ids` is **NOT used** for X. It is neither required nor needed by
this design, and this design does not depend on it. The design makes it impossible
to interpret X as "rerun retrieval with L excluded": X is defined as a **pure
text/hash derivation of the frozen T manifest**, with no renderer call in the
derivation path, and the
X receipt records `source_t_manifest` provenance proving the frozen source.

### 2.5 Rejection conditions (fail closed)

`derive_x_from_frozen` MUST reject (raise `FreezeVerificationError`, no artifact
produced) when any of the frozen record validations fail:

- `t.lesson_l_id` is missing or empty;
- L (by its frozen identity `lesson_id`) is absent from `t.ordered_lessons`;
- L occurs more than once as a frozen lesson identity in `t.ordered_lessons`
  (duplication);
- positions in `t.ordered_lessons` are missing, duplicated, or out of range
  (malformed position);
- the L record's `(position, lesson_id, lesson_hash)` relationship is
  inconsistent (e.g., `lesson_l_id`/`lesson_l_hash` do not match the identified
  L record);
- the frozen record set cannot deterministically map the selected L record back
  to exactly one rendered position;
- the resulting X would remove anything other than L.

Content equality alone is NEVER the mechanism used to identify L. Two distinct
lessons with identical rule text are still disambiguated by their frozen
`(position, lesson_id, lesson_hash)` identity, so identical rule text is not by
itself a rejection. For multiline rule text, record-to-output mapping is based on
the frozen ordered record and position, not by scanning for a textual line
boundary in the rendered block.

### 2.6 X identity

X is a distinct `FrozenArtifact` with `arm="X"`, own hashes, and the T-provenance
link. X is **not** T; X's `treatment_set_hash` differs from T's only by the L
removal and `SHA256` of the shorter block.

---

## 3. C0 Representation — Resolved Decision

### 3.1 Authority (F0 §3:63-64)

C0 = fresh worker, same task, **no treatment artifact delivered** (empty
`active_block`).

### 3.2 Authoritative choice — serialized empty-artifact manifest through the same fresh-child verification path

Rationale: F0 requires C0 to use the same **fresh-worker / task semantics** and
the same fresh-process isolation as T/X. Routing C0 through the same
`sys.path.insert(0, REPO_ROOT) → load_manifest → verify_manifest →
install_replay_state` path guarantees C0 is *verified as "nothing was
delivered"* rather than silently being the LIVE fallback. This preserves
fail-closed parity across all three arms.

### 3.3 C0 artifact definition

- `arm = "C0"`.
- `rendered_artifact = ""` (empty active block).
- `ordered_lessons = ()`, `lesson_l_id = None`, `lesson_l_hash = None`.
- `task_id` = same task identity as T/X.
- `treatment_set_hash = SHA256("")` (empty block hash).
- `lesson_set_hash` = SHA256(arm="C0" + protocol + empty lessons).
- `manifest_hash` = canonical manifest hash excluding itself.
- `freeze_timestamp` recorded.

### 3.4 Persistence / verification / transport

- C0 manifest persisted via `persist_manifest` (same atomic path).
- Child transports C0 manifest path via `SWARM_F2_MANIFEST_PATH`/`SWARM_F2_REPLAY`
  (same env mechanism).
- Child MUST independently `load_manifest` + `verify_manifest` the C0 manifest,
  then `install_replay_state`. `get_delivery_artifact()` then returns `""`,
  which is **both** the C0-empty-block and the guarantee that no live retrieval
  ran (fail closed: verification failure → no C0, no silent LIVE).

### 3.5 Same fresh-worker task semantics

C0 runs the same task prompt, same fresh workspace, same fresh process, same
rollout/trajectory identity requirements as T/X (F0 §7). The only difference is
the delivered block is empty.

---

## 4. Fresh Process / Workspace Contract

### 4.1 Parent lifecycle (owner: orchestration process)

| Step | Action | Basis |
|---|---|---|
| Workspace prep | `git reset --hard` per arm; `git status --porcelain` clean; delete `.session.json` | F0 §8 REQUIRED CONTROLS |
| T creation | `render_active_lessons` → active_block incl L | F0 §3 |
| Freeze T | `freeze_artifact(arm="T")` → persist manifest | F0 §4; state §10.2A |
| Derive X | `derive_x_from_frozen(t)` (only for X arm) | F0 §3; this design §2 |
| Prepare C0 | empty-artifact manifest (only for C0 arm) | F0 §3; this design §3 |
| Manifest persist | `persist_manifest` T (/X/C0) | implemented primitive |
| Env construction | `SWARM_F2_REPLAY=1`, `SWARM_F2_MANIFEST_PATH=<path>`, repo root | f2_replay transport; accepted contract |
| Fresh process launch | `Popen([sys.executable, "-u", <worker script>], env=..., cwd=REPO_ROOT)` | F0 §7 fresh process; accepted contract |
| Process identity | capture child PID + parent PID + command identity | F0 §7 evidence; F1 precedent |
| rollout_id / trajectory_run_id | UUID4 per arm; backend trajectory_run_id per `step_agent_stream` | F0 §7 |
| Evidence collection | collect child result/evidence after completion | F0 §7 |

### 4.2 Child lifecycle (owner: fresh worker process)

| Step | Action | Basis |
|---|---|---|
| Bootstrap | `sys.path.insert(0, REPO_ROOT)` BEFORE repository imports | accepted F2 execution/import contract |
| Manifest load | `load_manifest(SWARM_F2_MANIFEST_PATH)` | state §10.2B |
| Independent verify | `verify_manifest(artifact)` | state §10.2B |
| Replay install | `install_replay_state` → FROZEN_REPLAY | f2_replay |
| Arm validation | assert `artifact.arm` matches the requested arm | state §10.2E/F |
| Delivery | `get_delivery_artifact()` → concatenate into system prompt | state §10.2C |
| Worker execution | run the arm's governed worker loop | F0 §5 endpoint |
| Evidence return | produce arm receipt (see §7) | F0 §7 |

### 4.3 F0 requirement vs F1 precedent vs F2 design choice

- F0 requirement: fresh workspace reset, fresh process, rollout/trajectory
  identity, empty session, no resume (F0 §7, §8).
- F1 precedent (not authorization): `f1_infra.py` process-record (pid,
  expected_pid, command_identity, parent_pid), health gate, evidence manifest.
- F2 explicit design choice: reuse F1's **process identity fields and evidence
  manifest pattern** because F0 §7 requires process/trajectory/timestamp
  evidence and f1_infra proves the schema is viable in this repository.
  [2026-09-29 F2 AUTHORITY AMENDMENT note: `expected_pid` is a recorded field
  only; expected-PID equality is NOT a requirement.]
  The F1 **health gate** is NOT required by F0 for F2; it is optional precedent
  and may be omitted unless monitoring proves necessary. Every reuse is a design
  choice, not an inheritance claim.

---

## 5. Delivery-Path Contract

### 5.1 Callers inventoried (evidence)

| Path | Uses `get_delivery_artifact()`? | Can bypass replay? | Delivers worker lesson block? |
|---|---|---|---|
| `runtime_v2/services/stream_runner.py:658-669,765-779` | YES (gated `is_replay_active()`) | No (when replay active) | YES — SEP/empty-failure prompt paths |
| `runtime_v2/api/agent_service_v2.py:2667-2675` | YES (gated) | No | YES |
| `swarm_os/brain.py:262-285` (`render_active_lessons` direct) | No | YES | YES (legacy swarm-brain path) |
| `organism_console/core/repair_engine.py:656` (direct) | No | YES | YES (reflexion-repair path) |
| `runtime_v2/services/f2_replay.py` | provider itself | n/a | n/a |

### 5.2 Reachability from an F2 arm

- The F2 arm's worker runs through `stream_runner.py`/`agent_service_v2.py`
  (the runtime worker delivery paths) — **both gated**.
- **F1 worker reachability (established by repository tracing):** neither
  `swarm_os/brain.py` nor `organism_console/core/repair_engine.py` is reachable
  from the F1 worker path (`run_curriculum → organism_console --json →
  stream_runner/agent_service_v2`). `brain.py` is the legacy swarm-kernel brain
  used by simulation/scenario paths; the `render_active_lessons` call in
  `repair_engine.py` is reached only via explicit `/heal`, autonomy/watch-loop,
  or simulation-style paths — not via the worker prompt.
- **F2 reachability remains NOT PROVEN** in this design: the future F2
  worker/orchestrator execution path does not yet exist to trace. The design
  does NOT include the swarm-brain or reflexion-repair path in the F2 arm, and
  the orchestrator MUST assert that an F2 arm never enters them; if a future
  authorized F2 arm invokes them, that path is a bypass and MUST be addressed
  (see §5.3). This is not a claim that F2 cannot reach them.

### 5.3 Orchestrator delivery contract (state §10.2C)

**One authoritative delivery provider:** `get_delivery_artifact()` MUST be the
only path by which the F2 arm obtains its treatment block. The orchestrator
contract therefore requires:

1. The F2 arm is bound to the gated runtime worker paths
   (`stream_runner`/`agent_service_v2`), which consult `is_replay_active()` →
   `get_delivery_artifact()`.
2. **If** any reachable bypass exists for a given arm (i.e., the arm's worker
   could reach `brain.py` or `repair_engine.py` before its final edit), the arm
   MUST either (a) be aborted with `contamination_delivery_bypass`, or
   (b) be prevented from reaching it by construction (delivery-relevant code
   only via the gated seam). This is a **future remediation requirement**, not
   implemented here, and **requires separate authorization** (§12).
3. No independent replay guard may be added; the singleton provider is
   authoritative (state §10.2C).

---

## 6. Delivery Instrumentation

F0 defines three delivery evidence fields (§4:77, §6, §7):

| Field | Producer | Computation point | Canonical input | Persisted in | Manifest? | Independently verifiable? | Missing → run? |
|---|---|---|---|---|---|---|---|
| `lesson_block_hash` | child (delivery) | at the moment the frozen block is concatenated into the prompt | SHA256(active_block) | arm receipt + trajectory | equivalence to `treatment_set_hash` (for T) is checkable | YES (recompute SHA256 of delivered block) | INVALID RUN |
| `final_prompt_hash` | child (delivery) | when prompt built & block appended | SHA256(system_prompt + active_block) | arm receipt + trajectory | YES (recompute) | YES | INVALID RUN |
| `delivery_timestamp` | child (delivery, pre-first-decision) | before the worker makes its first decision | wall clock of the delivery moment | arm receipt + trajectory | timestamp ordering vs first edit step | YES (step.timestamp comparison) | INVALID RUN (rediscovery can't be classified) |

- **Producer:** child (the process that actually delivers). Parent records the
  child's reported values in the receipt.
- **Relation to manifest:** `treatment_set_hash` (in manifest) ==
  `lesson_block_hash` (in receipt) for T and X should be consistent; X's differ
  from T's. C0 = hash of empty.
- **Where recorded:** arm receipt (§7) and trajectory (`F0 §7` requires trajectory
  records).
- **Required before certification:** YES (F0 §6 rediscovery is timestamp-based).

No extra telemetry is designed.

---

## 7. Arm Receipt / Evidence Schema

Define (schema only; no serialization). Ownership: parent aggregates child output.

| Field | Owner | Notes |
|---|---|---|
| experiment_id | parent | frozen "experiment_j" |
| protocol_version | parent/frozen | "f2_v1" |
| git_sha | parent | repo commit at arm time |
| task_id | parent | frozen task identity |
| arm | parent | T / X / C0 |
| rollout_id | parent | UUID4 |
| trajectory_run_id | child/backend | per step_agent_stream |
| process identity | parent | child PID, parent PID, command identity |
| treatment identity | parent | ordered lesson ids/hashes (from frozen artifact) |
| T/X/C0 identity | parent | content_address + treatment_set_hash |
| manifest identity / integrity | parent+child | manifest content_address + verified manifest_hash |
| lesson_block_hash | child | delivered block hash |
| final_prompt_hash | child | delivered prompt hash |
| delivery_timestamp | child | pre-first-decision |
| execution timestamps | child | start/end of worker execution |
| outcome/endpoint evidence | child | first-edit filesystem action within k steps, path ∈ relevant_file_set, per F0 §5 |
| invalidation_reason | any | e.g. contamination_*, verify_*, bypass (free text consistent with F0 categories) |

---

## 8. Fail-Closed Matrix

| Case | Behavior | Basis |
|---|---|---|
| Missing manifest | FAIL CLOSED (raise, no LIVE) | f2_replay.py:254-257 |
| Corrupt manifest | FAIL CLOSED (verify raises) | state §10.2F |
| Manifest hash mismatch | FAIL CLOSED | verify_manifest |
| Artifact hash mismatch | FAIL CLOSED | verify_manifest (artifact hash check) |
| Wrong arm | FAIL CLOSED — child must not execute a different arm than manifest | state §10.2E/F |
| Wrong task | FAIL CLOSED if task_id mismatch is integrity-relevant | state §10.2E |
| Wrong git identity (where required) | FAIL CLOSED (manifest git_sha) | state §10.2E |
| Missing X | ABORT ARM (no valid X) | F0 §3 |
| Malformed X | FAIL CLOSED (derive rejects) | this design §2.5 |
| Missing C0 | ABORT ARM | F0 §3; this design §3 |
| Replay unavailable | FAIL CLOSED — FROZEN mode must not run LIVE | state §10.2C/F |
| LIVE fallback | FORBIDDEN — any silent fallback invalidates the arm | state §10.2F |
| Delivery bypass | ABORT ARM (`contamination_delivery_bypass`) | this design §5.3 |
| Bootstrap failure | ABORT ARM | F0 §7 fresh process |
| Worker identity mismatch | INVALID RUN (evidence integrity) | F0 §7 |
| Missing delivery evidence | INVALID RUN | F0 §6/§7 |
| Missing outcome evidence | INVALID RUN | F0 §5 |
| Evidence persistence failure | INVALID RUN | evidence requirement |

No new scientific classifications invented; falls within F0 contamination and
invalid-run terminology.

---

## 9. Concurrency / Mutability

- **One fresh process per arm — REQUIRED.**
  - F0 §7: "Fresh Python process per arm"; F0 §8: "Process reuse = REQUIRED
    CONTROL".
  - `f2_replay.py` uses process-global `_f2_state` (authoritative for production
    delivery). Running two arms in one process would share/mutate that global and
    permit cross-arm delivery contamination.
- Within the one arm process: standard asyncio (FastAPI worker) is expected; the
  process-global state is visible across its tasks, which is desired for a single
  worker.
- **Prohibited:** multi-arm reuse of one process; reusing live retrieval when
  FROZEN_REPLAY is active.
- **Immutable lifetime:** T, X, C0 `FrozenArtifact`s are immutable after
  creation.
- **Replay-state lifetime:** installed at child start; cleared by
  `clear_replay_state()` after arm completion (f2_replay.py:121-129) or process
  exit — never mid-arm.
- **Worker completion semantics:** when the worker's `k` steps complete or the
  timeout/endpoint fires, the child stops the worker, records outcome evidence,
  and returns. The parent terminates the child process. No partial-arm resume.

No concurrency implementation changes are designed.

---

## 10. Complete Execution Graph

| # | Transition | Owner | Input | Output | Verification | Failure behavior | Mutability |
|---|---|---|---|---|---|---|---|
| 0 | workspace reset | parent | fresh clone | clean working tree | `git status --porcelain` empty, HEAD=`base` | ABORT ARM | reset once |
| 1 | render T | parent | task query | active_block incl L | L present | ABORT ARM | — |
| 2 | freeze T | parent | block | FrozenArtifact(T) + manifest | `verify_manifest` | ABORT ARM | T immutable |
| 3 | derive X | parent | FrozenArtifact(T) | FrozenArtifact(X) | `verify_manifest`(X) + T→X link | fail closed (§2.5) | X immutable |
| 4 | prepare C0 | parent | task identity | FrozenArtifact(C0) empty | `verify_manifest`(C0) | ABORT ARM | C0 immutable |
| 5 | persist artifact(s) | parent | manifest bytes | manifest file | load roundtrip | ABORT ARM | file immutable |
| 6 | spawn fresh worker | parent | env + worker cmd | child process | PID identity | ABORT ARM | — |
| 7 | bootstrap | child | REPO_ROOT | sys.path[0]=REPO_ROOT | import repo pkgs | ABORT ARM | — |
| 8 | load manifest | child | manifest path | FrozenArtifact | parse ok | fail closed | — |
| 9 | verify manifest | child | artifact | verified | `verify_manifest` | fail closed | — |
| 10 | install replay | child | artifact | FROZEN_REPLAY state | `is_replay_active()` | fail closed | state until clear |
| 11 | validate arm | child | requested arm | arm==manifest.arm | equality | fail closed | — |
| 12 | deliver | child | get_delivery_artifact | active_block in prompt | gated provider | INVALID RUN if bypass | prompt immutable per run |
| 13 | record delivery evidence | child | block | lesson_block_hash/final_prompt_hash/delivery_timestamp | recompute | INVALID RUN | — |
| 14 | execute | child | worker task | run to k/timeout | endpoint detection (F0 §5) | INVALID RUN | — |
| 15 | record outcome | child | trajectory | first-edit evidence | endpoint criteria | INVALID RUN | — |
| 16 | persist receipt | parent | child output | arm receipt | schema validate | INVALID RUN | — |
| 17 | validate receipt | parent | receipt | verified receipt | cross-check hashes | INVALID RUN | — |
| 18 | terminate process | parent | child | child exit | PID reaped | ABORT ARM | — |

> **Design-amendment note (2026-09-29):** rows 7–14 describe the **F2 worker /
> arm-controller** view. Steps 12–14 ("deliver", "execute") are NOT guaranteed to
> run in the worker process: the authoritative model-facing delivery point lives
> in the **execution/backend process** that runs `stream_runner` /
> `agent_service_v2` and consults `get_delivery_artifact()`. `_f2_state` is
> process-local (f2_replay.py:87). Therefore "the worker installed replay" does
> NOT by itself make model delivery frozen unless the delivery process IS the
> worker process. The unambiguous process-ownership model is specified in §13.

---

## 11. Invariant A–H Crosswalk

| Invariant | Satisfied by | Status |
|---|---|---|
| A. Freeze T first | §1.4, §2.3 (derive from frozen T only) | RESOLVED |
| B. Cross-process replay | §4 env transport; child load+verify+install | RESOLVED |
| C. One delivery abstraction | §5 `get_delivery_artifact()` singleton; bypasses documented + remediation defined | RESOLVED and IMPLEMENTED — `_f2_replay_required()` abort at `stream_runner.py` and `agent_service_v2.py` |
| D. Separate hashes | §1.3, §2.3, §3.3; manifest self-exclusion | RESOLVED |
| E. Freeze provenance | §1 manifest fields | RESOLVED |
| F. Fail closed | §8 matrix | RESOLVED |
| G. Retrieval mutation isolation | replay returns frozen artifact regardless of live mutation (existing tests) | RESOLVED (test-level); orchestrator inherits |
| H. Rediscovery evidence | §6 delivery_timestamp + §7 receipt | RESOLVED in design; `qwen_train/f2_rediscovery.py` and `classify_run_rediscovery` are IMPLEMENTED and on the verification path, but *persisting* the rediscovery block is still not authorized |

No invariant silently changed.

---

## 12. Authorization Boundary

**This document is DESIGN ONLY.** It does NOT authorize:

- orchestrator implementation;
- X derivation implementation;
- C0 implementation;
- delivery bypass remediation;
- delivery instrumentation implementation;
- arm-receipt implementation;
- test modifications beyond inspection already performed;
- N=2;
- evaluation;
- certification;
- promotion;
- ACTIVE lesson creation;
- `SWARM_RECEIPT_KEY` provisioning.

Future implementation scopes requiring **separate explicit authorization**:

1. A scoped **orchestrator implementation authorization** (create parent/child
   harness, wiring artifact creation + fresh-process spawn + evidence
   collection).
2. A scoped **X-derivation implementation authorization** (implement
   `derive_x_from_frozen` and its rejection checks per §2).
3. A scoped **C0 implementation authorization** (C0 manifest path per §3).
4. A scoped **delivery instrumentation authorization** (§6 hashes/timestamps).
5. A scoped **arm-receipt/schema implementation authorization** (§7).
6. A **delivery-bypass remediation authorization** ONLY IF §5.3 reachability
   analysis concludes a bypass is reachable in a real arm (§5.2 — currently
   NOT PROVEN).
7. Separately and later: **certification**, then **N=2** — each requires F0/state
   conditional process (state §10.3 steps 10-14).
8. A scoped **real-execution worker authorization** — required before any code
   invokes an actual governed task. It must name the execution seam (§13),
   the delivery-process replay-ownership model (§13.1-13.4), the exact F1
   machinery reused (§13.7), the receipt/evidence layers (§13.6), and the
   fail-closed contract (§13.10). It MUST NOT authorize N=2, Experiment J
   execution, evaluation, certification, promotion, QLoRA, frontend, or
   unrelated deployment.

**Resolved design decision (repository-supported, authority-compatible):**
the X anchor is the frozen ordered-record identity `(position, lesson_id,
lesson_hash)` already represented by `FrozenArtifact` / `LessonEntry`. L is
identified from the frozen ordered lesson records, not by reparsing rendered
text or matching `lesson_l_hash` against live rules. This is a design decision;
it does not authorize implementation.

**UNRESOLVED — AUTHORITY CLARIFICATION REQUIRED:**
F2 worker reachability of the `brain.py` / `repair_engine.py` delivery paths.
F1 worker reachability is resolved: neither bypass is reachable from the
documented F1 worker path (`run_curriculum → organism_console --json →
agent_service_v2 / stream_runner`). F2-specific reachability remains NOT PROVEN
until the authorized F2 worker execution path exists and can be traced and
verified.

---

## 13. Real Execution Seam — Design Amendment (2026-09-29)

This section is a **bounded design amendment** specifying the missing real
execution seam. It does NOT authorize implementation and does NOT change any
scientific or authorization boundary. It supersedes any earlier implied model in
which "the worker delivered the frozen artifact" was assumed to equal
"model delivery was frozen".

### 13.0 Baseline process-ownership facts (non-negotiable)

- `_f2_state` (f2_replay.py:87) is a **module global loaded once per Python
  process**. It never crosses a subprocess boundary by itself.
- The authoritative model-facing delivery points are in the **backend process**
  that runs `runtime_v2/services/stream_runner.py` (658-671, 770-780) and
  `runtime_v2/api/agent_service_v2.py` (2667-2675), which call
  `get_delivery_artifact()` only when `is_replay_active()` is true in **that
  backend process**.
- Installing replay state in the F2 worker process therefore does **NOT** install
  replay in the backend process. If the backend has no replay state, those
  delivery points fall back to LIVE `render_active_lessons()`.
- **Conclusion:** a real F2 arm MUST establish FROZEN_REPLAY state in the exact
  process that consults `get_delivery_artifact()`, and MUST fail closed if that
  state is not active there.

### 13.1 Required process graph (unambiguous ownership)

```
F2 orchestrator (parent — P0)
   │  render T → freeze T → derive X / build C0 → persist manifest
   ▼
F2 worker / arm controller (P1, fresh process per F0 §7)
   │  bootstrap: sys.path.insert(0, REPO_ROOT)
   │  load_manifest → verify_manifest → validate arm==manifest.arm
   │  install_replay_state(...)   [installs in P1's own _f2_state]
   │
   │  REAL EXECUTION SEAM (to be authorized separately; see §12 item 8)
   │
   ▼
Execution / delivery process (P2)
   │  receives SWARM_F2_REPLAY=1 + SWARM_F2_MANIFEST_PATH (env propagation)
   │  at P2 startup: install_verified_replay_from_env() → install_replay_state
   │                 INTO P2's _f2_state  (mirrors swarm_os/app/main.py:522-541)
   │  BEFORE any model request: is_replay_active() MUST be True
   │  model-facing delivery: get_delivery_artifact() → system prompt  (P2)
   │
   ▼
Model/agent runtime (P2)  →  task result / trajectory  →  evidence  →  F2 receipt
```

- **P0 (orchestrator)**: creates, freezes, derives, persists artifacts; constructs
  env; spawns P1; collects + validates the final receipt.
- **P1 (worker/arm controller)**: a fresh worker/controller process, per arm.
  Verifies the manifest and validates the arm identity. P1 does not own delivery
  replay state for the HTTP graph.
- **P2 (execution/delivery process)**: a separate fresh backend process, per arm,
  that actually executes the governed task and renders the model request.
  **P2 owns the authoritative `_f2_state` and delivery for an F2 arm.**
- HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY AMENDMENT:
  the prior statement "P1 and P2 MAY be the same process" and its fusion
  rationale are retained as a historical note only. Under the amendment, the F2
  HTTP execution graph is exactly `P0 → P1 → P2` with P1 and P2 separate fresh
  processes; P3 is not required; **P1/P2 fusion is NOT authorized**; P1 drives
  P2 directly over loopback HTTP/SSE (`127.0.0.1`); P2 owns authoritative replay
  state and delivery.

### 13.2 Where replay is installed and verified (P2)

1. The frozen manifest path reaches P2 via environment:
   `SWARM_F2_REPLAY=1`, `SWARM_F2_MANIFEST_PATH=<content-addressed path>`.
2. At P2 process start, `install_verified_replay_from_env()` loads + verifies the
   manifest and installs `_f2_state=ReplayState(FROZEN_REPLAY, ...)`.
3. Immediately before the first model request, P2 must assert
   `is_replay_active()` is True and `get_delivery_artifact()` returns the
   installed frozen delivery state.
   [DESIGN-PROPOSAL — NON-NORMATIVE — NOT CURRENT F2 AUTHORITY: the prior phrase
   "hash equal to `treatment_set_hash`" described a design proposal; the
   committed F0 authority requires recording the delivery identity, and whether
   a delivery-time hash mismatch is itself an abort condition remains a separate
   implementation/authorization question that is NOT resolved by the
   2026-09-29 F2 AUTHORITY AMENDMENT.]

### 13.3 Exact final-delivery invariant

> Once an F2 arm is selected, the actual model-facing lesson/prompt content MUST
> come from the verified frozen T/X/C0 artifact via `get_delivery_artifact()` in
> the delivery process (P2), and MUST NOT come from a live
> `render_active_lessons()` retrieval / rerank / refill / backfill /
> substitution / `exclude_ids` path.

If `is_replay_active()` is False at any delivery point in P2, the arm MUST abort
before model execution. **LIVE `render_active_lessons()` is never an acceptable
fallback for an F2 arm.**

### 13.4 Fail-closed contract (design-level)

An F2 arm MUST abort before model execution (never fall back to LIVE) if ANY of:

- manifest missing / unreadable / hash invalid;
- arm mismatch (requested != `manifest.arm`);
- T/X/C0 identity mismatch vs manifest;
- replay cannot be installed in the execution/delivery process P2;
- `is_replay_active()` is False in P2 at the delivery point;
- `get_delivery_artifact()` cannot return the verified frozen artifact;
- delivered artifact hash != `manifest.treatment_set_hash`;
  [DESIGN-PROPOSAL — NON-NORMATIVE — NOT CURRENT F2 AUTHORITY: retained as a
  historical design proposal. The committed F0 authority requires RECORDING the
  delivered delivery identity (`lesson_block_hash`, `final_prompt_hash`,
  `delivery_timestamp`, backend PID/start time); whether a delivery-time hash
  mismatch is itself an abort condition is NOT resolved by the 2026-09-29
  amendment and remains a separate implementation/authorization question.]
- execution/delivery process identity cannot be established;
- the backend serving P2 is not the expected fresh process (per F0 §7/§8);
  [HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY AMENDMENT:
  P2 identity is the serving process — the process owning the listening socket
  that serves the F2 task, with its start time (`p2_serving_pid` /
  `p2_serving_start_time`). Launcher PID/start time and evidence-writer PID are
  recorded separately; expected-PID equality is NOT a requirement.]
- task execution crosses an unauthorized LIVE-delivery path (see §13.8);
- child / backend / CLI times out;
- model execution fails;
- required execution evidence is missing;
- the receipt cannot bind execution evidence to the frozen manifest.

### 13.5 Task/workspace/rollout boundary

- **Task prompt**: supplied to P1/P2 by the ORCHESTRATOR as the frozen task
  identity; must be the same `task_id` recorded in the manifest and the CLIP task.
- **Workspace reset**: on a fresh isolated clone; `git reset --hard` per arm,
  `.session.json` deleted (F0 §8), HEAD = frozen base.
- **rollout_id / trajectory_run_id**: created fresh per arm (UUID4), propagated to
  P2 via env; must match the ids recorded in the receipt.
- **Arm identity**: `--arm` (CLI) and `manifest.arm` must be equal; the worker
  already enforces this (f2_arm_worker.py).

### 13.6 Receipt / evidence design amendment

The F2 receipt must distinguish SIX evidence layers, joined by immutable
identifiers:

| Layer | Who produces | Evidence captured |
|---|---|---|
| A. Controller/worker | P1 | repo-root bootstrap, manifest load+verify result, worker PID |
| B. Execution backend | P2 | backend PID, health-gate result, `install_verified_replay_from_env` outcome |
| C. CLI/task | P1/P2 CLI | task prompt hash, `--arm`, rollout/trajectory identity |
| D. Model/provider | P2 | model selected, provider, latency, token counts |
| E. Task endpoint/outcome | P2 | first-edit detection (F0 §5), steps to first edit, outcome |
| F. Frozen-delivery identity | P2 | `get_delivery_artifact()` value, `lesson_block_hash`, `final_prompt_hash`, `delivery_timestamp` |

Joined by: `experiment_id`, `arm`, `task_id`, `rollout_id`,
`trajectory_run_id`, `manifest.content_address`, `treatment_set_hash`,
`lesson_block_hash`, `final_prompt_hash`, and P2 `process_identity`. These
identifiers MUST be equal across layers A–F for the receipt to be valid. No new
scientific values are invented; the receipt binds existing immutable identities.

### 13.7 F1 reuse boundary

**Technical reuse of F1 machinery is a design/implementation fact, NOT F2
scientific authorization.** If implementation later uses `_reset_instance`,
`start_backend_fresh`, `wait_for_backend`, `_attempt_once`, or the existing
`stream_runner` / `agent_service_v2` delivery, the future real-execution
authorization must name those mechanisms explicitly. F1 authorization
(`docs/EXPERIMENT_J_F1_AUTHORIZATION.md`) grants only the F1 no-lesson pilot and
does NOT extend to F2 execution.

### 13.8 Bypass boundary (preserved)

- `stream_runner` (658, 770) and `agent_service_v2` (2667) replay-gated delivery
  is usable for F2 ONLY when replay state is installed in the actual backend
  process P2 (§13.2).
- `swarm_os/brain.py` (262-285) and `organism_console/core/repair_engine.py`
  (656) call `render_active_lessons()` directly and remain known LIVE bypasses.
- A real F2 arm must be proven NOT to reach those paths, and the future
  real-execution authorization must retain that prohibition. F1's current
  non-reachability is NOT proof of F2 non-reachability.

### 13.9 Candidate execution architectures (factual, unranked)

> HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY AMENDMENT:
> the candidate table below is retained as a historical design record. It is
> not open authority. The selected F2 HTTP execution graph is exactly
> `P0 → P1 → P2` (see §13.1 as amended). No candidate reopens the graph choice.

| Candidate | Process graph (P0→P1→P2) | Replay owner | Frozen propagation | Notes |
|---|---|---|---|---|
| A. F2 worker directly executes the well-formed governed task | P0→P1(=P2) | P1/P2 fused | manifest loaded in-place by the fused worker | simplest correct model; requires the worker to both verify and drive a model request itself |
| B. Worker invokes existing `run_repair_task.py` machinery | P0→P1→`run_repair_task` (spawns backend P2 + CLI) | P2 backend (env-installed) | env `SWARM_F2_*` to backend | uses F1 lifecycle; reuse must be named in authorization; F1 auth not F2 auth |
| C. Worker reuses `_reset_instance` / `start_backend_fresh` / `wait_for_backend` / `_attempt_once` independently | P0→P1→(backend P2 + CLI) | P2 backend (env-installed) | env to P2 | same as B but ad-hoc composition; requires naming each primitive |
| D. New thin F2-specific adapter owns the orchestration→F1-runtime boundary | P0→P1→adapter→P2 | adapter installs into P2 | adapter propagates manifest to P2 | cleanest isolation of F2 logic from F1; new code requiring its own authorization |
| E. Another existing repository mechanism | TBD by forensic inspection | — | — | none identified in this audit beyond the above |

No rank/winner is assigned. The repository evidence fully specifies the required
**process-ownership model** (§13.1-13.4) but does NOT decide which candidate
implements it. That selection is a **design decision requiring future authorized
design/authorization**.

### 13.10 Remaining unresolved design questions

1. **backend-per-arm vs another execution-process ownership model**
   (fused P1==P2 vs separate backend P2): UNRESOLVED — REQUIRES FUTURE
   AUTHORIZED DESIGN DECISION.
2. Whether `run_repair_task.py` is merely a reusable lifecycle wrapper or the
   intended F2 execution boundary: UNRESOLVED — REQUIRES FUTURE AUTHORIZED
   DESIGN DECISION.
3. Exact mechanism by which the frozen manifest is propagated to P2 env:
   specified here as `SWARM_F2_REPLAY` + `SWARM_F2_MANIFEST_PATH` (existing
   contract); remaining detail (which process sets them) is an implementation
   decision. PARTIAL.
4. Whether the F2 receipt wraps F1 evidence or joins with it: UNRESOLVED —
   REQUIRES FUTURE AUTHORIZED DESIGN DECISION (the six-layer model in §13.6
   specifies the join keys, not the container).
5. How first-edit/task endpoint evidence enters the F2 receipt (F0 §5):
   defined at layer E (producer P2), but the exact source (backend trajectory vs
   CLI result) is UNRESOLVED — REQUIRES FUTURE AUTHORIZED DESIGN DECISION.
6. Exact recording of backend/CLI process identity: schema fields exist
   (layer B/C); the concrete producer wiring is UNRESOLVED — REQUIRES FUTURE
   AUTHORIZED DESIGN DECISION.

---

## 14. Real-Execution Architecture Selection — Design Decision (2026-09-29)

### 14.1 Selected architecture

This design specification selects:

**D — F2-specific thin execution adapter**

as the concrete architecture for the future F2 worker-execution implementation.

This is a **DESIGN DECISION ONLY**. It does NOT authorize implementation. The
adapter, any changes to `f2_arm_worker.py` / `f2_arm_orchestrator.py`, and any
use of the F1 machinery named below remain future-authorized work (§14.9).

### 14.2 Factual basis for the selection

The selection is based on repository architecture and boundary evidence, not on
preference or ranking:

1. The actual model-facing delivery process is the backend, designated **P2**
   (see §13); P2 runs `stream_runner` / `agent_service_v2` and is where
   `get_delivery_artifact()` must be authoritative.
2. F2 requires explicit propagation of `SWARM_F2_REPLAY=1`,
   `SWARM_F2_MANIFEST_PATH=<verified frozen manifest>`, and F2 arm / rollout
   identity into P2's environment (§13.2, §13.5), because F2 replay state is
   process-local.
3. `f1_infra.start_backend_fresh()` already provides a reusable fresh-backend
   lifecycle (f1_infra.py:546-608) — a viable mechanism to obtain a fresh P2.
4. `f1_infra.wait_for_backend()` already provides the backend health/lifecycle
   gate (f1_infra.py:611-688).
5. `cli_baseline_swe._reset_instance` provides reusable workspace-reset
   mechanics.
6. `run_curriculum._attempt_once` provides reusable CLI task-process mechanics
   (spawns `python -m organism_console --json`). [2026-09-29 F2 AUTHORITY
   AMENDMENT: `_attempt_once` is NOT part of the selected `P0 → P1 → P2` HTTP
   graph; future CLI-shaped use requires separate authorization.]
7. Existing `stream_runner` / `agent_service_v2` provide the replay-gated
   model-facing delivery seam (§13.8).
8. A dedicated F2 adapter gives the F2 execution contract one explicit boundary
   without redefining F1's scientific authorization (F1 auth is F1-only).
9. The adapter can explicitly own F2-specific environment propagation, arm
   identity, process/evidence collection, and receipt integration while reusing
   the existing F1 mechanics underneath.
10. The adapter MUST NOT change the semantics of the existing F1 machinery merely
    to support F2.

This is not a claim that D is "better" than other candidates; it is the
architecture selected by this design specification because it provides the
explicit F2-to-runtime boundary required by §13 while preserving the existing F1
runtime machinery as reusable implementation components.

### 14.3 Concrete future process graph (D)

```
P0 — F2 orchestrator
↓
P1 — F2 arm worker / controller
↓
F2 execution adapter            (qwen_train/f2_execution_adapter.py, IMPLEMENTED)
↓
fresh backend P2
├─ P2 environment contains:
│    SWARM_F2_REPLAY=1
│    SWARM_F2_MANIFEST_PATH=<verified manifest>
│    F2 arm / rollout identity
│
├─ main.py installs verified replay   (install_verified_replay_from_env)
└─ _f2_state becomes authoritative in P2
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
F2 adapter collects execution evidence
↓
F2 receipt
```

> HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY AMENDMENT:
> the prior graph node "CLI / task process P3 (via `_attempt_once` mechanics)"
> (previously shown between the adapter and P2) is superseded. Under the
> amendment the selected graph is exactly `P0 → P1 → P2`: P1 drives P2 directly
> over loopback HTTP/SSE (`127.0.0.1`); P3 is NOT required for this graph.

The exact Python call structure *inside* the future adapter is implementation
work and is not specified here.

### 14.4 Replay propagation path (D)

1. P1 (worker) verifies the manifest and validates arm identity.
2. The F2 execution adapter receives the verified arm/manifest reference.
3. The adapter starts a fresh backend (P2) whose environment explicitly contains
   `SWARM_F2_REPLAY=1` and `SWARM_F2_MANIFEST_PATH=<content-addressed path>`,
   plus F2 arm / rollout identity.
4. P2's `main.py` lifespan calls `install_verified_replay_from_env()` →
   `_f2_state` = FROZEN_REPLAY in P2 (main.py:522-541).
5. P1 (in the adapter) drives P2 directly over loopback HTTP/SSE (`127.0.0.1`).
   P3 is not required and carries no replay/delivery semantics; delivery occurs
   in P2. [HISTORICAL — NON-NORMATIVE — SUPERSEDED BY 2026-09-29 F2 AUTHORITY
   AMENDMENT: the prior wording "The adapter (or P1) drives a CLI/task process
   (P3) which talks to P2 over HTTP/SSE" is superseded.]

### 14.5 Replay invariant (mandatory, P2)

Installing replay in P1 is not sufficient. For the actual F2 arm:

- **P2 MUST independently install and verify the frozen replay state.**
- The authoritative delivery check occurs in P2.
- If `is_replay_active() != TRUE` at the actual model-facing delivery point:
  **ABORT THE ARM.**
- **Never** fall through to `render_active_lessons()`.
- **Never** silently continue.

### 14.6 F1 machinery explicitly designated reusable (technical, not authorization)

The following are **reusable technical machinery; NOT F2 authorization**:

- `cli_baseline_swe._reset_instance` (fresh workspace reset)
- `f1_infra.start_backend_fresh` (fresh backend lifecycle)
- `f1_infra.wait_for_backend` (backend health/lifecycle gate)
- `run_curriculum._attempt_once` (CLI task-process mechanics; NOT part of the
  selected `P0 → P1 → P2` HTTP graph — future CLI-shaped use requires separate
  authorization)
- existing `stream_runner` / `agent_service_v2` replay-gated delivery

The future worker-execution authorization MUST explicitly authorize their use. No
modification to those F1 files is authorized or implied.

### 14.7 Adapter contract (`qwen_train/f2_execution_adapter.py`)

**IMPLEMENTED** under `EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md`;
`f2_arm_worker.py` imports and instantiates `F2ExecutionAdapter` on the production
arm path. This section records the contract it is responsible for:

1. receiving a verified F2 arm/manifest from the worker;
2. preserving arm identity;
3. establishing a fresh execution backend;
4. explicitly propagating the F2 replay environment into P2;
5. verifying and recording the serving backend process identity —
   `p2_serving_pid` / `p2_serving_start_time` (process owning the listening
   socket serving the F2 task); `p2_launcher_pid`/`p2_launcher_start_time`,
   `p2_evidence_pid`, and `command_identity` recorded separately; expected-PID
   equality is NOT a requirement (2026-09-29 F2 AUTHORITY AMENDMENT);
6. waiting for backend readiness;
7. establishing task delivery to P2 directly over loopback HTTP/SSE
   (`127.0.0.1`); a CLI-shaped task process is used only if a task is routed
   through CLI-shaped machinery (not required by the `P0 → P1 → P2` graph);
8. preserving rollout/trajectory identity;
9. proving replay is active in P2;
10. preventing LIVE fallback;
11. collecting backend/CLI/task/model/outcome evidence;
12. returning that evidence to the F2 worker;
13. binding the evidence to the frozen manifest (recording the committed F0
    delivery identity: `lesson_block_hash`, `final_prompt_hash`,
    `delivery_timestamp`, backend PID, backend start time — F0 §4/§7/§9; this
    does NOT add a delivery-time hash-mismatch ABORT requirement);
14. contributing the required evidence layers to the F2 receipt;
15. propagating failures and timeouts as fail-closed failures.

### 14.8 Bypass contract (preserved)

- `repair_engine.get_similar_lessons` is reached through `/heal` slash-command
  paths (`_commands_ai.py:204`), not the worker/CLI prompt path.
- `brain.py` live lesson rendering is on simulation/scenario paths
  (`swarm_os/brain.py:270,280`).
- Neither is on the existing worker/CLI prompt execution path.
- This does NOT constitute proof about arbitrary future code.
- The future adapter authorization MUST explicitly prohibit invoking those paths,
  and the implementation must test the actual F2 process graph.

### 14.9 Receipt / evidence boundary

Keep the six evidence layers:

- **A. F2 controller/worker** — produced by P1 / F2 primitives.
- **B. execution backend** — produced/collected by the adapter from P2.
- **C. CLI/task** — optional; produced/collected by the adapter from a
  CLI-shaped task process only when a task is routed through CLI-shaped
  machinery; for the `P0 → P1 → P2` HTTP graph it is absent (P1 drives P2
  directly).
- **D. model/provider** — produced/collected by the adapter from P2.
- **E. task endpoint/outcome** — produced/collected by the adapter.
- **F. frozen-delivery identity** — provided by F2 primitives (manifest
  identity, hashes, delivery identity).

The adapter is responsible for collecting/joining B–E and returning them to the
F2 controller, while F2 primitives provide the frozen identity layer (F). The
receipt schema itself is NOT modified by this amendment.

### 14.10 Future authorization boundary

The architecture selection does NOT authorize:

- creation of `qwen_train/f2_execution_adapter.py` — **since granted**, see
  `EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md` §18;
- changes to `f2_arm_worker.py` / `f2_arm_orchestrator.py` — **since granted** for
  the authorized delivery-seam and bundle-emission scope;
- use of `_reset_instance`;
- use of `start_backend_fresh`;
- use of `wait_for_backend`;
- use of `_attempt_once`;
- actual model execution;
- F2 task execution;
- N=2;
- Experiment J execution;
- evaluation;
- certification;
- promotion;
- QLoRA;
- frontend work;
- deployment;
- unrelated infrastructure.

All of those require a later explicit authorization.

[SUPERSEDED-BY-AMENDMENT consistency note — 2026-09-29 F2 AUTHORITY AMENDMENT]
The selected F2 HTTP execution graph is exactly `P0 → P1 → P2` and does NOT use
`_attempt_once`. If a future task is routed through CLI-shaped machinery, that
use requires a separate authorization; the prior framing of `_attempt_once` as a
dependency of this graph is superseded (HISTORICAL — NON-NORMATIVE).

---

*End of design contract. Implementation requires separate authorization. Nothing
herein changes F0, F1, or any existing implementation.*