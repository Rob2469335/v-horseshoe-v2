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

**END OF AUDIT RECORD**
