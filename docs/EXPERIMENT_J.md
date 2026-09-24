# Experiment J — Lesson-Delivery Causal Experiment (F0 Scientific Freeze)

**Status:** F0 SCIENTIFIC DESIGN FROZEN — READY FOR ENGINEERING EXECUTION
**Date:** 2026-09-24
**Authoritative Specification:** This document is the single source of truth for Experiment J's scientific design. Implementation requirements are explicitly excluded.

---

## 1. Research Question

> **Does delivery and utilization of a genuine, lawfully promoted lesson L change a fresh worker's observable first-relevant-edit behavior compared to the exact same frozen treatment artifact with L removed?**

- **Explicit** — specifies "genuine, lawfully promoted lesson L" and "fresh worker"
- **Falsifiable** — T vs X comparison on first-relevant-edit behavior
- **Specifically tests lesson utilization** — not retrieval, not learning, not task success
- **Distinguishes lesson-delivery from task success** — primary endpoint is behavioral (first edit), not outcome

---

## 2. Causal Model / DAG

```
failure
  → learning/governance (process_failure → evaluate_candidate → promote)
  → genuine ACTIVE lesson L (Qdrant ActiveLessons + promotion_proof + HMAC receipt)
  → retrieval/selection (LessonManager.render_active_lessons: get_all → filter → sort → token-budget)
  → frozen treatment artifact (active_block string from render_active_lessons)
  → T/X delivery (frozen artifact replayed via contextvar bypass)
  → worker behavior (first edit-type filesystem action in first k steps)
  → secondary task outcome (not primary)
```

**Layer separation (each frozen at its designated stage):**

| Layer | Mechanism | Frozen At |
|-------|-----------|-----------|
| Formation | `process_failure` → `evaluate_candidate` | F2 (L produced) |
| Persistence | `promote` → `LessonManager.store` → Qdrant | F2 |
| Retrieval/Selection | `render_active_lessons` (get_all → sort → budget) | F2 (captured in frozen artifact) |
| Delivery | `system_prompt += active_block` | F2 (captured as `lesson_block_hash`) |
| Utilization | Worker's tool decisions | F2 (measured via trajectory) |
| Behavior Change | First edit-type filesystem action | **Primary endpoint** |
| Task Outcome | Test pass/fail | **SECONDARY** (not primary) |

**Primary causal claim stops at behavior change (first relevant edit).** Task outcome is explicitly secondary.

---

## 3. Arm Definitions

### T Arm (Treatment)
- **Exact frozen treatment artifact containing L** — the exact `active_block` string produced by `LessonManager.render_active_lessons()` at F2, including lesson L.

### X Arm (Control: L Removed)
- **Exact same frozen treatment artifact with ONLY L removed** — L's rule text deleted from the numbered list.
- **Explicitly prohibited for X:**
  - Reretrieval (no call to `render_active_lessons`)
  - Reranking (frozen artifact is pre-ordered, pre-budgeted)
  - Refill (token budget not recalculated; X artifact is strictly shorter)
  - Backfill (next-ranked lesson does NOT move up; gap remains)
  - Substitution (no decoy lesson inserted; L's position left empty)

### C0 Arm (No-Lesson Control)
- Fresh worker, same task, **no treatment artifact delivered** (empty `active_block`).

---

## 4. Treatment Artifact Definition

**Treatment artifact = frozen output of `LessonManager.render_active_lessons()` at F2.**

**Scientific definition (not implementation):**

| Identity Type | Elements | Purpose |
|---|---|---|
| **Treatment Identity** | 1. Ordered lesson IDs (e.g., `[id_A, id_B, id_L, id_C]`)<br>2. Ordered lesson content hashes (SHA256 of each `rule`)<br>3. Rendered `active_block` string (exact text delivered)<br>4. `treatment_set_hash` = SHA256(`active_block`) | Defines WHAT the treatment is; identical for T and X except L removal |
| **Delivery Identity** | 5. `lesson_block_hash` = SHA256(rendered `active_block`)<br>6. `final_prompt_hash` = SHA256(`system_prompt + active_block`)<br>7. `delivery_timestamp` (pre-first-decision) | Proves WHAT was delivered to THIS worker at THIS moment |
| **Provenance/Audit Identity** | 8. `arm` ∈ {T, X, C0}<br>9. `rollout_id` (harness)<br>10. `trajectory_run_id` (backend)<br>11. `ordered_selected_lesson_ids` (from retrieval)<br>12. `ordered_lesson_hashes` (from retrieval)<br>13. `treatment_set_hash` | Enables reconstruction and audit; links to promotion provenance |

**Critical distinction:** Treatment identity ≠ rendered text alone. Two treatments with identical text but different provenance (e.g., different lesson IDs, different rollout) are different treatments.

---

## 5. Primary Endpoint

> **First edit-type filesystem action (`filesystem` with `operation ∈ {write, patch, edit, create}`) within the first `k` decision steps whose `path` belongs to a predefined frozen `relevant_file_set`.**

**Verification criteria:**
- ✅ Observable — `tool_calls` in trajectory records `function_name="filesystem"`, `arguments.operation`, `arguments.path`
- ✅ Measurable from trajectory — ATIF step records contain full tool call + args
- ✅ Defined before confirmatory runs — `relevant_file_set` frozen before pilot; `k` frozen at F1
- ✅ Behavior before delivery NOT counted — Rediscovery rule (Section 6)
- ✅ Rediscovery classified separately — See Section 6
- ✅ `k` frozen before confirmatory — F1 freezes `k` per predefined rule

**`k` selection rule (pre-registered, frozen at F1):**
> `k` is selected during F1 (no-lesson pilot) as the 95th percentile of steps-to-first-edit across pilot runs, capped at `[8, 12]`. This rule is frozen at F1 and cannot change after confirmatory data.

---

## 6. Rediscovery Rule

> If a worker performs the target edit behavior **at a step timestamp strictly earlier than the `delivery_timestamp`** (the moment the frozen treatment artifact was concatenated into the system prompt), that behavior is:
> 1. **Recorded** in the trajectory with a `rediscovery` flag
> 2. **Classified** as pre-delivery behavior (rediscovery)
> 3. **Excluded** from the primary T/X causal analysis
> 4. **Handled** as missing data for that arm's primary endpoint (the run contributes to contamination diagnostics, not causal effect)

**Unambiguous:** Uses strict timestamp ordering (`step.timestamp < delivery_timestamp`). No behavioral inference required.

---

## 7. Fresh-Worker Definition

**Scientific definition (not implementation mechanism):**

| Requirement | Verification |
|---|---|
| New `rollout_id` | UUID4 generated per harness invocation |
| New `trajectory_run_id` | Backend generates per `step_agent_stream` invocation |
| New process | Fresh Python process per arm |
| Empty session state | `.session.json` deleted; no history passed to backend |
| No checkpoint restore | No `resume=` parameter passed |
| Semantic decision cache disabled | `SWARM_SEMANTIC_CACHE=0` (default OFF) |
| Model identity recorded | `model_name` in trajectory; GGUF SHA256 verified at startup |
| LoRA identity recorded | GGUF contains LoRA; SHA256 verified |
| Prompt hash recorded | SHA256 of `system_prompt + active_block` captured as `final_prompt_hash` |
| Treatment hash recorded | SHA256 of `active_block` captured as `lesson_block_hash` |
| Process/session/checkpoint evidence retained | Trajectory records, rollout logs, process tree |

**Distinction maintained:** Scientific requirement defined; implementation mechanism (fresh process spawn, env vars, file deletion) is downstream engineering.

---

## 8. Contamination Framework

| Factor | Classification | Rationale |
|---|---|---|
| Session state (`.session.json`) | REQUIRED CONTROL | Carries history into fresh worker; must be deleted |
| Checkpoint/resume | REQUIRED CONTROL | `resume=` parameter must be absent |
| Process reuse | REQUIRED CONTROL | Fresh Python process per arm |
| Qdrant lesson state | REQUIRED RECORD | Log `ActiveLessons` state hash per arm; **NOT required control for T/X** (frozen artifact bypasses retrieval) |
| Workspace/code state | REQUIRED CONTROL | `git reset --hard` per arm; `git status --porcelain` clean |
| Model identity/hash | REQUIRED RECORD | GGUF SHA256 logged at startup |
| LoRA identity/hash | REQUIRED RECORD | Same as model |
| Rollout identity | REQUIRED RECORD | `rollout_id` per arm |
| Trajectory identity | REQUIRED RECORD | `trajectory_run_id` per arm |
| Experiment environment | REQUIRED RECORD | `SWARM_EXPERIMENT_J_ARM`, `SWARM_RECEIPT_KEY`, etc. |
| Semantic cache | REQUIRED CONTROL | `SWARM_SEMANTIC_CACHE=0` (default OFF) |
| Web activity/policy | REQUIRED RECORD | Log `web_search`/`web_fetch` queries if enabled |
| Backend/process identity | REQUIRED RECORD | Backend PID, start time logged |
| Manual intervention | REQUIRED CONTROL | No human input during arm execution |

**Qdrant snapshot/restore explicitly NOT required for T/X** — frozen artifact bypasses retrieval.

---

## 9. Provenance

**Minimum provenance for each worker run:**

| Field | Source |
|---|---|
| `arm` | Test harness assignment |
| `rollout_id` | Harness-generated UUID4 |
| `trajectory_run_id` | Backend `run_id` |
| `ordered_selected_lesson_ids` | `LessonManager.get_all()` → sorted by `(effectiveness desc, version desc)` |
| `ordered_lesson_hashes` | SHA256 of each selected lesson's `rule` |
| `treatment_set_hash` | SHA256 of frozen `active_block` |
| `lesson_block_hash` | SHA256 of `active_block` (delivery artifact) |
| `final_prompt_hash` | SHA256 of `system_prompt + active_block` |
| `delivery_timestamp` | `time.time()` at `system_prompt += active_block` |

**Critical:** Treatment identity **cannot** be reconstructed from rendered text alone if provenance differs — two treatments with identical `active_block` but different `ordered_lesson_hashes` are distinguishable via provenance.

---

## 10. F0 / F1 / F2 Freeze Boundaries

| Stage | Action | Frozen At |
|---|---|---|
| **F0** | Scientific design freeze (this document) | Now |
| **C0** | Pre-learning control snapshot (Qdrant `ActiveLessons` empty) | After F0, before pilot |
| **No-lesson pilot** | Measure variance, timing; inform `k`/`n` per predefined rule | After C0 |
| **F1** | Freeze `k`, `n`, practical-effect criterion per protocol | After pilot |
| **Learning event** | Genuine L produced via real learning/governance path | After F1 |
| **F2** | Freeze genuine L, exact ordered treatment material, relevant state | After L verified |

**Critical gate:** F2 **cannot occur** before genuine L is lawfully produced (promotion → ACTIVE with full provenance) and verified.

---

## 11. Practical Effect / Statistical Plan (Frozen at F1)

| Element | Specification |
|---|---|
| Primary contrast | T vs X (difference in first-edit behavior rate) |
| Unit of analysis | Rollout (one trajectory per arm per task) |
| Success coding | Binary: target edit occurred within first `k` steps on a `relevant_file_set` path |
| Missing/invalid handling | Pre-registered exclusion rules (Section 12); excluded runs do not contribute to denominator |
| Practical effect criterion | Risk difference ≥ δ (pre-registered at F1, e.g., δ = 0.15) |
| Statistical test | Pre-registered at F1 (e.g., Fisher's exact for binary; bootstrap CI for risk difference) |
| Pairing | T/X paired by task + rollout seed; C0 independent |

**Protocol specifies WHAT must be frozen at F1; exact values (δ, α, test) frozen at F1.**

---

## 12. Invalid / Contaminated Run Rules (Pre-Registered)

**Exclusion rules (cannot be chosen post-hoc):**

| Condition | Rule |
|---|---|
| Trigger failure (worker crash) | EXCLUDED — infrastructure failure |
| Worker timeout (`k` steps exceeded without edit) | EXCLUDED — infrastructure failure |
| Infrastructure failure (backend down, Qdrant down) | EXCLUDED — infrastructure failure |
| Malformed trajectory (missing `tool_calls`, missing `run_id`) | EXCLUDED — data integrity |
| Missing treatment artifact (`active_block` empty) | EXCLUDED — treatment not delivered |
| Wrong lesson delivered (hash mismatch) | EXCLUDED — contamination |
| Wrong arm assignment | EXCLUDED — contamination |
| Wrong prompt hash (delivery integrity) | EXCLUDED — contamination |
| Wrong model/LoRA (hash mismatch) | EXCLUDED — contamination |
| Wrong session/checkpoint state | EXCLUDED — contamination |
| Manual intervention detected | EXCLUDED — contamination |
| Web-policy violation (if applicable) | EXCLUDED — contamination |
| Duplicate `rollout_id`/`run_id` | EXCLUDED — identity collision |

**Enforcement:** Rules are code-enforced in test harness; exclusion decision logged with reason; cannot be overridden after outcomes observed.

---

## 13. Secondary Analyses

| Analysis | Status | Constraint |
|---|---|---|
| Lesson selection/retrieval diagnostics | SECONDARY | Cannot replace T/X |
| Delivery confirmation (hash match) | SECONDARY | Cannot replace T/X |
| Utilization evidence (step timing) | SECONDARY | Cannot replace T/X |
| Task success (test pass/fail) | SECONDARY | Cannot replace T/X |
| Boundary-task validation | SECONDARY | Cannot replace T/X |
| D arm | **DROPPED** | Not part of primary contrast |

**Explicit constraint:** No secondary analysis can silently replace the primary T/X contrast.

---

## 14. Active Lesson Dependency

> **A genuine ACTIVE lesson L is required before F2. However, the absence of currently recovered L does NOT make the scientific design itself undefined. It makes confirmatory material unavailable.**

**Current experiment-material status:** No verified ACTIVE lesson L has currently been recovered from the audited repository/runtime artifacts. This is an experiment-material limitation, **NOT** a scientific-design ambiguity.

---

## 15. Qdrant Role Clarification

**Qdrant snapshot/restore is NOT a primary T/X causal requirement** because T/X replay frozen treatment artifacts rather than invoking live retrieval. Qdrant is relevant only for:

- Producing/persisting the genuine lesson L at F2 (via `LessonManager.store()`)
- Recording the provenance state hash for audit

Qdrant state control is an operational requirement for C0/learning-event isolation, not a T/X causal-control requirement.

---

## 15. D Arm

**D arm is dropped from the primary design.** No decoy lesson, no substitution. The primary causal contrast is T vs X.

---

## 16. Change-Control Rule

**After F0 freeze, implementation may improve mechanics but may NOT alter without a new scientific review:**

| Protected Element | Change Requires New Scientific Review |
|---|---|
| Research question | ✅ |
| Causal contrast (T vs X) | ✅ |
| T/X definitions | ✅ |
| Treatment artifact identity | ✅ |
| Primary endpoint | ✅ |
| Rediscovery rule | ✅ |
| Fresh-worker definition | ✅ |
| Contamination rules | ✅ |
| Primary interpretation | ✅ |
| Confirmatory exclusion rules | ✅ |

---

## 16. Later Execution Sequence

```
C0 (pre-learning snapshot, Qdrant ActiveLessons empty)
  → no-lesson pilot (calibration, inform k/n)
  → F1 (freeze k, n, practical-effect, statistical parameters)
  → genuine learning event (failure → evidence → evaluation → HMAC receipt → promotion → ACTIVE)
  → F2 (freeze genuine L, exact ordered treatment material, relevant state)
  → T/X/C0 (frozen treatment replay)
  → analysis
  → Twine
  → Click
  → Pyfakefs
  → Sandbox Bounds
  → 25-rollout measurement LAST
```

---

## 17. Current Experiment-Material Status

> **No verified ACTIVE lesson L has currently been recovered from the audited repository/runtime artifacts.**

This is an **experiment-material limitation**, **NOT** a scientific-design ambiguity. The scientific design is complete and frozen. The engineering work to produce a lawful ACTIVE lesson L through the genuine promotion pathway is the next execution step.

---

## 18. Implementation Boundary

**F0 does NOT require implementation of (engineering work after F0):**

- `exclude_ids` parameter in `render_active_lessons()`
- Dynamic retrieval suppression
- Qdrant snapshot/restore for T/X
- Replay code (contextvar bypass)
- Trajectory schema changes
- Delivery instrumentation (hashes, timestamps)
- Fresh-process harness
- Promotion fixture / lawful lesson generation
- Promotion test fixes

These are execution engineering tasks after F0 freeze.

---

**END OF F0 SCIENTIFIC SPECIFICATION**

This document is the authoritative Experiment J F0 scientific design. All implementation details, instrumentation, fixtures, and operational procedures are explicitly excluded and belong to the post-F0 engineering phase.