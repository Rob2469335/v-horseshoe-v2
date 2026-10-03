# Experiment J — Learning-Event Task Readiness and Population Contract

**Status:** OPERATIONAL CONTRACT — does not modify F0, F1 scientific parameters, or
any frozen document.
**Date:** 2026-10-03
**Scope:** Task readiness definition, and designation of a learning-event task
population.
**Supersedes:** the `AssertionError → READY` rule recorded in
`docs/LEARNING_EXPERIMENT_STATE.md` D-10 (commit `1be4e955`).
**Operator:** Rob (human operator).

---

## 0. Why this document exists

`docs/EXPERIMENT_J_F2_ISOLATION_POPULATION_AUDIT.md:39-42` established that the F2
task population is `NOT PROVEN` because `run_f2_arm()` accepts no repository, base
commit, test command, fail-to-pass set or workspace. That audit predates the
2026-10-03 readiness preparation, which produced five validated candidate tasks.
This document supplies the missing readiness definition and population
designation, and **retracts** the assertion-error rule that briefly occupied the
gap.

---

## 1. RETRACTION — the `AssertionError → READY` rule

**The rule is retracted. It had no authority and no implementation.**

| Evidence | Finding |
|---|---|
| `git log --all -S 'AssertionError' -- docs/` | Exactly one commit: `1be4e955` (this operator session). Absent at `dcdf75a2`. |
| `docs/EXPERIMENT_J.md` | Zero occurrences of `FAIL_TO_PASS`, `AssertionError`, `SWE-bench`, `test_patch`, `gold_patch`, `assertion`. |
| `runtime_v2/api/evaluation_bridge.py` (pre-fix) | Readiness = `has_failures = "FAILED" in output` (`:466-471`). No exception-type inspection. |
| `qwen_train/cli_baseline_swe.py::_test_result` | Env-failure detection is collection-crash / no-summary-and-no-ids (`:216-224`). No exception taxonomy. |
| SWE-bench `swebench/harness/grading.py::test_failed()` | Treats `ERROR` and `SKIPPED` identically to `FAILED` for F2P. `AssertionError` appears nowhere in the harness. |
| `docs/F1_TASK_ENVIRONMENT_INVENTORY.md:35-36` | Already declared a `werkzeug.exceptions` failure a **valid, unsolved** task. |

A rule contradicting the repository's own most recent operational record, its
evaluator, and the external methodology it claims to follow, was never scientific
authority. It is withdrawn, not refined.

---

## 2. READY — the definition adopted

A task is **READY** iff all eight hold. R1–R4 are task-layer, R5–R6 run-layer,
R7 program-layer, R8 F2-layer. They are distinct questions and are not collapsed
into one scalar.

**R1 — Task validity.** The declared `FAIL_TO_PASS` node id is reported
**non-passing** (`FAILED`, `ERROR` or `SKIPPED`) by the declared `test_cmd`, run
with the task's own interpreter, at `base_commit` + `test_patch`, in a suite that
actually ran (a summary line exists and at least one test id was emitted).
*No exception-type requirement.*

**R2 — Gold reachability.** The **same** node id is `PASSED` under the
authoritative gold patch, same base commit, same environment, same interpreter.

**R3 — No regression.** Zero declared `PASS_TO_PASS` tests transition to
`FAILED`/`ERROR` between base and gold.

**R4 — Not a confirmed environment fault.** No `TIER_ENVIRONMENT` signature:
browser-launch failure, display unavailable, OOM/`Killed`, container unavailable,
network unreachable. Suite-level apply-patch failure, reset failure, suite error
or timeout ⇒ invalid. `TIER_AMBIGUOUS` signatures (missing module, zero
collected, per-test timeout) are **not** disqualifying — record and adjudicate,
per SWE-bench `infra_failure.py`, which is advisory and non-excluding.

**R5 — Evidence provenance.** Base and gold runs archived outside the agent
workspace with SHA-256. A substitute probe (`swe_rebench_probe.probe()`) does
**not** satisfy this; it does not run `_test_result`, does not evaluate P2P, and
is labelled as substitute evidence.

**R6 — Learning-signal sufficiency.** The run classifies `BEHAVIORAL` under
`evaluation_bridge.classify_evaluation_failure`, which since `5f703869` gates on
the authoritative evaluator verdict first: `evaluator_passed=True → SOLVED`
(never learning evidence), `None → UNKNOWN`, `env_error`/`regression →
INFRASTRUCTURE`. Only an evaluator-confirmed **failure** may enter the
learning-failure channel.

**R7 — Promotion reachability.** The event spans **≥2 distinct tasks** and ≥3
unique `rollout_id`s for one hypothesis, else `MIN_EVIDENCE_TASKS = 2`
(`prompt_repairer.py:1432`) makes `ACTIVE` unreachable.

**R8 — Endpoint measurability.** A frozen `relevant_file_set` exists for the task.
Currently unmet for every SWE task: the only definition is
`F1_RELEVANT_FILE_SET = frozenset({"swarm_os/lib/paths.py"})` (`f1_infra.py:144`),
hardcoded to the F1 pilot. See §6.

---

## 3. Designated learning-event population

Designated from `qwen_train/curriculum/swe_pool.jsonl`. All five are declared
`base_image_name = python_base_310` → **Python 3.10.11**. All five are
docker-free-usable (`probe()` verdict `USABLE (docker-free)`).

| Task | repo | base_commit | F2P | P2P | R1 | R2 | R3 | R4 |
|---|---|---|---|---|:-:|:-:|:-:|:-:|
| `databricks__dbt-databricks-935` | databricks/dbt-databricks | `4b1d2d99b0` | 1 | 17 | ✅ | ✅ | ✅ 0 broken | ✅ |
| `sinaptik-ai__pandas-ai-1099` | Sinaptik-AI/pandas-ai | `ad67fc66da` | 1 | 2 | ✅ | ✅ | ✅ 0 broken | ✅ |
| `qiskit__qiskit-ibm-runtime-367` | Qiskit/qiskit-ibm-runtime | `f1aac5bc44` | 1 | 20 | ✅ | ✅ | ✅ 0 broken | ⚠ see 3.1 |
| `pallets__werkzeug-2583` | pallets/werkzeug | `1ce57f64c9` | 1 | 114 | ✅ | ✅ | ✅ 0 broken | ✅ |
| `xknx__xknx-470` | XKNX/xknx | `715856fdb6` | 1 | 136 | ✅ | ✅ | ✅ 0 broken | ✅ |

R5 is **substitute-probe only** for all five. R6, R7 and R8 are unmet pending
§5 and §6.

Observed F2P base failure types — recorded for provenance, **not** as a
criterion: `AssertionError`, `KeyError`, `ERROR`/`TypeError`,
`werkzeug.exceptions.*`, `TypeError`. All five fail at base and pass at gold on
the same node id, with every declared P2P test green at both states.

### 3.1 qiskit-ibm-runtime-367 — required mitigation

Its declared `test_cmd` also runs `test/integration/test_retrieve_job.py`, which
errors 13× at setup (`AttributeError: 'NoneType' object has no attribute 'find'`)
for want of IBM Quantum credentials. Those errors are **identical at base and
gold** and lie outside the declared F2P/P2P sets, so they do not affect R1–R4.

**Condition of designation:** for any learning-event run on this task, the
`--test-cmd` must be narrowed to the unit-test subset
(`test/unit/test_job_retrieval.py`), or the harness's `_test_result` env_error
branch must be confirmed not to fire. Otherwise the run is scored against a
suite whose health the experiment cannot control.

### 3.2 Population minimum

R7 requires ≥2 designated tasks. Any two of the four clean tasks
(`dbt-databricks-935`, `pandas-ai-1099`, `werkzeug-2583`, `xknx-470`) satisfy R1–R4.
`qiskit-ibm-runtime-367` is designated subject to §3.1.

---

## 4. Remediation landed before this contract

Commit `5f703869` — *FIX: preserve the evaluator verdict so a solved task cannot
become learning evidence*. Three defects, one design problem (no authoritative
success signal threaded from production to consumption):

- **D1** `pass_to_pass` was hardcoded `[]` and passed as `[]` into `_test_result`,
  making the regression branch dead code. Fixed; declared P2P now flows from the
  curriculum row.
- **D2** the base FAIL_TO_PASS measurement was printed then ignored, so a task
  whose F2P already passed scored `ok=True` with no agent work. Fixed; the run
  now aborts.
- **D3** the evaluator verdict was accepted and discarded; a successful run
  classified `BEHAVIORAL` and persisted a false failure narrative. Fixed; verdict
  preserved, gated, and written into the audit record inside the first 100
  characters.

Verified by revert-then-pass against HEAD's own classifier
(`BEHAVIORAL → SOLVED`), 152 passing tests, clean ruff on both `runtime_v2`
files, and byte-identical PromptRepairer stores.

---

## 5. OPEN DECISION — lesson authenticity (BLOCKING, scientific)

This is the remaining blocker to a *genuine* learning event and it is **not** an
engineering defect. It is a scientific question this contract does not decide.

**Finding.** `evaluation_bridge._build_hypothesized_action` returns one of exactly
**two** canned strings, selected only by whether the agent attempted an edit:

1. `"edit-failed: The agent attempted a source modification but it did not resolve the failing tests. Review the test failures and the agent's approach to determine whether the modification targeted the correct location."`
2. `"no-edit: After researching the problem and gathering sufficient information, apply the fix with filesystem patch or filesystem write. Do not spend all turns on investigation without transitioning to code modification."`

Executed probe of the real `_derive_learner_artifact` confirms these are the only
two artifacts the evaluation-bridge path can produce. Of the seven
`_CONDITION_KEYWORDS`, only `no-edit` and `edit-failed` are reachable from a
bridge trigger; the other five are unreachable.

Both texts are **task-agnostic** (they name no file, no test, no defect) and
**tautological** (they restate that the run failed). F0 §1 requires "a genuine,
lawfully promoted lesson L". A lesson identical for every task, containing no
defect-specific knowledge, is not obviously that.

**Consequence if unaddressed.** A learning event would execute end-to-end and
promote one of two boilerplate strings. F2 would then compare T (boilerplate
delivered) against X (boilerplate removed) on first-edit timing. That measures
whether generic "review your approach" advice nudges edit latency. It would
produce a lawful `ACTIVE` lesson and a valid-looking experiment that does not
test the F0 research question.

**Deliberately not decided here.** Deriving defect-specific hypotheses would mean
feeding test-failure content into the promoted text. That risks (a) leaking
`test_patch` semantics into an `ACTIVE` lesson, breaching the clean-room
requirement in §11, and (b) converting a behavioral hypothesis into an
implementation-fix disclosure, which `_build_behavioral_prompt`
(`run_repair_task.py:409`) exists to prevent. The current closed vocabulary plus
generic action may well be a deliberate anti-leakage design. Choosing between
them changes what an `ACTIVE` lesson **is** — the treatment artifact in F0's
primary comparison — and therefore requires scientific review, not agent
judgment.

**Options for the operator**

| Option | Content | Trade-off |
|---|---|---|
| **A** | Accept process-hygiene lessons; run the experiment as designed. | Zero new risk. Weak treatment; arguably does not test F0 §1. |
| **B** | Authorize a defect-grounded hypothesis generator, with an explicit clean-room membrane (no test identifiers, no diff hunks, no patch text in the promoted artifact). | Stronger treatment; requires the membrane to be designed and audited before any run. |
| **C** | Defer; produce L by a different governed route. | No new machinery; slower. |

Recommendation: **B**, because A produces a scientifically hollow result and C
buys nothing. B's membrane must be reviewed before use.

---

## 6. OPEN DECISION — F2 endpoint measurability

F0 §5 requires the primary endpoint to be an edit-type action on "a predefined
frozen `relevant_file_set`". Only `{"swarm_os/lib/paths.py"}` exists
(`f1_infra.py:144`), tied to the F1 pilot. None of the five designated tasks has
one.

This does not block the **learning event** (which produces L) but it does block
**F2** (which measures L's delivery). Each task admitted to F2 must carry a frozen
`relevant_file_set` — the source file(s) the declared FAIL_TO_PASS test exercises
— serialized, sorted and SHA-256 recorded before the first trajectory, per F1-OP-003.

`REQUIRES AUTHORIZATION` to designate. `NOT ESTABLISHED` whether
`find_qualifying_first_edit` generalizes beyond the pilot's single-path set.

---

## 7. Historical evidence state

Read-only forensic scan of `data/prompt_repairer_audit.jsonl` (135,755 records):

- **99.0% (134,391) is test-fixture residue** — `cand_fake` (132,805), `forged`
  (1,579), `test_cid_a/b/d`. Written 2026-09-18 → 2026-10-02.
- **Isolation is fixed.** Root `conftest.py::isolate_prompt_repairer_store`
  (commit `417d013c`, 2026-10-01). Zero fixture residue on 2026-10-03. The residue
  is historical, not ongoing.
- **`RECOVERED_INTERRUPTED_PROMOTION` records: 134,384**, all carrying those
  fixture ids. They are test artefacts, not recovery events.
- **Promotion journal: 143 complete transactions** (`begin`/`snapshot_saved`/
  `qdrant_applied`/`committed`), 142 fixture candidate ids including
  `lesson_123`. Archived to `data/_quarantine/` on 2026-10-02.
- **Zero `ACTIVE` transitions, ever.** `new_state` census: `CANDIDATE` 5,
  `EVALUATING` 9, `EVALUATION_FAILED` 9, `REJECTED` 3.
- **Five genuine candidates reached `CANDIDATE`** (`140bd53f9430`,
  `a8f69f262bae`, `b576a901b262`, `8366cbfae8d2`, `31d6929a46e6`, on 2026-09-24 and
  2026-09-27) and were **never evaluated** — consistent with
  `evaluate_and_promote_eligible`'s `MIN_EVIDENCE_TASKS = 2` skip. The live
  candidate store is `{}`.
- **The bridge has run historically: 88 `OBSERVED_FAILURE` records** from 5
  distinct run ids (85 `pypa__twine-1066`, 3 `f1_pilot/run_1_fresh`).
- **Whether any of those 88 was a SOLVED run is `NOT ESTABLISHED` and is now
  permanently unanswerable**: `process_failure:1204` audits
  `failure_reason[:100]`, which truncated the F2P verdict sentence. Commit
  `5f703869` places the verdict inside that window for all future runs.

**Therefore:** the historical audit log cannot serve as evidence for or against
the D3 defect's historical impact, and its 99% fixture contamination means it is
not usable as a scientific record at all.

---

## 8. What this contract does and does not authorize

**Does:** define READY (R1–R8); retract the assertion-error rule; designate five
candidate tasks; record the qiskit mitigation; record the D3/D1/D2 remediation.

**Does not:** authorize running the learning event; authorize
`SWARM_RECEIPT_KEY` provisioning; authorize ACTIVE-lesson creation, evaluation or
promotion; designate any F2 population; decide §5 or §6; modify F0, F1
scientific parameters, or any frozen document.

---

## 9. Sequence to a genuine learning event

1. **Decide §5** (lesson authenticity). Blocks everything downstream.
2. **Decide §6** (per-task `relevant_file_set`) — needed before F2, not before the learning event.
3. Provision `SWARM_RECEIPT_KEY` under a new explicit authorization. Currently
   `NOT AUTHORIZED` by `EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md:97,126`.
4. Execute ≥3 `BEHAVIORAL` runs across ≥2 designated tasks, each on an
   evaluator-confirmed failure.
5. Verify `ACTIVE` with full provenance (`promotion_proof` + verified HMAC receipt).
6. Only then freeze F2.

Step 5 requires the receipt key; step 4 does not. Provisioning before step 1
would permit promotion of whatever the current pipeline produces — which §5 shows
is boilerplate.

---

*Frozen F0 (`4EAFD2FAF2BA7907…`) unchanged. No scientific evidence, candidate
state, or Qdrant data was modified in producing this contract.*