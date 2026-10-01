# Experiment J — State & Plan (Updated 2026-09-28)

Handoff/state doc. If you are resuming, **read this first**, then
`docs/EXPERIMENT_J.md`, `docs/EXPERIMENT_J_F1_AUTHORIZATION.md`, `docs/EXPERIMENTS.md`.

---

## 0. The Question We Are Answering

> **Does delivery and utilization of a genuine, lawfully promoted lesson L change a fresh worker's observable first-relevant-edit behavior compared to the exact same frozen treatment artifact with L removed?**

This is the frozen F0 research question from `docs/EXPERIMENT_J.md` §1. The learner is the Swarm OS system (prompt repair, memory, governance) — NOT Qwen/robs4b model weights.

**Answer so far:** Experiment J F1 is CLOSED (20/20 protocol observations completed). F1 established the no-lesson baseline (k, n, practical-effect criterion frozen). No verified ACTIVE lesson L exists yet. The next scientific step is F2: produce a genuine ACTIVE lesson L through the real governed learning pathway.

---

## 1. Where Experiment J Stands (Live State)

| Item | State | Evidence |
|---|---|---|
| **Experiment J F0 (scientific design)** | **FROZEN** | `docs/EXPERIMENT_J.md` (commit `20a1989b`) |
| **Experiment J F1 (pilot)** | **CLOSED** | 20/20 protocol observations completed; 10 valid (endpoint @ ATIF Step 4), 10 infrastructure-invalid; `k`, `n`, practical-effect frozen per `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` |
| **Post-F1 Governance Baseline** | **ESTABLISHED 2026-09-27** | `SWARM_AUTONOMY=0`, `SWARM_GENETIC_MUTATION=0`, `SWARM_EVAL_TICK=0`, `SWARM_EXPERIMENT_J_ARM=0`, `SWARM_F1_NO_WEB_TOOLS=0`, `SWARM_EVOLUTION=0`; verified in backend PID 20332 |
| **Experiment J F2 (genuine learning event)** | **PENDING** | No verified ACTIVE lesson L exists. Prerequisites: `SWARM_RECEIPT_KEY` provisioned (missing, fail-closed), backend/Qdrant healthy, `ActiveLessons` empty (C0). See `docs/EXPERIMENT_J.md` §17, §18. |
| **F1 Harness Machinery** | **IMPLEMENTED** | Evaluator separation, pre-flight checks, behavioral prompt, fresh-clone gate, `SWARM_F1_NO_WEB_TOOLS=1` |
| **F2 Engineering Work** | **PARTIAL** | Promotion fixture, delivery instrumentation, fresh-process harness, replay code: IMPLEMENTED (2026-09-29 baseline `7165cb20`). `exclude_ids`: IMPLEMENTED (2026-09-30, relevance-ordered governed seam). Rediscovery classification and invariant-F mutation coverage: IMPLEMENTED. Certification gate: NOT IMPLEMENTED. See `docs/EXPERIMENT_J.md` §18. |

**F1 Protocol Observations: 20/20 COMPLETED** (reconciled 2026-09-26 from `qwen_train/results/f1_obs*.jsonl`):
- 10 valid observations (all reaching qualifying first-edit endpoint @ ATIF Step 4)
- 10 infrastructure-invalid observations
- Observation 1: infrastructure-invalid (web-tool drift)
- Observation 2: qualifying first-edit endpoint @ ATIF Step 4 (`filesystem.patch` on `swarm_os/lib/paths.py`), patch rejected by read-before-write guard, repair correctness UNKNOWN, capability credit +0/-0
- Observations 7-11,13-16 (UUID-named): VALID, endpoint @ ATIF Step 4
- Observations 18-20: infrastructure-invalid (timeout, rejection-loop stall)
- Supplementary 21st execution excluded from official 20-observation dataset

**F1-OP Frozen Parameters (per `docs/EXPERIMENT_J_F1_AUTHORIZATION.md`):**
- F1-OP-001: 20 independent no-lesson pilot runs
- F1-OP-002: Pilot task = `repair_task1` / `sandbox_bounds` @ base commit `45d9f619`
- F1-OP-003: `relevant_file_set` = `{"swarm_os/lib/paths.py"}` (test file excluded, immutable)
- F1-OP-004a: Pilot observation horizon = 12 ATIF decision steps (inclusive), right-censor at step 12
- F1-OP-004b: Censored observations contribute value 12 to P95 set; all 20 runs contribute
- F1-OP-004: P95 = nearest-rank empirical P95, n=20, rank=19; `k = min(12, max(8, P95))`

---

## 2. Experiment J F2 — Next Authorized Step

**Objective:** Produce a genuine ACTIVE lesson L through the real governed learning pathway, then freeze F2.

**Prerequisites (from `docs/EXPERIMENT_J.md` §17, §18 and `docs/LEARNING_EXPERIMENT_STATE.md` §1):**
| Prerequisite | Status | Notes |
|---|---|---|
| `SWARM_RECEIPT_KEY` provisioned in `.env` | ✅ PROVISIONED | Operator-provisioned 2026-09-30 per §10.3 item 9. Required for HMAC receipt signing; promotion remains fail-closed without it |
| Backend healthy (`/readyz`=ready, `/health`=ok) | ✅ Verified | Backend PID 20332, Qdrant healthy |
| Qdrant `ActiveLessons` empty (C0) | ✅ Verified | Post-F1 baseline confirmed |
| `SWARM_HARNESS_KEY=dev` in `.env` | ✅ Set | For rollout provenance |
| `SWARM_AUTONOMY=0`, `SWARM_GENETIC_MUTATION=0`, `SWARM_EVAL_TICK=0`, `SWARM_EXPERIMENT_J_ARM=0`, `SWARM_F1_NO_WEB_TOOLS=0`, `SWARM_EVOLUTION=0` | ✅ Verified | Post-F1 governance baseline |

**Engineering Work Required for F2 (per `docs/EXPERIMENT_J.md` §18):**
- Promotion fixture / lawful lesson generation
- `exclude_ids` parameter in `LessonManager.render_active_lessons()`
- Dynamic retrieval suppression for T/X replay
- Replay code (contextvar bypass for frozen artifact delivery)
- Delivery instrumentation (hashes, timestamps: `lesson_block_hash`, `final_prompt_hash`, `delivery_timestamp`)
- Fresh-process harness (distinct `rollout_id`, `trajectory_run_id`, clean session/checkpoint state)
- Qdrant snapshot/restore for C0 isolation (not required for T/X per F0 §15)

**SINGLE NEXT STEP AFTER THIS DOCUMENTATION UPDATE:**
Execute the engineering work to enable a genuine governed learning event that produces an ACTIVE lesson L, then capture the frozen F2 treatment artifact via `LessonManager.render_active_lessons()`. This is NOT a single script execution — it requires implementing the missing F2 infrastructure listed above.

### 11. Final success condition
Report `CONTAMINATION FILTER: PASS` only if: contaminated hashes identified from reliable
evidence; denylist explicit + inspectable; append-only; denylisted commits cannot enter
usable train/eval candidates; exclusions auditable; existing non-contaminated harvesting
preserved; all contamination tests pass; existing harvester tests pass; syntax/import checks
pass; usable output contains zero denylisted hashes. Else `CONTAMINATION FILTER: NOT PASS`
+ exactly what is unresolved.

### Report format
```
CONTAMINATION FILTER: PASS | NOT PASS
Denylisted commits: - <hash>  —  <reason>
Counts: Before filtering X / Excluded Y / Usable Z
Leakage: denylisted hashes in usable candidates: 0
Tests: existing harvester PASS/FAIL · contamination PASS/FAIL · syntax/import PASS/FAIL
Files changed: <list>     Scope: contamination filtering only
```
**Do not proceed to train/eval partitioning in this task. Next phase begins only after
`CONTAMINATION FILTER: PASS`.**

### Contamination hashes identified (evidence: `git log --since="24 hours ago"`, FIX:/HEAL:)
Session's own `FIX:` commits → harvesting them tests the coder against bugs it just fixed
(self-referential). **Verify each still exists before denylisting.**

```
75ec5bdfd9224a4508fa12f128440d59e28ff12f  FIX: restore parenthesized except (approval_registry)
1ee3898de615e0f71720553fe9feca0347a2fb05  FIX: headless SWARM_NO_TOASTS gate + ablation grants
e85623231182ace0073f65f27d316951e1e77eb3  FIX: memory ablation samples approval-free tasks
eb7fc07ae2dd17b55bc95fb9812dd7bf2ac021c6  FIX: DeepSeek direct model name -> deepseek-flash
8c80a99159059e5423e2e02730843a2451e72c7b  FIX: update tests for intentional behavior changes
1f3fc12b8112c93f58759cfbbefd70465f1dcdb0  FIX: DangerRoom heavy-dir exclusion + stale sweep
```
Infrastructure `SERVICE:` commits (e.g. `852a3809` harvester, `71c14885` age-filter) are
**NOT** contamination — they are not harvested (harvester takes `FIX:`/`HEAL:` only).

---

## 3. Plan AFTER Item 1 (ordered — do NOT skip ahead)

1. **Contamination filter → PASS** (Item 1 above).
2. **Count / inspect clean real-bug candidates** (harvest yield; distribution).
3. **Group by FILE / MODULE.**
4. **Design train/eval split holding out whole files/modules** (harder to cheat than
   commit-level splits — commits 2 & 3 may be near-identical fixes to the same code).
5. **Verify zero file/module leakage** between train and eval.
6. **Freeze eval** 🔒.
7. **Run the CLI** and compare against the frozen eval.

Escalation hierarchy (only if we need *more* diversity after step 2):
Level 4 real bugs (seen repos) → Level 5 real bugs (**unseen repos**) →
Level 6 unseen repos + unseen mechanisms. **Hold out whole repos, never split within a repo.**
If needed, adopt SWE-bench rather than building an in-house multi-repo harvester.

---

## 4. Where the real-bug harvester is (built this session)

- `qwen_train/mine_fix_commits.py` — harvests `FIX:`/`HEAL:` commits (mirrors
  `qwen_train/mine_v6.py`) → materializes PARENT + FIX with **`git archive` into temp dirs**
  (never a worktree, never the live tree) → discovers related tests → runs pytest via JUnit XML
  → **FAIL_TO_PASS** = tests failing at parent, not failing at fix. Emits `repo_fix_tasks.jsonl`.
- `--min-age-hours` (default 24) skips the session's own recent commits (clean-signal guard);
  **after Item 1 this becomes a secondary diagnostic — the hash denylist is authoritative.**
- Tests: `tests/test_mine_fix_commits.py` (flip detected; non-flip rejected; extraction isolated).
- Harvest scale: **~400 eligible commits** (this repo, pre-tonight).

---

## 5. Operational traps (cost the session real time — read before running anything)

1. **Backend env is loaded at STARTUP.** `ANALYSIS_CLOUD_MODEL`, `SWARM_WRITE_ROOT`,
   `SWARM_MEMORY_INJECT` need a REAL backend restart; a stale process keeps the old value.
2. **Two `python -m uvicorn …` processes are NORMAL** (parent supervisor + child owning
   `:8000`). Killing the parent kills the backend. **Do not "clean up" the second one.**
   (Killing pid 291244 took the whole backend down and produced a run of phantom failures.)
3. **`/readyz` can time out while `/health` returns 200** — the readiness probe hits the model
   (background daemons hold the slot). `/health` 200 = backend up.
4. **A dead backend = phantom `cli_ok=False` rows.** Always filter `cli_ok == True`.
5. **DeepSeek model names (2026-09-14 rename):** direct = `deepseek-flash` / `deepseek-v4-pro`;
   `deepseek-chat`/`deepseek-reasoner` are DEAD. OpenRouter ids unchanged.
6. **DangerRoom copies:** excluded `data/run_snapshots` (was 17 GB), `storage/collections`
   (5.3 GB), `qwen_train` (3 GB) + a stale-sandbox sweep. Deleting `data/run_snapshots`
   (5 × 3.4 GB old files) freed ~17 GB. **Never copy those into a sandbox again.**
7. **Approvals fire desktop toasts** → set `SWARM_NO_TOASTS=1` for headless runs.
8. **`__pycache__` staleness**: broken vs fixed sources of equal byte-length need SEPARATE
   dirs (or `PYTHONDONTWRITEBYTECODE=1`) or a stale `.pyc` gives a false "unsound".

---

## 6. Commits pushed this session (origin/master)

```
1f3fc12b FIX: DangerRoom heavy-dir exclusion + stale-sandbox sweep (disk-fill guard)
71c14885 SERVICE: harvester skips recent self-commits (--min-age-hours)
852a3809 SERVICE: FIX:-commit task harvester (real fail->pass verifier, git-archive isolated)
0535f1bc SERVICE: add --concurrency to candidate-pool runner
8c80a991 FIX: update tests for intentional behavior changes
0ba0524a SERVICE: AGENTS.md session record 2026-09-14
eb7fc07a FIX: DeepSeek direct model name -> deepseek-flash
```

---

## 7. Key research grounding (verified)

- `arXiv:2608.04003` PAST-Bench — matched experience on/off + **pathway evidence**.
- `arXiv:2605.30621` Harness-Updating ≠ Benefit — benefit is non-monotonic in model
  capability; weak workers fail to ACTIVATE/FOLLOW learned artifacts.
- `arXiv:2603.10600` Trajectory-Informed Memory — strategy/recovery/optimization tips + provenance.
- `arXiv:2602.03219` TDScaling + `2026.findings-acl.768` — **diversity > quantity**.
- `arXiv:2608.13568` — tool use is a task-shaped learnable policy.

---

## 8. Experiment J F1 - infrastructure evidence and reusable lesson (2026-09-24)

Candidate robs4b training/memory lesson. Recording location: this experiment-state
doc - out of band from the F1 observation path. This is evidence + a generalized
lesson, NOT proof the model has learned anything yet.

### 8.1 Factual Experiment J F1 evidence (provenance: F1 preparation, no observation run)

1. The prior infrastructure-invalid F1 Run 1 involved a missing-file condition on
   `/Users/rober/Projects/swe_probe_work/f1_pilot/run_1_fresh/repo/agent_sdk_toolkit/workspace/bounds.rs`
2. That path was **NOT** a missing file in the fresh clone. The fresh clone was clean
   at commit `45d9f6192dd7b1c81c63f46d277798f19adb97ec` with **zero**
   `git status --porcelain` changes.
3. `agent_sdk_toolkit` did not exist anywhere in the fresh clone.
4. ATIF trajectory evidence:
   - step 3 = `web_search` for sandbox_bounds-related terms
   - step 4 = model-generated `filesystem read` of the nonexistent
     `agent_sdk_toolkit/workspace/bounds.rs` path
5. Conclusion: the `bounds.rs` condition was **model-generated tool-use drift**
   (the model constructed a plausible-but-nonexistent path after a web-search
   result), not harness-generated state, not fixture state, not stale repository
   state, and NOT evidence that the file should be created.
6. The normal (non-F1) backend was later verified NOT to provide F1 isolation:
   `/status` reported `workspace_root = C:\Users\rober\Projects\v-horseshoe-v2`
   with `write_covers_workspace = True` (sandbox = live project root, not the F1 clone).
7. Launch-time F1 env vars were empirically shown to be OVERWRITTEN during backend
   startup because:
   - `swarm_os/app/main.py` calls `load_dotenv(override=True)`
   - `swarm_os/config/settings.py` also reloads `.env`
   - `.env` contains `SWARM_SEMANTIC_CACHE=1` and `SWARM_GENETIC_MUTATION=1`
   - empirical: BEFORE backend import `SEMANTIC_CACHE=0/GENETIC_MUTATION=0`;
     AFTER backend import both = `1`.
8. Therefore: **"environment variable was set before launch" does NOT prove "the
   application is running with that effective value."**
9. Effective runtime configuration must be verified AFTER application startup,
   preferably through the application's authoritative status/configuration surface
   (e.g. `/status` sandbox fields), not inferred from launch config.

### 8.2 Reusable robs4b lesson (generalized, candidate — not a training record of success)

> Controlled evaluation environments require verification of effective runtime
> state, not assumptions based on launch configuration. An environment variable
> can be overwritten during application startup, especially when dotenv/config
> files are reloaded. Inspect the startup path and verify the running
> application's effective configuration before declaring an experimental control
> active.
>
> When a model trajectory references a file path, verify that the path exists in
> the actual workspace before treating it as repository state. A web-search result
> can cause a model to construct a plausible but nonexistent path. Do not create
> the referenced file merely to satisfy the trajectory.
>
> When an evaluation requires isolation, stop rather than proceeding with the
> wrong workspace, cache, memory, mutation, or autonomy settings.

### 8.3 Labels / metadata

- Classification: infrastructure/debugging, controlled evaluation,
  environment/configuration verification, tool-use grounding,
  hallucinated-path detection, experiment isolation, negative lesson / failure avoidance.
- Provenance: Experiment J F1 preparation; `bounds.rs` ATIF steps 3-4;
  clean clone commit `45d9f619`; backend `/status` evidence; dotenv override investigation.
- Status: candidate robs4b training/memory lesson. Recording this does NOT claim
  robs4b has learned the lesson.

### 8.4 Authoritative initialization-order audit facts (read-only, 2026-09-24)

Observed facts from a READ-ONLY initialization-order audit of the backend. This
section records what the audit ESTABLISHED about the runtime. No service was
started during the audit; the single F1 observation remains unrun.

1. `SWARM_SEMANTIC_CACHE` is NOT consumed at module import.
   - `runtime_v2/services/_semantic_decision_cache.py:70-71` defines `_enabled()`
     as `os.environ.get("SWARM_SEMANTIC_CACHE", "0") == "1"`.
   - Read live during tool-decision processing.
   - Therefore its effective value can be changed after import, before requests
     are served.
2. `SWARM_GENETIC_MUTATION` is NOT consumed during module import.
   - `swarm_os/app/main.py:290` checks
     `os.environ.get("SWARM_GENETIC_MUTATION", "").strip() == "1"`.
   - The check runs inside the async `lifespan`, after module import.
   - Therefore its effective value can be changed after importing
     `swarm_os.app.main` but before the server begins serving.
3. The application has an environment-overwrite hazard.
   - `swarm_os/app/main.py:20` calls `load_dotenv(override=True)`.
   - `swarm_os/config/settings.py:23` also performs unconditional dotenv loading.
   - Therefore setting `SWARM_SEMANTIC_CACHE=0` or `SWARM_GENETIC_MUTATION=0`
     BEFORE importing `swarm_os.app.main` is insufficient when `.env` contains
     those values as `1`. The launcher value can be silently overwritten.
4. Other F1 controls survived the dotenv reload because they are not present in
   `.env`:
   - `SWARM_WORKSPACE_ROOT`, `SWARM_MEMORY_INJECT`, `SWARM_AUTONOMY`,
     `SWARM_NO_TOASTS`.
5. The validated safe mechanism (no `.env` and no source modification):
   - Set workspace/isolation controls before import where required.
   - Import `swarm_os.app.main`, allowing its normal dotenv loading to complete.
   - Set `SWARM_SEMANTIC_CACHE=0` and `SWARM_GENETIC_MUTATION=0` AFTER that import.
   - Start uvicorn with the ALREADY-IMPORTED app object rather than an
     import-string target, so uvicorn does not re-import the application and
     repeat dotenv loading.
6. This mechanism was empirically validated in-process:
   - `_enabled()` returned False after the post-import override;
   - genetic mutation gate saw "0";
   - workspace resolution pointed at the F1 fresh clone;
   - an object-based uvicorn configuration held the already-imported app;
   - `SWARM_HARNESS_KEY` remained supplied by normal `.env` loading;
   - no backend was started during this audit.

### 8.5 Generalized robs4b lesson — "Verify reality at the boundary"

A launch configuration is NOT evidence of effective runtime configuration.
Always verify the control AT THE BOUNDARY where the application actually
consumes it.

- Model says a file exists -> check the actual filesystem.
- Launcher says an environment variable is set -> check the running
  application's effective value.
- Backend says workspace isolation is active -> verify the actual
  workspace/write root.
- Experiment says a control is disabled -> verify the effective runtime control.
- Memory says an event occurred -> verify the underlying evidence.

### 8.6 Tool-use grounding lesson (negative)

The F1 trajectory referenced:
`/Users/rober/Projects/swe_probe_work/f1_pilot/run_1_fresh/repo/agent_sdk_toolkit/workspace/bounds.rs`
but the corresponding Windows fresh clone did NOT contain that path or the
`agent_sdk_toolkit` directory. The fresh clone was clean at commit
`45d9f6192dd7b1c81c63f46d277798f19adb97ec` with zero git-status changes. ATIF
showed the model performed a `web_search` then a `filesystem read` based on a
generated path.

Experiments Lesson: MODEL-GENERATED PATH DRIFT / hallucination — NOT missing
experiment infrastructure.

Negative lesson: Never create a missing file merely because an agent trajectory
claims it exists. First verify the path against the ACTUAL workspace, repository
state, fixture state, and experiment harness. A plausible-looking path is not
evidence.

### 8.7 Stop-condition lesson

When an experiment's isolation/control state is wrong, STOP before collecting an
observation. Do not "push through" and contaminate a scientific measurement. This
is the fail-closed decision rule for controlled evaluations: an observation taken
under the wrong control state is not an observation — it is contamination
diagnostics at best (F0 §7/§8), never causal evidence.

### 8.8 Labels (extended) and provenance (audit)

- Labels: infrastructure/debugging, environment/configuration verification,
  controlled evaluation, experiment isolation, tool-use grounding,
  hallucinated path detection, runtime initialization,
  negative lesson / failure avoidance.
- Provenance: Experiment J F1 pilot preparation; F1 Run 1 infrastructure review;
  ATIF steps 3-4 involving the `bounds.rs` path; fresh clone commit
  `45d9f6192dd7b1c81c63f46d277798f19adb97ec`; backend `/status` inspection;
  read-only initialization-order audit; dotenv override investigation.
- Status: candidate robs4b training/memory lesson. Recording this does NOT claim
  robs4b has already learned this behavior.

---

## 9. Evidence-First Self-Improvement Architecture Checkpoint (2026-09-25)

This section records the approved TARGET ARCHITECTURE / DESIGN CHECKPOINT for
the Evidence-First Self-Improvement system. It is **NOT YET IMPLEMENTED** beyond
the existing F1 harness machinery. It establishes the design principles and
layered architecture that will govern all future learning infrastructure.

### 9.1 Core Principle

> All learning artifacts are derived from immutable evidence. Infrastructure
> validity is assessed before capability inference. Causal conclusions are stored
> with explicit confidence levels. Experience promotion requires objective
> evaluator support whenever available. Retrieved experience must demonstrate
> measurable benefit before being used to influence model training.

### 9.2 Architecture Layers

```
Evidence
  → Validity Gate
    → Evidence Confidence
      → Interpretation
        → Causal Evidence Assessment
          → Credit Assignment
            → Experience Extraction
              → Promotion Candidate
                → Review Layer
                  → Promotion
                    → Retrieval
                      → A/B Evaluation
                        → Experience Distillation
                          → QLoRA
                            → A/B Evaluation
                              → later RL
                                → Curriculum Generation
```

**Implementation status:** The layers above are a DESIGN CHECKPOINT, not a
status claim. Do NOT imply that future stages are currently implemented. Only
the existing F1 harness machinery, `SWARM_F1_NO_WEB_TOOLS=1`, and the current
memory/learning infrastructure are IMPLEMENTED.

### 9.3 Evidence / Interpretation / Knowledge

The system distinguishes three levels of epistemic state:

**1. Evidence**
- Immutable raw trajectories
- Tool calls
- Environment state
- Evaluator results
- Validity state
- Failures
- Timestamps
- Provenance

**2. Interpretation**
- Versioned analysis derived from evidence
- Hypotheses
- Confidence
- Causal-evidence assessment
- Credit assignment

**3. Knowledge**
- Promoted memories
- Strategies
- Skills
- Training examples
- Other reusable learning artifacts

**Rules:**
- Raw evidence is immutable.
- Interpretations are versioned.
- Knowledge retains provenance back to its evidence.

### 9.4 Experience Ledger v1

The Experience Ledger is a first-class source of truth. Every learning artifact
must ultimately be reconstructable from the ledger.

Existing memory systems should eventually become rebuildable derived views/indexes
over ledger evidence rather than independent authoritative stores.

**Rule:**
> If an artifact cannot be reconstructed from ledger evidence, it is not
> authoritative.

### 9.5 Upgrade 1 — Evidence Confidence

Every episode should distinguish evidence availability from evidence quality.

Use structured confidence fields:

```
evidence_confidence:
  infrastructure: high
  capability: none
  causal: unknown
  evaluator: n/a
```

Confidence must be scoped by evidence type.

### 9.6 Upgrade 2 — Explicit UNKNOWN State

UNKNOWN must be a first-class state. Do not force
`success` / `failure` / `invalid` when the evidence does not support such a
conclusion.

**Example — Observation 2:**
- repair capability = UNKNOWN
- tool-selection capability = UNKNOWN
- debugging capability = UNKNOWN

Because the agent never reached the tool-decision stage.

**Infrastructure:**
- backend runtime stability = infrastructure-invalid / diagnostic evidence

The learner must preserve:

> insufficient evidence

rather than inventing a capability conclusion.

### 9.7 Upgrade 3 — Evidence Review / Confidence Decay

**Do NOT decay raw evidence.** Raw evidence remains immutable.

Confidence and interpretations may be reevaluated as the environment changes.

Add a conceptual review structure:

```
evidence_review:
  last_verified:
  verification_context:
  confidence:
```

Examples of changing context:
- repository evolution
- infrastructure changes
- tooling changes
- model/version changes

An inference that was strong in 2026 may become less applicable later without
changing the underlying evidence.

### 9.8 Upgrade 4 — Mandatory Provenance

Every memory, strategy, skill, and training example must have structured
provenance.

**Minimum conceptual schema:**

```
provenance:
  episode_ids:
  experiment_ids:
  source_evidence:
  source_interpretations:
  generator_version:
  timestamp:
```

No promoted learning artifact may exist without provenance.

### 9.9 Upgrade 5 — Promotion Evidence Requirements

Formalize minimum requirements for promotion classes.

**Examples:**

```
training_candidate:
  requires:
    validity: VALID
    evaluator: PASS

skill_candidate:
  requires:
    repeated_success: true

strategy_candidate:
  requires:
    evidence_strength: moderate

infrastructure_evidence:
  requires:
    capability_evidence: none
```

The exact implementation schema can be designed later, but the requirement is
explicit:

> Promotion criteria must be objective, explicit, and machine-checkable where
> practical.

### 9.10 Upgrade 6 — Rebuildability

Every memory, strategy, skill, and training example must be reconstructable
entirely from ledger evidence.

Derived systems are views. The ledger is authoritative.

### 9.11 Upgrade 7 — Negative Capability Protection

```
negative_capability_credit:
  allowed: false
  when:
    validity != VALID
```

Infrastructure-invalid, evaluator-invalid, timeout-invalid, or otherwise
non-valid episodes cannot decrease a capability estimate.

**Important distinction:** They may still produce diagnostic evidence about
infrastructure, tooling, harness behavior, etc. They simply cannot be used as
negative evidence about the model's task capability.

### 9.12 Upgrade 8 — Promotion Review Layer

Promotion must become:

```
Promotion Candidate → Review Layer → Promotion → Retrieval
```

Review should check:
- conflicts
- duplicates
- contradictions
- outdated knowledge
- provenance completeness
- evidence requirements

### 9.13 Upgrade 9 — Contradiction Tracking

Promoted knowledge must support:

```
contradiction_status: none | suspected | confirmed
```

and relationships:

```
supersedes:
superseded_by:
contradicts:
```

Conflicting strategies must not silently coexist as if both were universally
valid.

### 9.14 Upgrade 10 — Evidence Constitution

**Permanent section — principles that do not change:**

1. Raw evidence is immutable.
2. Interpretations are versioned.
3. Capability inference requires valid episodes.
4. Infrastructure-invalid episodes receive zero capability credit.
5. Promotion requires objective evidence when available.
6. Every promoted artifact retains provenance.
7. Retriever effectiveness must be measured before training influence.
8. Models cannot self-promote. Evaluators decide promotion.
9. Rebuildability is required.
10. When uncertainty exists, preserve uncertainty.

**Also explicitly recorded:**

> UNKNOWN is a valid scientific result.

### 9.15 Evidence Progression

The evidence strength ladder:

```
Observed → Hypothesis → Supported → Replicated → Generalized
```

Stronger stages require stronger evidence. Causal Evidence Assessment estimates
the strength of support; it does not claim to mathematically prove causality.

**Example — Patch A → tests pass:**
- = weak causal evidence / temporal association only

**Revert Patch A → tests fail, Reapply Patch A → tests pass:**
- = substantially stronger causal evidence through reversal/replication

### 9.16 Current Experiment J State

**Valid F1 observations: 10. Protocol observations completed: 20/20.**
(Reconciled 2026-09-26 from result files in `qwen_train/results/f1_obs*.jsonl`.
Authoritative source: `interpretation.validity_infrastructure` and
`interpretation.f1_endpoint_step` fields in each file's first JSON line.
The authorized F1 dataset comprises the FIRST 20 CHRONOLOGICAL DISTINCT EXECUTIONS.
A 21st supplementary execution occurred but is excluded from the official 20-observation dataset.)

**Post-F1 Governance Baseline (established 2026-09-27):**
- SWARM_AUTONOMY=0 (WatchLoop + autonomous repair disabled)
- SWARM_GENETIC_MUTATION=0 (genetic mutation daemon disabled)
- SWARM_EVAL_TICK unset/disabled (Evaluation Tick daemon off)
- SWARM_EXPERIMENT_J_ARM unset/disabled
- SWARM_F1_NO_WEB_TOOLS unset/disabled globally
- SWARM_EVOLUTION=0
- Verified in restarted backend PID 20332: /health=ok, /readyz=ready, Qdrant healthy, no WatchLoop, no genetic mutation daemon, no Experiment J/F1 workload
- 183 pending mutations preserved but not applied (genetic mutation disabled)

**Observation 1:**
- infrastructure-invalid due web-tool drift (model spent entire 1200s budget on
  web_search/web_fetch, never reached filesystem editing)
- diagnostic evidence only
- zero capability evidence
- corrected by `SWARM_F1_NO_WEB_TOOLS=1`

**Observation 2:**
- **QUALIFYING FIRST-EDIT ENDPOINT @ ATIF STEP 4** (authorial interpretation
  clarified: action-based endpoint; recorded in F1 authorization §7
  F1-OP-004-CLARIFICATION)
- qualifying action: `filesystem.patch` on `swarm_os/lib/paths.py`
- ATIF step: 4 (within 12-step horizon)
- patch accepted: NO (read-before-write guard rejected the relative-path patch)
- file mutated: NO
- repair correctness: UNKNOWN
- capability credit: +0 / -0
- valid F1 capability observation: NO (endpoint qualification ≠ capability evidence)

**Explicit rule:**

> Do not start another F1 observation until the backend crash is diagnosed, the
> minimum infrastructure correction is made, and stability verification
> demonstrates that the backend remains alive through the actual observation
> startup boundary.

**Also recorded:**

> Preflight health is not equivalent to runtime stability.

### 9.17 Implementation Status

**IMPLEMENTED:**
- existing Experiment J/F1 machinery (evaluator separation, pre-flight checks,
  behavioral prompt, fresh-clone gate)
- `SWARM_F1_NO_WEB_TOOLS=1` correction (per-invocation web-tool restriction)
- existing memory/learning infrastructure

**DESIGN APPROVED / NOT IMPLEMENTED:**
- Experience Ledger v1
- Validity Gate as the authoritative learning gate
- Evidence Confidence
- UNKNOWN state
- Evidence Review / confidence reevaluation
- Mandatory provenance
- Promotion evidence requirements
- Rebuildability requirement
- Negative capability protection
- Promotion review layer
- Contradiction tracking
- Evidence Constitution
- Causal Evidence Assessment integration
- Retrieval A/B gate
- Skill promotion
- Training dataset generation
- QLoRA distillation
- Later RL
- Curriculum generation

### 9.18 Failure Classification

Infrastructure failures must use explicit structured classifications. Do not
convert UNKNOWN root cause into a probable cause without supporting evidence.

**Observation 2 classification:**

```
validity:
  infrastructure: INVALID

failure_class:
  process_lifetime_failure

root_cause:
  value: UNKNOWN
  confidence: NONE

observability_failure:
  confirmed: true

capability_credit:
  positive: 0
  negative: 0
```

The `root_cause` field is UNKNOWN with confidence NONE. Candidate hypotheses
exist (see 9.20) but none is supported by evidence sufficient to elevate it
to a conclusion.

### 9.19 Observability as a First-Class Requirement

**Constitutional principle:**

> No system component may participate in scientific evaluation without
> producing sufficient evidence for postmortem analysis.

Insufficient observability is itself an infrastructure defect. A backend that
crashes without leaving a traceback is an observability failure, not merely
an unexplained crash. The absence of evidence is itself evidence of an
observability gap.

### 9.20 Explicit Unknown Root Cause

The backend crash cause for Observation 2 is preserved as UNKNOWN with
explicit candidate hypotheses. None is a conclusion.

```
backend_crash_cause:
  value: UNKNOWN
  confidence: NONE
  candidate_hypotheses:
    - oom
    - process_kill
    - python_exception
    - dependency_failure
    - supervisor_termination
```

Candidate hypotheses are possibilities that future evidence may support or
refute. They are not ranked by probability. Do not select one as "the most
likely" without empirical evidence.

### 9.21 Stability Verification Gate

The required sequence before any future F1 observation:

```
Launcher Fix
  → Stability Verification
    → Observability Verification
      → F1 Preflight
        → First Valid F1 Observation
```

Do NOT proceed directly from launcher fix to F1 observation. Each stage
must independently pass before the next begins.

### 9.22 Evidence Preservation Checklist

Before any future observation, the following evidence must be confirmed
as captureable. This is a reproducibility requirement.

```
evidence_preservation:
  backend_stdout: confirmed
  backend_stderr: confirmed
  router_stdout: confirmed
  router_stderr: confirmed
  launcher_logs: confirmed
  exit_codes: confirmed
  timestamps: confirmed
  process_tree_capture: confirmed
```

If any field cannot be confirmed, the observation must not proceed. The
absence of postmortem evidence is itself a blocking infrastructure defect.

### 9.23 Dependency Mapping

The actual process/dependency graph used by F1 must be documented before
observations proceed. The graph must identify parent/child relationships,
which services the backend depends on, which services the CLI depends on,
and which process failures invalidate an observation.

**Current observed graph (from Observation 2 postmortem):**

```
Start-Process python (fire-and-forget)
  └── backend (uvicorn, port 8000)
        ├── depends on: model_router (ports 8079/8080/8081/8082)
        ├── depends on: Qdrant (port 6333)
        └── depends on: llama.cpp services

CLI (run_repair_task.py → organism_console)
  └── connects to: backend (port 8000)
  └── connects to: evaluator (external, read-only)
```

Process failures that invalidate an observation:
- backend death → observation invalid
- model_router death → observation invalid (backend cannot serve tool decisions)
- Qdrant death → observation may be invalid (memory/lesson paths may fail)
- CLI crash → observation invalid

### 9.24 Observation 2 Evidence Scope

```
evidence_scope:
  applies_to:
    - launcher supervision
    - backend observability
    - process lifetime monitoring
    - infrastructure diagnostics
  does_not_apply_to:
    - repair ability
    - code generation
    - debugging ability
    - tool selection
    - repository reasoning
    - strategy quality
```

Observation 2 provides diagnostic evidence about infrastructure reliability
and observability. It provides zero evidence about the model's capability
on any task dimension.

### 9.25 Scientific Integrity Rule

> Infrastructure corrections may improve measurement reliability. They must
> not improve agent performance.

Supervision, logging, health checks, liveness monitoring, process capture,
and crash preservation are measurement/infrastructure corrections, not
model-performance interventions. Adding process supervision does not make
the model smarter. Adding crash logging does not improve code generation.
These corrections improve the reliability of the measurement, not the quality
of the measured system.

### 9.26 Canonical Reference Episode

Observation 2 is marked as a **canonical/reference episode** for the future
Experience Ledger. It teaches:

- unknown root-cause handling (preserve UNKNOWN, do not guess)
- infrastructure invalidity (process death = zero capability credit)
- capability-credit suppression (negative credit forbidden on invalid episodes)
- observability requirements (insufficient evidence is itself a defect)
- evidence-scope enforcement (infrastructure evidence does not apply to capability)
- decision-boundary protection (capabilities assessed only after reaching the boundary)

### 9.27 Capability Decision Boundary

> Capability credit may only be assigned after the agent successfully reaches
> the relevant decision boundary.

For Observation 2, because the agent never reached tool selection:

```
repair = UNKNOWN
tool_selection = UNKNOWN
debugging = UNKNOWN
```

Never classify these as failures. Never classify them as successes. They are
UNKNOWN because insufficient evidence exists.

The decision boundary for each capability must be explicitly named before
credit can be assigned. For the F1 sandbox_bounds task, the relevant
boundaries are:

1. Tool selection boundary — agent must select `filesystem` tool
2. File reading boundary — agent must read `swarm_os/lib/paths.py`
3. Diagnosis boundary — agent must identify the inverted `relative_to`
4. Edit boundary — agent must apply a patch to the source
5. Verification boundary — agent must run the evaluator and observe results

Credit is assigned only after the agent reaches and passes the relevant
boundary. Before that boundary, the capability state is UNKNOWN.

### 9.28 Permanent Status

```
F1 protocol observations completed = 20 / 20
F1 valid observations = 10 (all reaching f1_endpoint_step = 4)
F1 infrastructure-invalid observations = 10
F1 remaining to complete = 0
Capability credit assigned = 0
Infrastructure diagnostic value = HIGH
Observation 1 = infrastructure-invalid (web-tool drift)
Observation 2 = infrastructure-invalid (no tools, 22.5s)
Observation 11 = infrastructure-invalid (health_gate_failure)
Observations 7-11,13-16 (UUID-named) = VALID, endpoint @ ATIF STEP 4
Observations 18-20 = infrastructure-invalid (timeout, rejection-loop stall)
Replicated behavioral finding: Observations 18-20 exhibit identical rejection-loop stall pattern
Supplementary 21st execution excluded from official 20-observation dataset
Reconciled 2026-09-26 from result files; batch summaries are stale.

Post-F1 Governance Baseline (2026-09-27):
- SWARM_AUTONOMY=0
- SWARM_GENETIC_MUTATION=0
- SWARM_EVAL_TICK unset/disabled
- SWARM_EXPERIMENT_J_ARM unset/disabled
- SWARM_F1_NO_WEB_TOOLS unset/disabled globally
- SWARM_EVOLUTION=0
- 183 pending mutations preserved but not applied (genetic mutation disabled)
- F1 scientific protocol unchanged; only operational runtime parameters modified
```

---

## 10. F2 Engineering Checkpoint (2026-09-28)

**Status:** DOCUMENTATION CHECKPOINT — one scoped F2 engineering work item implemented 2026-09-29 (fresh-process execution/import contract, see `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md`); all other F2 engineering remains not implemented and no experimental result exists.

**Purpose:** Record F2 engineering design decisions, implementation status, and implementation order before code changes begin. This section is an engineering checkpoint, not experimental results.

**Scientific status preserved:** F0 is frozen. F1 is closed. No legitimate ACTIVE lesson L exists. N=2 is not authorized. SWARM_RECEIPT_KEY must not be provisioned. No manual ACTIVE lesson may be created.

### 10.1 Engineering Status (from source audit)

**IMPLEMENTED (F1 machinery, reused for F2):**
- `PromptRepairer.process_failure()` — candidate creation from failures
- `PromptRepairer.evaluate_candidate()` — evaluation gate with HMAC receipt
- `PromptRepairer.promote()` — promotion with receipt verification, fail-closed without `SWARM_RECEIPT_KEY`
- `LessonManager.store()` — Qdrant ActiveLessons persistence
- `LessonManager.render_active_lessons()` — governed seam for lesson delivery
- `run_repair_task.py` — F1 single-observation harness (fresh process, workspace isolation, `SWARM_F1_NO_WEB_TOOLS=1`)
- `f1_infra.py` — evidence manifests, health gate, classification
- `evaluation_bridge.py` — harness→PromptRepairer adapter
- `_strip_web_tools_for_local_analysis()` — web-tool stripping

**NOT IMPLEMENTED (remaining F2-specific):**
- F2 freeze schema / deterministic artifact capture (primitives exist in `runtime_v2/services/f2_freeze.py`; governed production freeze pipeline not implemented)
- F2 certification gate

**IMPLETED since the 2026-09-29 baseline (removing items from the list above):**
- `exclude_ids` parameter in `render_active_lessons()` (2026-09-30)
- Rediscovery rule classification from trajectory timestamps — `qwen_train/f2_rediscovery.py` (F0 §6 predicate; not yet wired into a real F2 arm)
- Adversarial/corruption/mutation tests — `tests/test_f2_invariant_f_mutation.py` plus invariant G in `tests/test_f2_replay.py`

**IMPLEMENTED as of 2026-09-29 F2 baseline (`7165cb20`):**
- Delivery instrumentation (9-field P2 delivery evidence in trajectory JSONL)
- Fresh-process T/X/C0 arm orchestrator (adapter + orchestrator + real-P2 tests)
- Governed promotion fixture (`tests/test_learner_artifact_derivation.py`, 21 tests; uses test-only signing; no real ACTIVE lesson created). Verified ready 2026-09-30: the fixture drives failure → PromptRepairer → evidence → HMAC receipt → promotion → store, reaching `"promoted"` under a test-only receipt key with tmp storage and a mocked lesson manager. Receipt fail-closed paths without signing authority are covered in `tests/test_prompt_repairer.py` (no_signing_authority, wrong-key).
- One authoritative delivery abstraction (invariant C in §10.2)

**NOT YET TRIGGERED:**
- Genuine governed learning event (requires Qdrant healthy + production `SWARM_RECEIPT_KEY` provisioned)
- ACTIVE lesson L: NONE — does not exist; no legitimate ACTIVE lesson has been created

**IMPLEMENTED under dated authorization (2026-09-29):**
- F2 fresh-process execution/import contract — every F2 fresh Python child
  explicitly places `REPO_ROOT` at the front of `sys.path`
  (`sys.path.insert(0, REPO_ROOT)`) before repository imports, then loads and
  independently verifies its serialized freeze manifest/artifact. Enforcement
  point: `tests/test_f2_replay.py::_run_child` / `_child_bootstrap`.
  Authorization: `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md`.
  This is engineering/completeness work only; it does not create an ACTIVE
  lesson, freeze experiment treatment, certify F2, or authorize N=2.

### 10.2 F2 Design Invariants

These are engineering design decisions, not experimental results.

**A. Freeze T first.**
The treatment artifact T must first be generated using the normal governed retrieval/render path (`render_active_lessons()`). The exact resulting artifact is then frozen. F2 X must be derived deterministically from that frozen T artifact. Do NOT implement X by rerunning live retrieval with `exclude_ids` if doing so could alter ranking, budget selection, retrieval, reranking, refill, backfill, or substitution. `exclude_ids` may be implemented as a general backward-compatible rendering capability but must NOT silently change F2 T/X selection semantics.

**B. Cross-process replay.**
A Python `ContextVar` does NOT cross process boundaries. The parent F2 harness must pass a serialized/content-addressed freeze manifest or artifact reference to the child process. The child process must load and verify the artifact/manifest itself. Only inside the child process should the replay provider/context state be installed. The ContextVar is an in-process replay mechanism, not the cross-process transport mechanism.

**C. One authoritative delivery abstraction.**
Avoid creating two independent replay mechanisms. Prefer one authoritative delivery abstraction/provider that can select live lesson delivery or verified frozen-artifact delivery. The model/tool execution path consumes that authoritative delivery result. Do not create separate independent guards that can disagree about what was delivered.

**D. Separate hashes.**
The F2 provenance model must distinguish: (1) `artifact_hash` — hash of the exact bytes/text delivered; (2) `treatment_set_hash` — hash representing the logical treatment identity/set; (3) `manifest_hash` — hash of the canonical provenance manifest. The manifest must be deterministic/canonical and must not include its own hash when computing `manifest_hash`.

**E. Freeze provenance.**
The freeze manifest must have enough provenance to independently establish what was frozen. Design toward fields covering: `schema_version`, experiment identifier, F2 arm/protocol version, Git SHA, model identity, task identity, lesson L identity/hash when applicable, ordered lesson IDs/hashes, frozen artifact, artifact hash, treatment-set hash, freeze timestamp, promotion/provenance reference where applicable, manifest hash. Do not invent values for fields not yet known.

**F. Fail closed.**
Replay must reject corrupted or mismatched artifacts/manifests. A mismatch must not silently fall back to live retrieval. Engineering validation must test mutation of artifact contents, artifact hash, lesson identity/hash, Git SHA, model identity, arm/protocol identity, and manifest integrity.

**G. Retrieval mutation isolation.**
F2 replay validation must include an adversarial test: freeze T; mutate the live retrieval state; replay the frozen artifact; verify replay output is unchanged. This demonstrates replay is actually isolated from live retrieval.

**H. Rediscovery classification.**
Rediscovery is classified from recorded timestamps/trace evidence. The rule is derived from F0 §6 and documented as a classification rule, not presented as proof of causality. Record enough evidence to determine whether the relevant filesystem action occurred before or after lesson delivery.

### 10.3 Implementation Order

1. F2 freeze schema / deterministic artifact / hash primitives
2. Verified replay isolation (in-process + cross-process)
3. Exact T → deterministic X derivation
4. Delivery instrumentation
5. Rediscovery analysis
6. Fresh-process T/X/C0 harness
7. Adversarial/corruption/mutation tests
8. Governed promotion fixture
9. Only after engineering validation, provision real signing authority
10. Create a legitimate ACTIVE lesson through the governed path
11. Freeze F2 treatment/control artifacts
12. F2 certification
13. Authorization review
14. Only then N=2

### 10.4 Governance Rule

> `SWARM_RECEIPT_KEY` is a governance capability, not merely a configuration value.

It must not be provisioned simply to make tests pass. The engineering machinery must first be implemented and tested without granting real signing authority. Only when the governed promotion fixture is ready should real signing authority be introduced.

### 10.5 Current Prerequisites

| Prerequisite | Status | Source |
|---|---|---|
| F0 frozen | COMPLETE | `docs/EXPERIMENT_J.md` commit `20a1989b` |
| F1 closed (20/20) | COMPLETE | `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` |
| Backend healthy | COMPLETE | Live verification 2026-09-28 |
| Qdrant healthy / C0 verified | COMPLETE | `ActiveLessons` absent (404) |
| Post-F1 governance baseline | COMPLETE | Verified 2026-09-27 |
| `SWARM_RECEIPT_KEY` | PROVISIONED (operator, §10.3 item 9) | Fail-closed without it; required for promotion |
| F2 engineering | PARTIAL — fresh-process execution/import contract implemented 2026-09-29; all other F2-specific engineering NOT STARTED | `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md` |
| ACTIVE lesson L | NONE | No lesson exists in Qdrant |
| N=2 authorization | NOT AUTHORIZED | No document authorizes N=2 |

---

## F2 Authority Amendment Pointer (2026-09-29)

The F2 authority documents were amended on 2026-09-29 (see
`docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md` and
`docs/EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md`, as amended). The F2
HTTP execution graph is exactly `P0 → P1 → P2`: P1 and P2 are separate fresh
processes per arm; P1 drives P2 directly over `127.0.0.1` HTTP/SSE; P3 is not
required; P1/P2 fusion is not authorized; P2 owns authoritative replay state and
delivery. Backend identity is the serving process (socket/listener owner):
`p2_serving_pid` + `p2_serving_start_time`, with launcher and evidence-writer
identities recorded separately; expected-PID equality is not a requirement.
Committed F0 delivery-identity recording (`lesson_block_hash`,
`final_prompt_hash`, `delivery_timestamp`, backend PID/start time) remains
authoritative; a delivery-time hash-mismatch ABORT is not currently authorized.

This is a dated pointer record only. No experiment-state conclusion is changed by
this entry.
