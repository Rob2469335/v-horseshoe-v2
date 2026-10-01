# Experiment J — F2 Step 5 Audit

**Status:** AUDIT RECORD — NO IMPLEMENTATION AUTHORIZED
**Date:** 2026-09-30
**Baseline at audit time:** `master` @ `4c6a05b`
**Scope:** Read-only audit. No code was modified. No F2 arm was run. No experimental semantics were changed.

This document records the Step 5 (statistical design) audit, including the
correction of four false findings from an earlier report produced in the same
session. The corrections matter: the earlier numbers were treated as evidence
and would have driven a wrong design decision.

---

## 0. Evidence Labels

Every claim below carries one of:

| Label | Meaning |
|-------|---------|
| **PROVEN** | Directly verified in the repository at a cited file:line in this session. |
| **INFERRED** | Follows from PROVEN facts by a stated reasoning step. Not itself directly observed. |
| **REPORTED** | Stated by an authoritative document; not re-verified here. |
| **UNKNOWN** | Not established by this audit. Requires an explicit decision or new evidence. |

---

## 1. Corrections To Earlier False Findings

An earlier report in this session asserted four things. All four were
measurement or field-name errors. They are retracted here.

### 1.1 RETRACTED — "3,759 records with `verdict: null`"

**There is no such corpus.** A literal search for `"verdict": null` across all
94 files in `qwen_train/results/` returns **zero hits**.

The 3,759 figure was an artifact of PowerShell semantics: the expression
`$o.verdict -eq $null` evaluates true for an **absent** key, not only for an
explicit null. The 3,759 records are records from **different corpora with
different schemas** that simply have no `verdict` field — predominantly
curriculum CLI rollouts.

### 1.2 RETRACTED — "2,062 distinct `instance_id`"

**The field `instance_id` does not exist in these records.** The 2,062 count
was of the `id` field. Of those: 2,036 are curriculum IDs (`c01`…) from a
tool-selection corpus; **exactly 1** is a SWE instance
(`pypa__twine-1066`). All genuine F1 observation records carry
`id = f1_pilot/*`.

### 1.3 RETRACTED — "3,759 null-attribution records (no `run_id`/`interpretation`)"

**False.** Within the same corpus, 22 records carry `run_ids`, 23 carry
`interpretation`, and **19 carry both**. Example:
`f1_diagnostic_extended.jsonl` has `run_ids` and
`interpretation.validity = VALID`.

Further, the 26 F1 observation records are **legitimately excluded, not
silently unattributable**. Each carries an explicit verdict plus a machine
classification from `f1_infra.classify_observation` (`validity_infrastructure`
= `INVALID`, credit `+0/-0`). That is F0 §12's pre-registered exclusion
semantics operating as designed. The earlier report conflated *legitimately
excluded* with *silently unattributable*.

Only **4** records (`f1_obs_1`, `f1_observation_1`, `f1_observation_2`,
dated 2026-09-25, predating the classification wiring) are genuinely
uninterpretable.

### 1.4 CORRECTED — what the 3,838 records actually are

The 3,838 record total is correct. They are **heterogeneous training and
telemetry records spanning 2026-08-30 → 2026-09-26**, covering curriculum
tool-selection, exam scoring, symbol tracing, and F1 repair observations.

They are **not an F2 population** and must never be used to define one. There
is **zero overlap** between the F1 pilot population (`f1_pilot/*`) and
`swe_pool.jsonl`.

---

## 2. The F2 Evaluation Population

### 2.1 F2 POPULATION IS UNDEFINED — **PROVEN**

A search for `universe`, `swe_pool`, `task pool`, and `task list` across
`docs/EXPERIMENT_J.md`,
`docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md`,
`docs/EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md`,
`docs/EXPERIMENT_J_F1_AUTHORIZATION.md`, and
`docs/LEARNING_EXPERIMENT_STATE.md` yields:

- `universe` — **zero hits** in any `docs/*.md`.
- `swe_pool` — **zero hits** in any `docs/*.md`.

`docs/EXPERIMENT_J.md:198` states the unit of analysis as
*"Rollout (one trajectory per arm per task)"* and `:203` pairs *"by task +
rollout seed"*, but **no document names which tasks, how many, or from what
source.**

`docs/EXPERIMENT_J.md:187` assigns F2 the freezing of "genuine L, exact ordered
treatment material, relevant state" — **the task population is not in that
list.** A task list was never among the F0 §16 protected elements, so
designating one later is *permitted*; it is currently simply **absent**.

### 2.2 `swe_pool.jsonl` is NOT designated — **PROVEN**

`qwen_train/curriculum/swe_pool.jsonl` is tracked, introduced by `25dbd7c6`
(2026-09-15, "Initial SWE-rebench-V2 pool (14 verified USABLE tasks)") and
pinned by `fcc31de3`. It contains **14 rows**.

**No document states it is the F2 universe.** It is *available*, not
*designated*.

One row = one SWE-rebench-v2 instance (`build_swe_pool.py:7`:
"one record per USABLE instance"). Fields include `instance_id`, `repo`,
`base_commit`, `test_cmd`, `fail_to_pass`, `pass_to_pass`, `split`,
`f2p_base`, `f2p_gold`, `usable`. There is **no task/family grouping field**.

**Composition (PROVEN):** 14 instances across 14 distinct repositories —
exactly one instance per repo. All `usable: true`, all `f2p_base` fully
failing and `f2p_gold` fully passing.

**Caution — PROVEN:** the `split` field is **`eval` ×3 / `train` ×11**,
inherited verbatim from upstream SWE-rebench-v2. It is **not** an Experiment J
design split. If `swe_pool` is ever designated, this upstream split is
inherited, not designed. (`run_repair_task.py:331` separately hardcodes
`"split": "repair"` — a third, unrelated value.)

### 2.3 Only 3 of 14 pool repos are checked out — **PROVEN**

Present under `swe_probe_work/`: `pypa__twine-1066`,
`pallets__click-2380`, `pytest-dev__pyfakefs-916`.

`_run_swe_harness` (`prompt_repairer.py:315`) returns `None` when
`(repo / ".git")` does not exist, so **11 of 14 fail closed today**. See §4
for whether `_run_swe_harness` is on the F2 path at all.

### 2.4 The 3 present repos are dirty at their base commits — **PROVEN**

Observed on disk: each repo is at its `base_commit` with a modified file —
`tests/test_package.py` (twine), `tests/test_commands.py` (click),
`pyfakefs/tests/fake_pathlib_test.py` (pyfakefs). Combined with §4, an arm
launched against any of these aborts at preflight.

### 2.5 The experimental unit — **REPORTED**

`docs/EXPERIMENT_J.md:198` (verbatim):

| Element | Specification |
|---------|---------------|
| Unit of analysis | Rollout (one trajectory per arm per task) |
| Pairing | T/X paired by task + rollout seed; C0 independent |

**A task contributes 2 rollouts (T and X). The unit of analysis is a
rollout; the number of tasks is a free parameter that has never been set.**

### 2.6 `n` is asserted frozen but is not — **PROVEN**

`docs/LEARNING_EXPERIMENT_STATE.md:23` claims F1 froze "k, n,
practical-effect". The F1 authorization's "20" is the **pilot** count
(`EXPERIMENT_J_F1_AUTHORIZATION.md:42`), not a confirmatory `n`.
`docs/EXPERIMENT_J.md:201-202` both say "pre-registered at F1" for δ and the
statistical test; F1 did neither. **No confirmatory `n`, `δ`, or test exists in
the repository.**

---

## 3. Three Concepts, Separated

| Concept | Definition | Status |
|---------|-----------|--------|
| **A. Evaluation population** | The task set F2 will be run on | **UNDEFINED** (§2.1) |
| **B. Experimental / paired unit** | One unit of causal evidence: a rollout, paired by task | **REPORTED** — `EXPERIMENT_J.md:198`, `:203` (§2.5) |
| **C. Observed telemetry corpus** | The 3,838 records in `qwen_train/results/` | **NOT a population** — heterogeneous telemetry, zero overlap with A or the F1 pilot (§1.4) |

**The 3,838-record corpus must not be allowed to define A by default.**

---

## 4. F2 Execution Call-Path Verification

This section was added at the explicit request that the F2 path be established
by import/call tracing, not by filename or documentation inference.

### 4.1 Traced path — **PROVEN**

```
f2_arm_orchestrator.run_arm()            qwen_train/f2_arm_orchestrator.py
  │  freeze T → derive X → persist manifest
  │  env: SWARM_F2_REPLAY, SWARM_F2_MANIFEST_PATH, SWARM_F2_REPO_ROOT,
  │       SWARM_F2_ROLLOUT_ID, SWARM_F2_TRAJECTORY_RUN_ID   (:205-210)
  │  subprocess.Popen([python, -u, f2_arm_worker.py, --manifest, --arm])  (:212-221)
  ▼
f2_arm_worker.py                         qwen_train/f2_arm_worker.py
  │  install_replay_state, verify manifest
  │  exec_cmd = os.environ["F2_ARM_EXEC_CMD"]             (:153)
  │  if exec_cmd: subprocess.run(exec_cmd, shell=True)   (:184-187)
  │  else: record execution as "delegated"               (:197-198)
  ▼
F2ExecutionAdapter                       qwen_train/f2_execution_adapter.py
  │  backend_starter  → f1_infra.start_backend_fresh(workspace_root=…)  (:507-512)
  │  env propagation to P2 via os.environ.copy()
  │  cli_runner → run_curriculum._attempt_once(item, …)   (_default_cli_runner :421-423)
  ▼
run_curriculum._attempt_once()           qwen_train/run_curriculum.py:270
  subprocess.Popen([python, -m, organism_console, --json, prompt])
  ▼
fresh P2 (backend :8000) → agent_service_v2 → f2_replay delivery seam
```

### 4.2 `_eval_swe` is NOT on the F2 path — **PROVEN**

A search across all `qwen_train/f2_*.py` and `runtime_v2/services/f2_*.py`
for `_eval_swe`, `run_repair_task`, `_run_swe_harness`, `swe_rebench_probe`,
and `swe_pool` returns **zero matches**.

`run_repair_task.py` is referenced only by `prompt_repairer.py:334`
(inside `_run_swe_harness`), `run_f1_batch_9_24.py`, `run_controls.py`, and
tests. **No F2 module imports or invokes it.**

The sole non-test reference to any F2 module outside `qwen_train/` is a
**docstring** in `runtime_v2/api/agent_service_v2.py:186` (added by commit
`660323dc`), which cites `f2_execution_adapter.py` as the propagation source.
It is not an import.

### 4.3 `_eval_swe` / `run_repair_task.py` nonetheless have a real defect — **PROVEN**

This is stated separately and must not be conflated with §4.2.

`prompt_repairer.py:633-634` runs the two arms sequentially with **no reset
between them and no exception handling around the pair**:

```python
base = await self._run_swe_harness(swe, ps, eval_id=None, arm="baseline")
cand = await self._run_swe_harness(swe, ps, eval_id=eval_id, arm="candidate")
```

`_run_swe_harness:313-314` computes the same
`repo = work / instance_id / "repo"` for both arms (derived from the same
`swe` dict and an environment that is not mutated between calls) — **PROVEN
same directory.**

The candidate does **not** inherit baseline mutations, because
`run_repair_task.py:289` runs `_preflight_target_state`, which aborts
(`return 2`, `:294`) on a dirty tree — and that preflight runs **before** the
reset at `:414`. So the reset is unreachable in exactly the state it would
need to repair. `prompt_repairer.py:635-636` then raises
`"missing SWE harness result"`.

**INFERRED:** the paired evaluation is therefore inoperative whenever the
baseline arm edits the workspace — i.e. whenever baseline is informative — and
"works" only when baseline made no edits, which is the uninformative case.

**PROVEN, separate defect:** `_reset_instance` runs `git clean -fd`, not
`-fdx` (`cli_baseline_swe.py:242`), so gitignored residue
(`.pytest_cache/`, `__pycache__/`, `*.egg-info/`) is never removed between
arms even when preflight passes.

**This defect blocks the F1 promotion-gate evaluation path, not the F2 arm
delivery path.** Correcting it is a separate decision.

### 4.4 Arm isolation: specified but not implemented — **PROVEN**

`docs/EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md:679-680` (§13.5) specifies:

> **Workspace reset**: on a fresh isolated clone; `git reset --hard` per arm,
> `.session.json` deleted (F0 §8), HEAD = frozen base.

The implemented adapter does not do this. `qwen_train/f2_execution_adapter.py`
contains no `git reset`, no clone, and one docstring mention of
`_reset_instance` that is **never called**. Workspace handling is limited to
`env["SWARM_WORKSPACE_ROOT"] = str(workspace_root)` (`:229`, `:509`).

`f2_arm_orchestrator.py:19` guarantees *"One fresh child process per arm … No
multi-arm reuse."* — **process isolation only.** `subprocess.Popen` per arm
(`:215`) gives a fresh Python process and a fresh backend, but **both arms
receive the same `workspace_root` string.** Process isolation is not
filesystem or worktree isolation.

**The F2 design already specifies per-arm isolated clones. No authorization or
implementation currently delivers it.**

---

## 5. Blocking And Open Items

### BLOCKER (F2 arm delivery path)

- **Arm filesystem isolation is not implemented** (§4.4), though the F2 design
  specifies it. **PROVEN.**

### BLOCKER (F2 scientific readiness)

- **The evaluation population is undefined** (§2.1) and **`n`, δ, and the
  statistical test are not established** (§2.6). **PROVEN.**

### OPEN (F1 path — separate decision)

- **`_eval_swe` paired design is inoperative when baseline edits** (§4.3).
  **INFERRED** from **PROVEN** facts.
- **`git clean -fd` leaves gitignored residue between arms** (§4.3).
  **PROVEN.**

### UNKNOWN — requires an explicit decision, not an engineering fix

- Which task set constitutes the F2 population.
- The confirmatory `n`, δ, and statistical test.
- Whether `swe_pool.jsonl`'s upstream `eval`/`train` split is acceptable if
  the pool is designated.

---

## 6. Required Next Step

**No implementation is authorized by this document.** The two blocking items
require decisions, not engineering:

1. **Designate the F2 evaluation population** — the repository does not define
   one. This is a scientific-design decision under F0 §16 change control.
2. **Authorize per-arm isolated clones** in the F2 adapter, implementing what
   `EXPERIMENT_J_F2_ORCHESTRATOR_DESIGN.md:679` already specifies. This closes
   the F2 blocker without touching F1 harness code.

The F1 `_eval_swe` defect (§4.3) is real but is **not** the F2 blocker, and
fixing it should not be bundled with the F2 work.

Per `WORK_LOG.md`'s standing rule — *"a 0/N score is not a result until it is
attributable"* — no statistical design may be finalized before the population
exists and the arms are independently reset.

---

**END OF AUDIT RECORD**
