# Experiment J — F2 Isolation & Population Audit

**Status:** AUDIT RECORD — NO IMPLEMENTATION AUTHORIZED
**Date:** 2026-09-30
**Baseline:** `master` @ `2bd90b5c`
**Scope:** Read-only audit. No production code modified. No isolation implemented. No population designated. No F2 arm run. No F1 code altered. No experimental semantics changed.

This audit answers the isolation-ownership and population-source questions
raised by `docs/EXPERIMENT_J_F2_STEP5_AUDIT.md` (`2bd90b5c`).

**It also corrects an error in that document's §4.1 call graph.** See §2.3.

---

## 0. Evidence Labels

| Label | Meaning |
|-------|---------|
| **PROVEN** | Directly verified at a cited file:line. |
| **INFERRED** | Follows from PROVEN facts by a stated step. |
| **REPORTED** | Stated by an authoritative document; not re-verified. |
| **UNKNOWN** | Not established here. Requires a decision or new evidence. |

> **PART II CONTAINS CORRECTIONS TO PART I.** A re-verification pass (2026-09-30)
> falsified the "3 of 14 SWE repositories" claim inherited from `2bd90b5c`, and
> qualified two further statements. **Read §11 before relying on Part I.**
> Corrections C1, C6, C7 are material. Sections C3, C4, C5, C8 are confirmed
> unchanged.

---

## 1. Executive Finding

The isolation implementation boundary is **PROVEN** and it is the **parent
orchestrator** (design §10 row 0 assigns workspace reset to the parent; the
parent is the only component that sees the arm identity before the child is
spawned and the only one that can hand a per-arm path down).

The F2 task population is **NOT PROVEN**. F2 has no task-input contract at all:
`run_f2_arm()` accepts a `task_id: str` and a `render_t` callable, and nothing
else. No repository, base commit, test command, fail-to-pass set, or workspace
path enters F2.

The more consequential finding is that **F2 has no live task-execution path
today.** `F2ExecutionAdapter` is instantiated only in tests; the production
worker (`f2_arm_worker.py`) does not import it and, with no `F2_ARM_EXEC_CMD`,
records `execution_result = {"delegated": True, "reason": "…live worker
execution is a future authorized step"}`. `run_curriculum` and
`run_repair_task` are therefore **not** reached from any production F2 call
graph.

Engineering readiness and scientific readiness diverge sharply: delivery
attribution is working, and there is nothing to execute.

---

## 2. F2 Call Graph

### 2.1 Actual production path — PROVEN

```
run_f2_arm(...)                     qwen_train/f2_arm_orchestrator.py:135
  │ args: arm, manifest_dir, worker_script, repo_root, render_t,
  │       lesson_l_id, lesson_l_hash, task_id, git_sha, model_name,
  │       rollout_id, trajectory_run_id, system_prompt, timeout_s   (:135-153)
  │
  ├─ render_t()                     (:178)  ← caller-supplied Callable
  ├─ freeze T / derive X / build C0 (:190-195)
  ├─ verify_manifest(arm_artifact)  (:197)
  ├─ persist_manifest(...)          (:200)
  ├─ env: SWARM_F2_REPLAY / _MANIFEST_PATH / _REPO_ROOT /
  │       _ROLLOUT_ID / _TRAJECTORY_RUN_ID                        (:205-210)
  │
  └─ subprocess.Popen([sys.executable, "-u",
        f2_arm_worker.py, "--manifest", M, "--arm", A],           (:212-221)
        cwd=str(repo_root), env=env)
     ══ PROCESS BOUNDARY (fresh child) ══
        ▼
     f2_arm_worker.main()           qwen_train/f2_arm_worker.py:139+
       │ sys.path.insert(0, _HERE.parent)          (repo-root bootstrap,
       │                                            execution-contract auth)
       │ load_manifest → verify_manifest           (:165-166)
       │ arm == manifest.arm                       (:169-172)
       │ install_replay_state(...)                 (:175)
       │ delivered = get_delivery_artifact()       (:176)
       │ hashes + delivery_timestamp               (:178-180)
       │
       ├─ IF F2_ARM_EXEC_CMD  → subprocess.run(exec_cmd, shell=True,
       │                         timeout=30)                        (:184-194)
       │ ELSE                 → {"delegated": True, "reason":
       │                        "…live worker execution is a future
       │                        authorized step"}                   (:195-200)
       │
       └─ print(_arm_report(receipt))                               (:290)
```

**There is no further edge.** The worker never imports `f2_execution_adapter`,
`run_curriculum`, or `run_repair_task` (verified: zero matches for
`F2ExecutionAdapter|f2_execution_adapter|run_curriculum|cli_runner|adapter` in
`f2_arm_worker.py`).

### 2.2 Test-only path — PROVEN

```
F2ExecutionAdapter(...)            constructed ONLY in:
  tests/test_f2_execution_adapter.py   (8 sites)
  tests/test_f2_integration.py        (7 sites)
  tests/test_f2_real_p2_integration.py (1 site)
  └─ prove_real_p2_delivery(...)      f2_execution_adapter.py:744
       └─ start_real_p2_with_fake_model(...)   :787 → :204
            env["SWARM_WORKSPACE_ROOT"] = str(workspace_root)   :229
            real uvicorn P2 + FAKE model
       └─ post_task_stream(port, agent_id, task_prompt, …)      :803
```

`prove_real_p2_delivery` is explicitly a **proof** seam: *"NO real model/provider
is ever contacted"* (`:770`).

### 2.3 CORRECTION to `2bd90b5c` §4.1 — PROVEN

The pushed audit rendered the path as
`… → F2ExecutionAdapter → run_curriculum._attempt_once → fresh P2`, and put
"root: parent" on the adapter.

**Both are wrong.** The worker does not import the adapter, and the adapter is
never constructed in production. The production path **ends** at
`f2_arm_worker.main()`. `run_curriculum` is not on it.

The correction matters: the earlier graph implied a P2 exists and a task runs.
Neither is true in the production F2 path today. §4.3 of the pushed audit
correctly recorded that `_eval_swe` is not on the F2 path; this audit shows the
stronger fact that **no** task-execution code is.

---

## 3. Boundary Table

| # | Boundary | Function / file | Workspace in → out | Process boundary | Can select a separate workspace? | Clean at base commit guaranteed? | Independent per T/X/C0? | Preserved on retry? |
|---|---|---|---|---|---|---|---|---|
| 0 | Task specification → orchestrator | `run_f2_arm(...)` `f2_arm_orchestrator.py:135` | **none in** | no | **NO — no path parameter exists** | NO | NO | n/a |
| 1 | Render/freeze | `_default_render_governed_t` `:96`; `verify_manifest` `:197` | n/a | no | n/a | n/a | n/a | n/a |
| 2 | Parent → worker | `subprocess.Popen` `:212-221` | `cwd=repo_root` (the **H2V repo**, not a task repo) | **YES** | **YES** — parent chooses `cwd` and `env` | NO | **NO — same value every arm** | NO |
| 3 | Worker → exec seam | `subprocess.run(exec_cmd, shell=True, timeout=30)` `f2_arm_worker.py:187` | **none** — `shell=True` string, no path control | YES | Only if the operator writes a path into `F2_ARM_EXEC_CMD` | NO | NO | NO |
| 4 | Worker → receipt | `print(_arm_report(receipt))` `:290` | n/a | — | — | — | — | — |
| — | Adapter (test-only) | `F2ExecutionAdapter.__init__` `f2_execution_adapter.py:452`; `_start_fresh_backend` `:497` | `workspace_root` in → `env["SWARM_WORKSPACE_ROOT"]` `:229`, `:509` → backend_starter | YES | Receives a path; **creates none** | **NO** | **NO** — caller passes the same path | NO |

**Design row 0** (`EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md:446`) requires:

> `| 0 | workspace reset | parent | fresh clone | clean working tree | git status --porcelain empty, HEAD=base | ABORT ARM | reset once |`

Owner = **parent**. Input = **fresh clone**. Verification = porcelain empty AND HEAD=base. Failure = **ABORT ARM**.

None of this is implemented: no `git reset`, no `clone`, no porcelain check, no
base-commit check anywhere in `qwen_train/f2_*.py`.

---

## 4. Isolation Ownership Analysis (A–E)

| Option | Compatible with the repository/design? | Evidence |
|---|---|---|
| **A. Parent/orchestrator creates one isolated clone per arm** | **YES — and it is what the design assigns** | Design §10 row 0: owner `parent`, input `fresh clone`, failure `ABORT ARM`, mutability `reset once`. Design §13.5 `:679-680`: *"on a fresh isolated clone; `git reset --hard` per arm … HEAD = frozen base."* The parent is the only component holding `arm` **before** spawn (`:159` validation, `:212` argv, `:206-210` env). |
| **B. Worker creates one isolated clone per arm** | Partial | The worker receives only `repo_root` (H2V), `manifest_path`, `--arm`, `--system-prompt`. It has no base commit, no repository URL, no clone source. It could in principle read `artifact.task_id`, but no clone machinery exists. Cloning inside the worker would also put the reset **after** the process boundary, contradicting "reset once" and making retry semantics weaker. |
| **C. F2ExecutionAdapter creates one isolated clone per arm** | **NO — contradicts the design, and unreachable** | Adapter ownership is ruled **against** at design `:739` (option D rejected with rationale *"cleanest isolation of F2 logic from F1"*). Design §10 row 0 says parent. Additionally the adapter is **test-only** (§2.2), so an adapter-owned reset would never execute in production. |
| **D. Lower-level task/workspace infra owns isolation** | **NO for F2** | That infrastructure is F1's (`f1_infra.start_backend_fresh`, `cli_baseline_swe._reset_instance`). Design §13.7 `:708-709` states F1 reuse is *"a design/implementation fact, NOT F2 scientific authorization."* The adapter docstring `:34` calls `_reset_instance` *"reusable read-only as TECHNICAL machinery"* — read-only, never invoked. |
| **E. Another architecture already present** | **NO** | No clone/reset implementation exists in any `qwen_train/f2_*.py`. `f2_arm_orchestrator.py:19` claims *"One fresh child process per arm"* — process isolation only; `cwd=repo_root` is identical for every arm (`:219`). |

### Answer: **A — the parent/orchestrator.**

Selected on ownership and fail-closed grounds, not preference:

1. **Ownership** — the parent is the only component that knows `arm` before
   spawn, so only it can allocate a per-arm resource.
2. **Design mandate** — §10 row 0 assigns the reset to the parent, with input
   "fresh clone" and failure "ABORT ARM".
3. **Fail-closed** — `run_f2_arm` raises on any invalid state (`:227-244`); a
   parent-owned reset makes "HEAD=base verified" a precondition the parent can
   enforce *before* spending a child.
4. **Provenance** — the parent already writes `manifest_identity.manifest_path`
   and can record the per-arm workspace in the same immutable place.
5. **Retry** — "reset once" per row 0 implies re-materializing per arm
   invocation, which is a parent-lifetime concern.

**Not implemented. This audit does not implement it.**

---

## 5. Population Authority Chain

### 5.1 What F2 actually accepts — PROVEN

`run_f2_arm` signature (`f2_arm_orchestrator.py:135-153`):

| Task datum | Present in F2? |
|---|---|
| `task_id` | **YES** — `str` (`:144`), frozen into the manifest (`f2_freeze`) |
| repository / repo URL | **NO** |
| base commit | **NO** (`git_sha` at `:145` is the **H2V repo's** sha, recorded in the manifest as provenance, not a task checkout point) |
| test command | **NO** |
| fail-to-pass tests | **NO** |
| pass-to-pass tests | **NO** |
| workspace path | **NO** |
| prompt | Indirect — `system_prompt` (`:151`) is passed to the worker for hash computation; the **task prompt is not an orchestrator input** |

### 5.2 Authority chain — PROVEN

```
docs/EXPERIMENT_J.md            (F0, frozen)   — no task population named
docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md    — no pool named
docs/EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md — no pool named
docs/EXPERIMENT_J_F1_AUTHORIZATION.md          — one PILOT task only
docs/LEARNING_EXPERIMENT_STATE.md              — F1 pilot task only
─────────────────────────────────────────────────────────────
qwen_train/curriculum/swe_pool.jsonl           — 14 rows, ZERO inbound edges
```

`2bd90b5c` established: zero hits for `universe` and `swe_pool` across all
authoritative docs.

**New evidence this audit:** `swe_pool.jsonl` has **zero inbound edges from F2**.
`build_swe_pool.py` creates it; its consumers are F1-side
(`prompt_repairer._load_swe_task` `:292`, `:621`, `:656`; `cli_baseline_swe`
`:23`, `:296`) and `runtime_v2/api/evaluation_types.py:24`. No F2 module
references it.

### 5.3 Classification: **option 5 — currently disconnected from F2**

- Not (1) designated — no document names it.
- Not (2) merely reusable — no F2 mechanism can currently read it.
- Not (3) input to an F2 selection mechanism — **no such mechanism exists.**
- Not (4) *only* an F1 artifact — it is the F1 evaluator's pool
  (`prompt_repairer._is_swe_task` `:655-667`), and F2 does not consume it.
- **(5) Yes — disconnected.**

**The absence of a task-input contract (§5.1) is the root finding.** A
population cannot be designated into a contract that does not exist.

---

## 6. Task Workspace Lifecycle

| Step | Status | Evidence |
|---|---|---|
| Task specification → orchestrator | **NOT PROVEN / ABSENT** | `run_f2_arm:135-153` has no repository/commit/test/workspace parameter (§5.1) |
| Repository acquisition | **ABSENT in F2** | No clone/fetch in any `f2_*.py`. F1's `fetch_instance` (`prompt_repairer:623`) is unreachable from F2 (§2.3) |
| Base-commit checkout | **ABSENT in F2** | No `git reset --hard` / `checkout` in any `f2_*.py`. Design requires it (§3 row 0) |
| Clean-state validation | **ABSENT in F2** | No `git status --porcelain` check. Design requires it (§3 row 0) |
| Arm workspace creation | **ABSENT** | No per-arm allocation. All arms get `cwd=repo_root` (`:219`) |
| T/X/C0 execution | **PARTIAL** — process only | Fresh child per arm (`:215`); **live task execution is a stub** (`f2_arm_worker.py:195-200`) |
| Result/evidence collection | **PROVEN for delivery**; ABSENT for outcome | Delivery evidence bound and fail-closed (`660323dc`); no first-edit/outcome capture in the production path |
| Cleanup | **ABSENT** | No cleanup in `run_f2_arm` (child reaped at `:222`); no workspace teardown |

### 6.1 Does F2 assume repos are pre-checked-out under `swe_probe_work/`? — **NO, and this is a real coupling risk**

**PROVEN:** F2 never references `swe_probe_work`. That path exists only in
`prompt_repairer._run_swe_harness:313` — F1, unreachable from F2 (§2.3).
`run_f2_arm`'s `repo_root` is the **H2V repository root**, used as the child's
`cwd` for imports — not a task workspace.

**So the F1 limitation was NOT transferred into F2.** Good news, and it corrects
the concern raised in `2bd90b5c` §2.3, which framed the 3-of-14 checked-out
repos as an F2 obstacle.

**However** — the consequence is the opposite of reassuring: **F2 currently has
no task workspace at all.** Its `cwd` is the agent's own source tree. If a task
prompt is ever driven against that directory, the agent would be editing H2V
itself. Isolation is not merely unimplemented; there is no isolated target for
it to protect. **This raises the priority of option A.**

---

## 7. Isolation Invariant Table

Design requirement, `EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md:679-680` and
§10 row 0.

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | T and X cannot share a writable workspace | **NOT ENFORCED** | Both arms get identical `cwd=repo_root` (`f2_arm_orchestrator.py:219`); no workspace parameter exists |
| 2 | C0 cannot share a writable workspace with T/X | **NOT ENFORCED** | Same; C0 differs only by empty artifact (`build_c0_artifact` `:170`) |
| 3 | An arm cannot inherit another's uncommitted files | **NOT ENFORCED** (and vacuous today) | No workspace is created or reset; production path never runs a task (`f2_arm_worker.py:195-200`) |
| 4 | An arm cannot inherit another's generated ignored files | **NOT ENFORCED** | No reset exists. Note: F1's `_reset_instance` uses `git clean -fd`, not `-fdx` (`cli_baseline_swe.py:242`) — a known gap to **not** inherit |
| 5 | Starting tree corresponds to declared base commit | **NOT ENFORCED** | No `HEAD=base` check anywhere in F2 |
| 6 | Workspace identity recorded in evidence | **ABSENT** | Receipt (`f2_arm_worker.py:242-289`) records experiment/task/arm/rollout/trajectory/manifest/delivery identity — **no workspace path, no git SHA of the workspace** |
| 7 | Cleanup/retry must not reuse another arm's workspace | **NOT APPLICABLE** — no workspace exists | No allocation, no cleanup, no retry path in `run_f2_arm` |
| 8 | P2/backend points at the intended arm workspace | **PARTIAL (test-only)** | Adapter propagates `workspace_root` → `env["SWARM_WORKSPACE_ROOT"]` (`:229`, `:509`), but the adapter is test-only (§2.2) and merely forwards the caller's path without creating it |
| 9 | Evidence identifies arm and task unambiguously | **PROVEN for delivery** | `arm`, `task_id`, `rollout_id`, `trajectory_run_id` in the receipt (`:245-249`); arm==manifest enforced (`:169-172`, `:235-238`); `660323dc` added fail-closed rollout-bound delivery evidence |

**1 of 9 enforced** — and that one is the delivery layer, not isolation.

---

## 8. Provenance / Join-Key Table

Question: can a later audit prove task X @ base commit Y @ arm Z @ workspace W
@ manifest M @ rollout R @ evidence E are one experimental unit?

| Entity | Identity field | Where recorded | Status |
|---|---|---|---|
| Experiment | `experiment_id` | receipt `:245`; manifest | **PROVEN** |
| Task | `task_id` | receipt `:247`; manifest; `SWARM_TASK_ID` env | **PARTIALLY PROVEN** — identity present; **no task *content*** (repo/commit/tests) is bound, so `task_id` names a task without defining it |
| Arm | `arm` | receipt `:248`; `--arm` argv `:212`; enforced `:169`, `:235` | **PROVEN** |
| Rollout | `rollout_id` | receipt `:249`; env `:209`; P2 evidence record | **PROVEN** (`660323dc`) |
| Trajectory | `trajectory_run_id` | receipt `:250`; env `:210` | **PROVEN** |
| Manifest | `manifest_hash`, `content_address`, `manifest_path` | receipt `:263-268` | **PROVEN** |
| Treatment | `treatment_set_hash`, ordered lesson ids/hashes, `lesson_l_hash` | receipt `:250-261` | **PROVEN** |
| Delivery | `lesson_block_hash`, `final_prompt_hash`, `delivery_timestamp` | receipt `:270-275`; P2 authoritative | **PROVEN** |
| P2 process | `serving_pid`, `serving_start_time` | receipt `:278-284` | **PROVEN** |
| H2V source tree | `git_sha` | manifest; receipt `:246` | **PROVEN** |
| **Task repo** | — | — | **MISSING** |
| **Base commit of task** | — | — | **MISSING** |
| **Workspace path / identity** | — | — | **MISSING** |
| **Test command / F2P / P2P** | — | — | **MISSING** |
| **Outcome / first-edit evidence** | — | — | **MISSING** (no live execution) |

### Missing join keys — documented, not fixed

1. **Task-repository identity and base commit.** `task_id` is an opaque string;
   two different checkouts could satisfy it. Required for F0 §8
   ("Workspace/code state — REQUIRED CONTROL") and §12's "wrong model/LoRA"-
   style contamination rules to be enforceable.
2. **Workspace identity.** Without the resolved absolute path and its git HEAD,
   evidence cannot prove *which* tree produced the delivery hashes. F0 §8 lists
   workspace/code state as a REQUIRED CONTROL; the receipt has no field for it.
3. **Test-command / F2P / P2P.** F0 §12 excludes runs on malformed trajectory
   and wrong-treatment conditions but has no field binding a run to the test
   contract that defines its verdict.
4. **Outcome evidence.** No first-edit / steps-to-first-edit record exists,
   because production F2 never executes a task. F0 §5's primary endpoint has no
   producer.

**None of these are proposed as code here.**

---

## 9. Scientific Readiness Table

| Component | Status | Evidence |
|---|---|---|
| **A. F2 population definition** | **NOT PROVEN** | No task-input contract (§5.1); `swe_pool` disconnected (§5.3); no doc names a population (`2bd90b5c` §2.1) |
| **B. Paired-unit definition** | **REPORTED** | `EXPERIMENT_J.md:198` — "Unit of analysis: Rollout (one trajectory per arm per task)"; `:203` pairing by task + seed. Not implemented: `run_f2_arm` runs one arm per call and defines no pair |
| **C. Arm isolation** | **NOT PROVEN / BLOCKED** | Invariants 1–8 unenforced (§7); owner proven = parent (§4); nothing implemented |
| **D. Live execution** | **NOT PROVEN** | `f2_arm_worker.py:195-200` records `delegated` with *"live worker execution is a future authorized step"*; adapter test-only (§2.2) |
| **E. Delivery attribution** | **PROVEN** | `660323dc`: P2-authoritative evidence, rollout-bound, fail-closed on missing/wrong/stale/conflicting; 23 tests; revert-then-pass verified |
| **F. Outcome/verdict attribution** | **NOT PROVEN** | No live run → no first-edit evidence; join keys missing (§8) |
| **G. Confirmatory sample size** | **NOT PROVEN** | `n`, δ, test never frozen. `LEARNING_EXPERIMENT_STATE.md:23` asserts frozen; no preregistration exists (`2bd90b5c` §2.6) |
| **H. Statistical analysis** | **BLOCKED on A, C, D, F, G** | Unit is a rollout; no rollout is produced. Power analysis on a confounded or absent design is meaningless |

**Engineering readiness is not scientific readiness.** E (delivery attribution)
is PROVEN — genuinely working, revert-then-pass verified, and beyond what
published frameworks ship. It is also the *only* component that is ready, and it
measures delivery of a treatment into a prompt that no task ever receives.

---

## 10. Decision Gate

**Is the correct isolation implementation boundary now PROVEN?**
**YES** — the **parent orchestrator**, `run_f2_arm` in
`qwen_train/f2_arm_orchestrator.py`. Assigned by design §10 row 0 (owner
`parent`, input `fresh clone`, failure `ABORT ARM`, mutability `reset once`) and
§13.5 `:679`; and it is the only component holding `arm` before the child spawn.
Options C and D are ruled out (C contradicts the design and is unreachable;
D is F1 machinery the design classifies as non-authorizing, `§13.7`). Not
implemented.

**Is the F2 population source now PROVEN?**
**NO.** `swe_pool.jsonl` is classified **option 5 — disconnected from F2**:
zero F2 references, zero document designation, and no task-input contract for
it to plug into.

**Is F2 scientifically ready to execute?**
**NO.** Four of eight scientific components are NOT PROVEN or BLOCKED, and the
primary endpoint (F0 §5 first-edit) has no producer because no production path
executes a task. Only delivery attribution (E) is PROVEN.

**What exact prerequisite remains?**

Prerequisites are ordered; the first is genuinely blocking:

1. **Define the F2 task-input contract** — extend `run_f2_arm` to accept
   repository, base commit, test command, F2P/P2P, and workspace path (or a
   task record carrying them). **Without this no population can be designated
   and isolation has nothing to isolate.** This is a scientific-design decision
   under F0 §16 change control.
2. **Authorize parent-owned per-arm workspace materialization** at boundary 2
   (§3), implementing design row 0: clone/reset to the declared base commit,
   verify `git status --porcelain` empty AND `HEAD == base`, **ABORT ARM**
   otherwise, before spawning the child. Must be independent per T/X/C0 and
   must not inherit F1's `git clean -fd` (invariant 4).
3. **Authorize a live task-execution path** — replace the `delegated` stub
   (`f2_arm_worker.py:195-200`) so the F0 §5 endpoint has a producer, and route
   it against the per-arm workspace rather than `cwd=repo_root`.
4. **Add the missing join keys** to the receipt: task-repo identity, base
   commit, workspace path, test contract, outcome evidence (§8).
5. **Then** designate the population, establish `n`/δ/test by preregistration,
   and only then execute.

The F1 `_eval_swe` defect recorded in `2bd90b5c` §4.3 remains **real but
separate** — it is not on the F2 path (§2.3) and must not be bundled with F2
work.

---

---

# PART II — CORRECTIONS

**Added:** 2026-09-30, re-verification pass.
**Scope of this part:** correct claims in Parts I–X that re-verification proved
wrong. Sections not listed here stand unchanged.

Every correction below was produced by re-running the original probe. Where a
prior claim rested on a wrong path or a wrong probe root, that is stated
explicitly rather than silently replaced.

## 11. Correction Table

| # | Existing claim | Evidence | Correct statement | Status |
|---|---|---|---|---|
| C1 | `2bd90b5c` §2.3/§2.4: *"only 3 of 14 pool repos are checked out"*; *"those 3 are dirty at their base commits"* | `Get-ChildItem C:\Users\rober\Projects\swe_probe_work -Directory` → 34 dirs incl. **all 14** pool `instance_id`s. Probe at `<id>/repo`: **14/14 are Git checkouts with `HEAD == base_commit`.** | **All 14 pool repositories exist as Git checkouts at their declared base commits.** The "3 of 14" figure came from probing the wrong directory level (`<id>` instead of `<id>/repo`, which is what `prompt_repairer.py:313-314` uses) and from enumerating only the repos I had personally observed. **FALSIFIED.** | **CORRECTED** |
| C2 | §6.1: *"F2 never references `swe_probe_work`"* — correct — but the surrounding framing implied F1 repos were an F2 obstacle | Same probe | **Unchanged and now better supported:** all 14 exist and are at base commit, so F1 availability is not the constraint. The F2 conclusion (no task workspace at all) is unaffected. | **STANDS** |
| C3 | §2.3 correction of `2bd90b5c`'s call graph | `Select-String` on `f2_arm_worker.py` for `F2ExecutionAdapter\|f2_execution_adapter\|run_curriculum\|cli_runner\|adapter` → **zero matches**. `F2ExecutionAdapter(` constructed only in `tests/` (16 sites). | **CONFIRMED CORRECT.** Production path ends at `f2_arm_worker.main()`. | **STANDS** |
| C4 | §5.3: `swe_pool.jsonl` "currently disconnected from F2" | Zero F2-module references; zero doc designation; no task-input contract | **CONFIRMED CORRECT.** Disconnection is *contract-level*, not *disk-level*. | **STANDS** |
| C5 | §4: isolation ownership = parent orchestrator (Option A) | Design §10 row 0, §13.5:679, §739 rejecting adapter ownership | **CONFIRMED CORRECT.** | **STANDS** |
| C6 | §4.3/§4.3 cross-ref: *"The F1 `_eval_swe` defect … remains real but separate — it is not on the F2 path"* | `evaluate_and_promote_eligible` `prompt_repairer.py:860-869` → `evaluate_candidate` `:679` → `_eval_swe` `:616`; `promote` reached from production `_commands_ai.py:677` | **PARTIALLY CORRECT — see §12.** Not on the **F2** path (C3 holds), but **it IS on the governed learning path** that must produce ACTIVE lesson L. The earlier audits dismissed it as "separate"; that framing was incomplete. | **CORRECTED** |
| C7 | §9.G / `2bd90b5c` §2.6: "`n` asserted frozen but is not" | `EXPERIMENT_J.md:201-202` — δ and test both "pre-registered at F1". `EXPERIMENT_J_F1_AUTHORIZATION.md:42` — "20 independent no-lesson pilot runs"; `:107` — "20 independent observations". `LEARNING_EXPERIMENT_STATE.md:38-46` lists only pilot parameters. | **CONFIRMED CORRECT, and no contradiction found in `LEARNING_EXPERIMENT_STATE.md`.** Re-read at lines 36–46: the state document lists **pilot** parameters only and never claims a confirmatory `n`/δ/test. The earlier "asserts frozen" characterisation was **too strong**. | **CORRECTED** |
| C8 | §7 invariant 4: *"No reset exists"* for F2 | Same as C3 | **STANDS.** F2 has no reset. The `git clean -fd` finding belongs to F1's `_reset_instance` (`cli_baseline_swe.py:242-244`), not to F2. | **STANDS** |

## 12. `4c6a05b3` — Treatment-Artifact Impact

`4c6a05b3` added `_rank_active()`, `exclude_ids`, and `_client_query_active()`
to `swarm_os/services/lesson_manager.py`.

### 12.1 What changed — PROVEN

| | Before `4c6a05b3` | After |
|---|---|---|
| Selection | `get_all()` → filter `superseded_by` → sort `(effectiveness, version)` | `_rank_active(task_context)`: same filter, then score `relevance × effectiveness`, sort `(score, effectiveness, version, id)` |
| Relevance | **none** — `task_context` declared but unread | `_embed(task_context)` → `_client_query_active()`; score per hit; **failure degrades to governed order** |
| `exclude_ids` | absent | applied **before** packing |

### 12.2 Answers, from code

1. **Before** — static order by `(effectiveness, version)`; `task_context` unused.
2. **After** — relevance-weighted order with deterministic tie-break.
3. **Relevance** — `_client_query_active(vector)` dense score against the `ActiveLessons` collection, `limit=MAX_RULES*2`, `score_threshold=MIN_CONFIDENCE` (`_client_query_active`). Absent query or any exception → all scores `0.0`.
4. **`exclude_ids` removes** — named lessons from **eligibility**, filtered before packing; also withholds the request-scoped eval snapshot when `ctx.candidate_id` matches.
5. **Who supplies it** — **nobody.** `exclude_ids` defaults to `None` at every F2 call site; no caller passes it. Grep for `exclude_ids` finds the parameter and the F2 freeze docstrings that name it as a known gap (`f2_freeze.py:5`, `f2_arm_primitives.py:117`).
6. **Can it alter the treatment artifact?** **YES — via relevance, not `exclude_ids`.**
7. **Can it alter T?** **YES.** `run_f2_arm` → `render_t()` → `render_active_lessons_with_records(task_context, …)` (`f2_arm_orchestrator.py:106-108`). Ordering feeds `LessonEntry.position`, so relevance changes T's frozen order.
8. **Can it alter X?** **YES, transitively.** `derive_x_from_frozen` removes L from T and preserves remaining positions — so T's ordering determines X's.
9. **Can it alter C0?** **NO.** C0 is built by `build_c0_artifact` with no retrieval.
10. **On a production F2 path?** **YES, for T and X.**

### 12.3 Consequence — NOT ESTABLISHED

`EXPERIMENT_J.md:9` freezes T as *"the exact `active_block` string produced by `LessonManager.render_active_lessons()` **at F2**"* — so T is defined by live retrieval **at freeze time**, not pinned independently of it.

`4c6a05b3` therefore changed the function that *defines* T, one commit before
the F2 isolation audit, without a scientific-review record naming it.
`EXPERIMENT_J.md:16` lists "Change-Control Rule" protected elements — research
question, contrast, T/X definitions, treatment-artifact identity, endpoint,
rediscovery, fresh-worker, contamination, exclusion rules. **The *mechanism*
that computes T is not enumerated**, so `4c6a05b3` is arguably not a §16
violation — but that is my reading, not an established fact, and it is a
scientific question for the operator.

Two further points, both **NOT ESTABLISHED**:

- **Reproducibility.** T's ordering now depends on embedding output at freeze
  time. The manifest records the resulting `position`/`content_address`, so a
  frozen artifact is self-describing — but *re-deriving* T later requires the
  same embedder state. Frozen **replay** is unaffected (`f2_replay` installs
  the stored artifact).
- **`exclude_ids` is inert but load-bearing.** No caller supplies it, so today
  it changes nothing; it exists because the F2 freeze docs name it as required.
  Its semantics (withhold *before* pack, so withholding cannot promote a
  budget-excluded lesson) were designed for exactly the X-arm derivation F2
  needs.

## 13. `BenchmarkEvaluator` — Is It On The ACTIVE-Lesson Path?

**Yes. PROVEN.**

```
evaluate_and_promote_eligible()      prompt_repairer.py:834   (autonomous tick)
  └─ res = await self.evaluate_candidate(cid)                  :860
       └─ if self._is_swe_task(task_id): return await self._eval_swe(...)   :679
            └─ baseline → candidate, same repo dir            :633-634
                 └─ run_repair_task.py preflight (:289) aborts on dirty tree
                      └─ _run_swe_harness returns None → _eval_swe RAISES :636
  └─ if res == "evaluation_passed": await self.promote(cid)    :867-869
```

`promote()` is also reachable from production CLI `_commands_ai.py:677`.

### 13.1 Causal trace — every step classified

| # | Question | Answer | Class |
|---|---|---|---|
| 1 | Does baseline modify the workspace? | **Yes.** The agent edits via the `filesystem` tool; `git` is read-only (`tool_executor.py:1312` allows only `status/log/diff/diff-stat/show/branch`). | **PROVEN** |
| 2 | Does candidate use the same workspace? | **Yes.** `_run_swe_harness:313-314` computes the same `repo = work / instance_id / "repo"` for both arms; env is not mutated between calls. | **PROVEN** |
| 3 | When does preflight run? | `_preflight_target_state` at `run_repair_task.py:289`, **before** the reset at `:414`. | **PROVEN** |
| 4 | When does reset run? | `cls._reset_instance(inst, hf_inst)` at `:414` (and `:359` for the sanity check). | **PROVEN** |
| 5 | Does reset apply a test patch? | **Only if `test_patch` is non-empty** (`cli_baseline_swe.py:245-252`). `_run_swe_harness:332-342` does **not** pass `--test-patch`, so `test_patch=""` and **no patch is applied**. | **PROVEN** |
| 6 | Does reset leave the tree dirty? | `git reset --hard base` + `git clean -fd` (`cli_baseline_swe.py:241,244`). With no patch applied, the **tracked** tree returns to base; **ignored** residue (`.pytest_cache/`, `__pycache__/`, `*.egg-info/`) survives, since `-fdx` is deliberately avoided. | **PROVEN** |
| 7 | Can the pair complete? | **Only when the baseline arm leaves a clean tracked tree.** All 14 repos are currently dirty (`M tests/test_package.py` etc.) — so **every** `_eval_swe` invocation aborts at preflight `[1/6]` today. | **PROVEN** (observed state) |
| 8 | Under what exact conditions does it abort? | `_preflight_target_state` returns errors when `HEAD != base_commit` **or** `git status --porcelain` is non-empty (`run_repair_task.py:98-117`) → `return 2` → `_run_swe_harness` `None` → `_eval_swe` raises `"missing SWE harness result"`. | **PROVEN** |
| 9 | Does this affect the governed learning path? | **Yes.** `evaluate_candidate` is the sole evaluator for SWE tasks; a raise means no `evaluation_passed`, so `promote()` is never reached and **no ACTIVE lesson L can be produced**. | **PROVEN** |

### 13.2 Precise characterization

**This is NOT an F2 problem.** C3 stands: F2 does not import `_eval_swe`.
It is a **learning-path** blocker: F2 requires a genuine ACTIVE lesson L
(`EXPERIMENT_J.md:14`, `:186`), and L requires promotion, and promotion for a
SWE task requires `_eval_swe` to return.

**The blocking condition is environmental, not a logic defect.** The abort path
is correct fail-closed behaviour given a dirty tree. The defect is that no
component resets the tree *before* preflight runs — the reset at `:414` is
downstream of the check at `:289`.

**Scope note.** The observed dirt is exactly the evaluator test files
(`tests/test_package.py`, `tests/test_commands.py`,
`pyfakefs/tests/fake_pathlib_test.py`) — consistent with residue from an
earlier `--test-patch` invocation, **not** with agent edits. That distinction is
**INFERRED** from file selection; no residue provenance is recorded.

**Not investigated here:** whether the dirty state is *why* no promotion has
ever succeeded, or whether preflight ordering has always been this way.
`git log` on `run_repair_task.py` shows `6e024a00` (2026-09-19) *"remove
duplicate git reset from run_repair_task in favor of `_reset_instance`"* —
**INFERRED, not established**, that this is where preflight/reset ordering was
established.

## 14. SWE Repository Facts — Reproducible

Command:
```powershell
Get-ChildItem C:\Users\rober\Projects\swe_probe_work -Directory | Select-Object -ExpandProperty Name
(Get-Content qwen_train\curriculum\swe_pool.jsonl).Count
```
Result: **34 directories**, pool = **14 rows**. All 14 pool `instance_id`s are
present. Per-repo probe at `<id>/repo`:

| Fact | Count |
|---|---|
| Pool rows | 14 |
| Directory `<id>` exists | **14 / 14** |
| Git checkout (`.git` present at `<id>/repo`) | **14 / 14** |
| `HEAD == base_commit` | **14 / 14** |
| Clean (`git status --porcelain` empty) | **0 / 14** — all DIRTY |
| `usable: true` in pool | 14 / 14 |
| Usable by the harness **as-is** | **0 / 14** — all abort at preflight |

**These are five distinct facts.** Existence, being a Git checkout, being at the
declared commit, being clean, and being usable by the harness are separate, and
only the last fails.

---

## 15. Statistical-Design State — Re-verified

| Element | Class | Evidence |
|---|---|---|
| Evaluation population | **NOT PROVEN** | No task-input contract; `swe_pool` disconnected (C4) |
| Paired unit | **REPORTED** | `EXPERIMENT_J.md:198`, `:203` |
| Confirmatory `n` | **NOT PROVEN** | `EXPERIMENT_J_F1_AUTHORIZATION.md:42`, `:107` — 20 is the **pilot** count. No confirmatory `n` exists. |
| δ / practical effect | **NOT PROVEN** | `EXPERIMENT_J.md:201` — *"Risk difference ≥ δ (pre-registered at F1, e.g., δ = 0.15)"*. Placeholder survives. |
| Statistical test | **NOT PROVEN** | `EXPERIMENT_J.md:202` — *"Pre-registered at F1 (e.g., Fisher's exact…)"*. Placeholder survives. |

**Correction to `2bd90b5c` §2.6 / C7:** that audit claimed
`LEARNING_EXPERIMENT_STATE.md:23` "asserts frozen." Re-reading lines 36–46, the
state document lists **pilot** parameters (F1-OP-001 … F1-OP-004b) and does
**not** claim a confirmatory `n`, δ, or test. The audit's characterisation was
**too strong**. The underlying gap is unchanged: F0 assigned these to F1; F1 did
not record them; they remain unfrozen. **No contradiction between documents was
found** — there is a *silent gap* in F0/F1, not a conflicting claim.

---

**END OF PART II — CORRECTIONS**
