# Self-learning experiment — state & plan (2026-09-15)

Handoff/state doc. If you are resuming, **read this first**, then
`docs/EXPERIMENTS.md`, `docs/SOTA_ROADMAP.md`, `docs/WRITE_FIX_TASKS.md`.

---

## 0. The question we are answering

**Is the self-learning CLI actually learning?** (The learner is the CLI/system —
tool policy, memory, recovery, routing — NOT Qwen's weights; see `docs/EXPERIMENTS.md`.)

**Answer so far: the pipeline works and correctly proves there is nothing to learn yet.**
The tasks are below the coder's ability → no failure → no gradient → no behaviour change.

---

## 1. Where the experiment stands (live state)

| Item | State |
|---|---|
| Synthetic fix pool (130 candidates, 8 families) | **measuring** — `run_candidate_pool.py --n 130 --concurrency 4` |
| Real-bug harvester (`mine_fix_commits.py`) | **built + pushed**; single-repo (this repo); validation batch running |
| Contamination filter (Item 1) | **NOT built** — next task, full spec below |
| File/module holdout split | **not built** — after contamination filter |
| Multi-repo / SWE-bench supply | **deferred** — only if the above says we need diversity |

**Measured so far (valid only, `cli_ok=True`):**
```
synthetic fix pool : ~70/122 measured, 65 pass / 5 fail  ≈ 92% pass   (ceiling)
30 hand-written kinds : 20/20, 31/31 → ~100% pass          (ceiling)
Experiments A (T1→T2) : no_signal (Δ+3.3pp, McNemar p=0.625)
Experiment B (memory) : no_signal (Δ+6pp, p=0.508, n=50)
tool_weights          : all ≈0.99 (no discrimination — the degenerate-learning signature)
```

The 5 genuine failures (distinct mechanisms, the only real signal so far):
`cache_key_uses_length_only`, `aliasing_shared_dict_between_functions`,
`dict_items_vs_keys_confusion`, `wrong_branch_order`, `wrong_method_receiver`.

**CRITICAL — phantom failures:** if the **backend is down**, `run_candidate_pool` rows
come back `cli_ok=False` (CLI aborts ~20 s) and the module is "unfixed" → they look like
FAILURES but are **missing data**. Always filter `cli_ok == True` before counting. A
dead-backend run produced a false "76% fail" that was 103 phantoms; the real rate was ~92% pass.

---

## 2. The next task — Item 1: contamination exclusion (FULL VERBATIM SPEC)

Implement **Item 1 only**. Do not modify the curriculum, training pipeline, evaluation
logic, model configuration, or difficulty system in this task.

### 1. Inspect before changing anything
Locate the existing `FIX:` commit harvester; read its candidate extraction + validation
flow and its tests; identify where candidates are persisted/output; how commit hashes are
represented; reliable local Git evidence for commits created/materially produced by the
current contaminated session. **Reuse the existing architecture. Do not create a
second/parallel harvester.**

### 2. Add a commit-hash denylist
Add an explicit **commit-hash denylist** as the authoritative contamination mechanism.
Stored in a small, inspectable, checked-in data/config file (not hidden in logic). Each
entry: full commit hash + exclusion reason + enough provenance/context. Conceptual form:
```
<full_commit_hash>    excluded_contaminated_commit    <reason/context>
```
Use the project's existing preferred format if one exists. **Append-only going forward** —
future sessions append newly identified hashes+reasons without rebuilding/rewriting/
replacing the ledger. Existing entries and provenance stay intact.

### 3. Identify the current contaminated commits
Use reliable repo/session evidence. **Do not guess hashes. Do not auto-exclude every commit
made today.** Distinguish: actual bug/fix material produced by the session vs
infrastructure-only / harvester-implementation / test-only commits vs unrelated history.
e.g. infra commit `852a3809` must NOT be denylisted merely for being created this session.
**Fail-safe: if the contaminated commits cannot be determined reliably, STOP** — do not
create a guessed denylist; report what was inspected / what could / could not be determined /
what needs confirmation. A guessed denylist is worse than stopping.

### 4. Apply the filter before candidate use
A denylisted commit must be rejected **before it can become a usable harvested candidate**,
covering training candidates, evaluation candidates, and intermediate harvested outputs that
could later feed either pool. Never silently pass a denylisted commit into a dataset.

### 5. Preserve the audit trail
Do not silently delete/hide excluded candidates. When rejected for denylisting, record via
the existing logging/audit mechanism: commit hash, exclusion reason, candidate identity if
available, timestamp if the logging system already records it. Use explicit reason
`excluded_contaminated_commit`. **Rejected from the experiment ≠ erased from the evidence.**

### 6. Do not use weak contamination detection as the authority
Not authoritative: commit date, author name, commit message, branch name, "recent commit"
heuristics. They may be **secondary diagnostics only**; the **exact commit hash is
authoritative**. Goal: deterministic, reproducible exclusion.

### 7. Preserve existing harvester behavior
For every NON-denylisted commit: preserve existing `FIX:` detection, extraction, fail→pass
validation, candidate quality checks, output format, logging. **Do not weaken validation. Do
not make previously rejected candidates pass** merely because of this change.

### 8. Explicitly DO NOT change
curriculum generation · synthetic bug generation · bug difficulty · bug taxonomy · training
code · LoRA config · model config · model prompts · evaluation scoring · first-attempt
metrics · train/eval partitioning · dataset splitting · `outcome_fitness` · observation-log
semantics · `FIX:` extraction rules · fail→pass validation rules · test generation ·
hidden-test behavior · check-file hashing · unrelated security fixes · unrelated refactoring.
If a change is not necessary for contamination exclusion, do not make it.

### 9. Tests (minimum)
1. eligible `FIX:` commit → detected, passes filter, proceeds through harvester;
2. denylisted commit → detected, rejected, not a usable candidate;
3. no output leakage — a denylisted commit's candidate cannot appear in final usable output;
4. reason recorded — rejection produces `excluded_contaminated_commit` (or canonical equiv);
5. filtering regardless of bug validity — a denylisted commit that WOULD otherwise pass
   fail→pass validation is still rejected;
6. append-only — adding a new hash does not remove/overwrite existing entries.

### 10. Verification
Run: existing harvester tests; new contamination tests; syntax/import checks for modified
files; any project-specific validation the harvester normally requires. Then inspect the
resulting candidate output and report **Denylist** / **Counts** (before X / excluded Y /
usable Z) / **Leakage** (denylisted hashes in usable dataset: 0) / **Tests**.

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
