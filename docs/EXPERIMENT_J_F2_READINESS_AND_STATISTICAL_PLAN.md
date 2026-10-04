# Experiment J — F2 Readiness and Frozen Statistical Analysis Plan

**Status:** ENGINEERING + METHOD READY — SCIENTIFIC AUTHORIZATION **NOT GRANTED**
**Date:** 2026-10-04
**Engineering baseline:** `1dd07120` (readiness pass), on top of `1b8b4a59`
**Does NOT modify:** `docs/EXPERIMENT_J.md` (F0, `4EAFD2FAF2BA7907…`, frozen),
`docs/LEARNING_EXPERIMENT_STATE.md`, `docs/EXPERIMENT_J_TASK_READINESS_CONTRACT.md`.
This document is subordinate to all of them and adds no scientific authority of
its own. Where it states a decision, that decision requires operator
authorization before it governs anything.

---

## 0. Why this document exists

The engineering readiness audit (`1b8b4a59`) and the scientific-design review
that followed left two kinds of residue:

1. Controls that were **claimed in prose but not enforced in code** — a
   per-task endpoint that only worked for the F1 pilot task, git pseudo-refs
   that survived history stripping, a credential denylist that let bare `KEY`
   variables through, a distiller identity that could not reproduce itself, an
   orchestrator that never established the task workspace.
2. Numbers that existed only in **prose** — a sample size, an effect size, a
   test choice — with nothing in the repository deriving or testing them.

Both are now addressed in code with regression tests, and the derived numbers
are produced by tested functions rather than asserted. This document records
what is actually enforced, what the statistics are, and what still requires the
operator.

---

## 1. What the code now enforces

Every row is backed by a test in the named file. Status vocabulary is the
repository standard (AGENTS.md §3.8).

| # | Control | Implementation | Test | Status |
|---|---|---|---|---|
| 1 | Git future-history, remote refs, tags, reflog stripped | `qwen_train/arm_workspace.py::_strip_future_history` | `test_f2_arm_workspace_isolation.py` | **PROVEN** |
| 2 | Git **pseudo-refs** (`ORIG_HEAD`, `MERGE_HEAD`, `FETCH_HEAD`, …) deleted | same, via `update-ref -d` **and** direct `$GIT_DIR` unlink after gc | `…::test_orig_head_pseudo_ref_is_removed`, `…::test_no_pseudo_ref_survives_stripping` | **PROVEN** |
| 3 | Unreachable-object assertion covers **every** object type, not only commits | same (`unreachable `/`dangling ` match) | `…::test_dangling_blob_carrying_future_content_is_pruned` | **PROVEN** |
| 4 | Executing arm must declare an **explicit** workspace; no code-root fallback | `arm_workspace.py::resolve_required_workspace_root` | `test_f2_arm_workspace_isolation.py::TestWorkerRejectsUndeclaredWorkspace`, `test_f2_workspace_root_governance.py` | **PROVEN** |
| 5 | Orchestrator resolves/validates the workspace **itself** and injects it into the child env | `qwen_train/f2_arm_orchestrator.py::run_f2_arm` | `test_f2_workspace_root_governance.py` (7) | **PROVEN** |
| 6 | `web_search`/`web_fetch` **DENY** under `SWARM_F1_NO_WEB_TOOLS=1`, prompt-independent | `swarm_os/services/approval_registry.py` | `test_f1_mcp_denylist.py` (8 new) | **PROVEN IN CURRENT REVISION** |
| 7 | Learning-actor env membrane strips bare `KEY`/`*_KEY` credentials | `security_gate.py::is_credential_env_name` | `test_learning_env_membrane.py` (35) | **PROVEN IN CURRENT REVISION** |
| 8 | Learning-actor env is **allowlist**-based (allowlist cannot smuggle a credential) | `security_gate.py::clean_learning_env` | same | **PROVEN IN CURRENT REVISION** |
| 9 | Per-task endpoint is canonicalized, **hash-bound**, horizon-bounded to [8,12] | `qwen_train/f2_endpoint.py::FrozenEndpoint` | `test_f2_endpoint.py` (45) | **PROVEN** |
| 10 | Endpoint **fails closed** on missing/unsafe/mismatched set; never defaults | same | same | **PROVEN** |
| 11 | Endpoint refuses a **treatment-derived** or evidence-free derivation | `f2_endpoint.py::DISALLOWED_DERIVATION_SOURCES`, `assert_treatment_independent` | same | **PROVEN** |
| 12 | `find_qualifying_first_edit` has **no F1-only assumption**; set + horizon are parameters; horizon enforced | `qwen_train/f1_infra.py` | `test_f2_endpoint.py` | **PROVEN** |
| 13 | Distiller identity carries weights digest, prompt digest, sampling config, code version | `swarm_os/services/lesson_distiller.py::DistillerIdentity` | `test_distiller_reproducibility.py` (19) | **PROVEN** |
| 14 | Distiller **fails closed** without a weights digest unless absence is explicitly acknowledged; temperature frozen at 0.0 | `…::assert_reproducible` | same | **PROVEN** |
| 15 | Recorded sampling config **is** the executed request config | `…::_http_openai_complete` | `…::TestSamplingConfigIsWhatIsSent` | **PROVEN** |
| 16 | Persisted attestation carries the full reproducibility block | `lesson_synthesis.py::SynthesisAttestation.distiller_reproducibility` | `…::TestAttestationCarriesTheRecord` | **PROVEN** |
| 17 | Population admission is machine-checkable; every rule's verdict retained | `qwen_train/f2_population.py::screen_entry` | `test_f2_population.py` (25) | **PROVEN** |
| 18 | A task with **no** frozen endpoint is **rejected**, not admitted with a default | same (S6) | `…::TestR8IsEnforcedNotDefaulted` | **PROVEN** |
| 19 | Population entry identity hash excludes mutable annotations | `PopulationEntry.identity_payload` | `…::TestHappyPath` | **PROVEN** |
| 20 | Calibration refuses a lesson-bearing arm | `qwen_train/f2_calibration.py::CalibrationRecord` | `test_f2_calibration.py` (27) | **PROVEN** |
| 21 | Calibration reruns only for predefined **infrastructure** causes | `…::INFRASTRUCTURE_RERUN_CAUSES` | `…::TestInvalidAndRerunRules` | **PROVEN** |
| 22 | Calibration compares only records sharing one endpoint spec | `…::summarize_calibration` | `…::TestSummary` | **PROVEN** |
| 23 | Calibration flags endpoint **saturation** explicitly | `…::CalibrationSummary.saturation_flag` | `…::test_saturation_is_flagged_explicitly` | **PROVEN** |
| 24 | Exact paired-binary test / interval / power / required-N are **tested functions** | `qwen_train/f2_statistics.py` | `test_f2_statistics.py` (22) | **PROVEN** |
| 25 | Stage-B refuses evidence with no `task_id` (R6 provenance break) | `lesson_synthesis.py::FailureEvidence.from_dict` | existing synthesis suite | **PROVEN** |

### 1.1 Deliberate non-controls (disclosed, not fixed)

* **Model training-set contamination of gold patches.** Real and unclosable by
  any engineering control (arXiv 2512.10218; OpenAI's 2026 SWE-bench Verified
  audit found frontier models reproduce gold patches verbatim). Disclosed as a
  limitation; addressed only by task selection. **NOT ESTABLISHED / OPEN.**
* **Network egress from the learning actor's *agent* shell.** Row 8 removes
  *credential* leakage from the subprocess env, and rows 6 + the existing MCP
  denylist remove *tool-mediated* network access, but a shell that can run
  arbitrary code still has the host's network unless the operator isolates it.
  See §5. **GOVERNANCE GAP (partially closed).**
* **`web_search` outside a governed run.** Unchanged and intentional.

---

## 2. Frozen statistical analysis plan

**This section is a proposal. It governs nothing until authorized (§6, Q1–Q4).**

### 2.1 Estimand (unchanged from F0)

> The **paired risk difference** in the probability of a qualifying
> first-relevant-edit within `k = 12` ATIF decision steps, strictly after
> `delivery_timestamp`, on the task's frozen per-task `relevant_file_set`,
> between arm **T** (frozen artifact containing genuine lesson L) and arm **X**
> (the identical frozen artifact with L removed), over the F2 task population.

Estimator: `RD = (b − c) / m`, where `b` = pairs where T edits and X does not,
`c` = pairs where X edits and T does not, `m` = complete pairs.

### 2.2 Primary test

**Exact conditional McNemar** (binomial on the discordant counts `b + c`),
**two-sided**, `α = 0.05`.

Justification, each point traceable:

* Paired binary data ⇒ the independent two-sample proportion formula is wrong
  twice over; on shared-item evaluations the paired required-N is a median
  **2.15× smaller** (arXiv 2605.30315, which also derives the Connor 1987
  paired required-N and calibrates five McNemar variants).
* Exact rather than χ²/mid-p because the plausible confirmatory N is small
  enough that the asymptotic approximation is unjustified, and the exact variant
  is ~3pp **conservative** — the safe direction for planning.
* **Two-sided**, because a verbal lesson can *hurt*: VRL-Bench (arXiv 2609.12404)
  finds verbal-memory updates that reduce success, and "Honest Lying" (2026)
  documents memory-harmful environments. A benefit-only test would make harm
  unreportable. **Harm is reported, never hidden.**
* **No bootstrap.** evalstats (arXiv 2609.35815) finds no bootstrap variant
  reaches nominal coverage on paired binary data **even at N = 100**, and
  advises against bootstrap CIs below N = 100. F0 §11's illustrative
  "bootstrap CI for risk difference" is therefore **not** implemented.

### 2.3 Interval

Clopper–Pearson exact conditional interval for the discordant proportion,
mapped to the risk difference. Where there are **no** discordant pairs the
interval deliberately does **not** collapse to `[0, 0]`; it uses the
conservative worst-case paired half-width. (A defect found by the test suite
during this pass and fixed in the implementation, not the test.)

### 2.4 Derived sample size — **not** an invented number

`qwen_train/f2_statistics.required_pairs(delta, discordance, alpha, power,
sided)`. Exact conditional McNemar, two-sided, `α = 0.05`, target power `0.80`,
`m` = complete task pairs (each pair = one T rollout + one X rollout):

| δ \ π_d | 0.20 | 0.30 | 0.50 | 0.70 |
|---|---|---|---|---|
| 0.10 | 151 | 222 | 402 | 551 |
| 0.15 | 61 | 101 | 181 | 241 |
| **0.20** | 28 | 51 | **85** | 141 |
| 0.25 | n/a | 20 | 61 | 90 |
| 0.30 | n/a | 19 | 34 | 57 |

One-sided comparison at δ = 0.20: `π_d = 0.30 → 42`, `π_d = 0.50 → 74`.

Two facts that govern everything downstream:

1. **`δ` and `π_d` are both empirical.** Neither may be fixed by assertion.
   `π_d` must come from the no-lesson calibration (§3). A default would
   fabricate the sample size.
2. **The dispositive constraint.** With a 4-task population the best achievable
   exact one-sided p-value is **0.06250** (`b = 4, c = 0`), which does **not**
   reach 0.05; `b = 5, c = 0` gives 0.03125. This is machine-checked in
   `test_f2_statistics.py::test_four_perfect_pairs_are_NOT_significant`.
   **A 4–5 task population cannot produce a significant confirmatory result at
   any effect size.** Not "underpowered" — arithmetically incapable.

### 2.5 Multiplicity, exclusions, stopping

* One confirmatory hypothesis ⇒ no correction for the primary. Secondary
  outcomes are **descriptive with intervals, no p-values**; a second inferential
  family would need a pre-registered Holm count (Romano–Wolf step-down is only
  stable at n ≥ 30 per comparison and is not recommended below that).
* Exclusions follow F0 §12, with one **proposed strengthening**: an invalid arm
  invalidates the **whole pair**, because McNemar requires complete pairs. F0 §12
  is arm-level; leaving it arm-level would make the analysis undefined for
  partially-invalid blocks. **Requires authorization (§6 Q5).**
* **Fixed N.** No interim analysis, no futility or success stopping, no
  peeking-based sample-size change. Randomised-response style sequential designs
  are not adopted: they buy little at these effect sizes and cost a great deal of
  interpretive complexity.
* **Infrastructure-failure budget:** proposed ≤ 30 % invalid pairs, with a defined
  remedy (re-run for infrastructure causes only, never for outcome) and a
  **stop-and-diagnose** rule if exceeded. F1 lost **10 of 20** observations
  (50 %) to infrastructure, so this is not hypothetical. **Requires
  authorization (§6 Q6).**
* **Calibration is not confirmatory** and must never be reported as such.

### 2.6 The lesson-utilization ladder (F0 requires the claim; measurement lags)

| Level | Measurable today? | Evidence |
|---|---|---|
| L1 lesson exists | **yes** | `promotion_proof` + verified HMAC receipt |
| L2 retrieved | **yes** | `ordered_selected_lesson_ids` / `_hashes` |
| L3 delivered | **yes** | `lesson_block_hash`, `final_prompt_hash`, `delivery_timestamp`, cross-checked against P2's `delivery_evidence` on `manifest_content_address` |
| L4 behaviourally utilized | **NO** | no pre-registered content-specific behavioural predicate exists |
| L5 expected behaviour observed | **yes** | the paired risk difference (primary) |
| L6 task outcome improved | secondary only | explicitly not evidence about L |

**This is a real limit and must be stated in any report.** F2 can establish L5
(Δ ≠ 0) but **cannot attribute Δ to L4**. If Δ = 0, "the lesson was delivered and
ignored" remains an admissible explanation. Closing L4 requires a
pre-registered, content-independent behavioural predicate — and adding one
risks the wording-exploitation failure mode "Honest Lying" describes, so it is a
scientific decision, not an implementation detail.

---

## 3. Calibration (R13) — the next scientific measurement

**Purpose.** Produce the two empirical inputs the confirmatory plan needs:

1. the **X-arm base rate at k = 12** — F1 reached the endpoint in **10/10** valid
   no-lesson observations (all at ATIF step 4). If X is similarly saturated the
   binary endpoint has **zero headroom** and no positive risk difference is
   attainable at any N. This must be *measured*, not assumed;
2. the **empirical discordance `π_d`** that sets required N (a ~3× swing across
   its plausible range).

**Contract** (enforced in `qwen_train/f2_calibration.py`): no lesson · no ACTIVE
transition · no promotion · no receipt key · fresh process · fresh workspace ·
same execution membrane · same endpoint detector · same task population ·
identical infrastructure rules. Reruns only for one of eight predefined
infrastructure causes. Records are frozen and content-hashed. Records measured
by different endpoint specifications are refused as incomparable.

**Status: the schema, guard rails and analysis are implemented and tested
(27 tests). The arm runner is deliberately absent.**

**Why the runner is absent.** Executing it creates Experiment J observations.
`docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md` explicitly excludes
"Experiment J observations (T/X/C0)", and the pilot precedent (F1-OP-001 … F1-OP-004)
shows a no-lesson run requires its own explicit operator authorization naming the
run count, task, horizon and censoring convention. Running it also requires the
operator's model and backend services, which an agent must not start or stop.

**Required authorization (Q10):** no-lesson calibration run count, task set,
replicates per task, and the censoring convention — the direct analogue of
F1-OP-001/002/004.

---

## 4. Population (R1) — the binding constraint

**Machine-screening the actual 14-row `swe_pool.jsonl` pool** (14 distinct
repositories) against the rules in `f2_population.py`:

| State | Value |
|---|---|
| Pool rows / distinct repositories | 14 / 14 |
| Tasks with a frozen `relevant_file_set` (R8) | **0** — none designated |
| Admitted today | **0** |
| Admitted once endpoints + evidence digests are supplied | **13 of 14** |
| Rejected on data | 1 — `pyqtgraph__pyqtgraph-1845` declares an **empty** `pass_to_pass` set, so R3 has nothing to check for regression |

Two independent reasons the current population cannot be confirmatory:

1. **R8 is unmet for every task.** No designated task carries a frozen
   `relevant_file_set`, so the primary endpoint is uncomputable for all of them.
   `screen_entry` now *rejects* such a task rather than letting it silently
   measure "no qualifying edit".
2. **Arithmetic.** Even fully remediated, 13–14 pairs against a requirement of
   **19–85** (δ = 0.20…0.30, π_d = 0.30…0.50, two-sided) is short by roughly an
   order of magnitude, and at ≤ 5 pairs it is *arithmetically impossible* to
   reach significance at any effect size (§2.4).

**Expansion is therefore required, and it is a research/supply task, not a
code task.** `screen_pool_rows` accepts an externally supplied
`relevant_file_sets` map and `evidence_digests` map, so an enlarged,
contamination-screened pool can be admitted without touching the screening
logic. SWE-bench-Live is the purpose-built candidate: continuously refreshed,
LLM-verified, and its own protocol forbids delivering `hint`/`FAIL_TO_PASS`/
`test_patch` to the agent and forbids task-instance-specific solution content in
prompts — which is also the correct standard for J's *treatment artifact*.

**External dataset acquisition is not performed here** (network/licensing) and
is **Q1**.

---

## 4b. Q8 delivered — governed endpoint derivation

**Authorization:** operator, 2026-10-04 — *reference modified-file set, adopted as an
oracle-style proxy for task edit scope*. Implementation authorized for Q8 only.

**Module:** `qwen_train/f2_endpoint_derivation.py` · **Tests:** `tests/test_f2_endpoint_derivation.py` (29)
**Frozen manifest:** `qwen_train/curriculum/f2_endpoints.json` (tracked, alongside `swe_pool.jsonl`)
**Reproduce:** `python -m qwen_train.f2_endpoint_derivation --out qwen_train/curriculum/f2_endpoints.json`
**Deterministic anchor:** each entry's `relevant_file_set_hash`. (The whole-file
SHA-256 also covers a `derived_at` timestamp, so it identifies *this frozen
artifact* rather than being byte-stable across re-derivations; equality of the
per-entry hashes is what proves a re-derivation reproduced the same sets.)

### Method

`base_commit` is the parent of the reference fix commit, so the reference commit
is a **child** of it. That is not unique — on `pypa__twine-1066` there are three
children and two touch exactly one non-test file each, so file counts alone
cannot disambiguate. The unique, governed discriminator is the **declared
reference test patch** (an already-authorized artifact):

> the reference fix commit is the **unique** child of `base_commit` whose
> *test-file* diff set exactly equals the declared reference test patch's file
> set; `relevant_file_set` is that commit's **non-test** diff paths.

The **authoritative discriminator is the test-patch match alone**. The pool row's
`num_modified_files` is a recorded **diagnostic** only (`count_agreement` ∈
`matches_source_count` / `matches_total_count` / `mismatch` / `not_declared`) and
**never causes rejection** of an otherwise uniquely identified reference commit —
the field carries inconsistent semantics across rows (source-only for
`pypa__twine-1066`, all-files for `xknx__xknx-470`), so count gating was removed
after it produced demonstrably false rejections.

### Operator controls — all satisfied

| Control | Status |
|---|---|
| 1. Derived before any T/X/C0 trajectory | **PROVEN** — no arm has ever run |
| 2. Derived outside every arm workspace | **PROVEN** — reads the clone in place; writes only to `data/experiment_j/` |
| 3. Gold patch/reference solution never exposed to the worker | **PROVEN** — nothing here is delivered to a worker |
| 4. Store task id, base commit, file set, method, timestamp, canonical hash, provenance identity | **PROVEN** — and reference identity is a **digest only**, never a resolvable pointer |
| 5. Freeze before the first confirmatory trajectory | **PROVEN** — manifest written; no trajectory exists |
| 6. Never altered after observing outcomes | **PROVEN** — no outcome has been observed |
| 7. Do not call it semantic ground truth | **PROVEN** — `endpoint_semantics` field carries the caveat verbatim |
| 8. Report as oracle-style proxy for edit scope | **PROVEN** — in module docstring, manifest, and this document |
| 9. Sensitivity analysis may later compare against independent localization | **NOT YET DONE** — planned |

### Additional controls implemented (beyond the operator list)

* **Read-only enforcement — single git entry point.** Every `git` invocation
  passes through `_git`, which checks an allowlist of read-only subcommands
  (`rev-parse`, `rev-list`, `diff`, `log`, `for-each-ref`, `cat-file`, `show`).
  `reset`, `checkout`, `gc`, `update-ref`, `fetch`, `merge`, `rebase` are refused
  at the call site. **Corrected in this revision:** `_clone_fingerprint` previously
  called `subprocess.run` directly, bypassing the allowlist; it now routes through
  `_git`, so the claim is true for *every* git invocation. **PROVEN** by test
  (`TestReadOnlyGitEntryPoint`), which fails if fingerprinting does not call `_git`.
* **Clone non-mutation proof.** Each derivation fingerprints the clone
  (`HEAD` + all refs + commit count) before and after; a change raises. All 12
  successful derivations recorded `clone_unmutated: true`.
* **No gold content read.** Only `--name-only` and `--numstat` are used: paths
  and line counts, never a hunk or file body.

### Result — 12 derived, 2 fail closed

Revision `48d17a65` reported 8 derived / 6 failed. The four "no child" rows were
recovered by controlled fetch (see below); **no other row changed**.

| Derived (12) | `relevant_file_set` |
|---|---|
| `pypa__twine-1066` | `twine/package.py` |
| `aws-cloudformation__cfn-lint-3805` | `src/cfnlint/rules/resources/properties/StringLength.py` |
| `meltano__sdk-1881` | `singer_sdk/helpers/capabilities.py`, `singer_sdk/target_base.py` |
| `xknx__xknx-470` | `changelog.md`, `docs/sensor.md`, `…/xknx/factory.py`, `…/xknx/schema.py`, `xknx/devices/sensor.py` |
| `pybamm-team__pybamm-4267` | `pybamm/util.py` |
| `enthought__envisage-275` | `envisage/extension_registry.py`, `envisage/safeweakref.py`, `setup.cfg` |
| `qiskit__qiskit-terra-5662` | 7 `.py` + `releasenotes/notes/*.yaml` |
| `pyqtgraph__pyqtgraph-1845` | `pyqtgraph/colormap.py` |
| **`sinaptik-ai__pandas-ai-1099`** | `pandasai/pipelines/chat/code_execution.py`, `pandasai/responses/response_serializer.py` |
| **`databricks__dbt-databricks-935`** | `CHANGELOG.md`, `dbt/adapters/databricks/impl.py`, `dbt/include/databricks/macros/relations/liquid_clustering.sql`, `…/optimize.sql` |
| **`pallets__werkzeug-2583`** | `CHANGES.rst`, `src/werkzeug/routing/converters.py`, `src/werkzeug/routing/rules.py` |
| **`qiskit__qiskit-ibm-runtime-367`** | `qiskit_ibm_runtime/api/clients/runtime.py`, `qiskit_ibm_runtime/api/rest/runtime.py`, `qiskit_ibm_runtime/qiskit_runtime_service.py`, `releasenotes/notes/creation-date-filters-*.yaml` |

| Fail-closed (2) | Reason |
|---|---|
| `pytest-dev__pyfakefs-916` | no child matches the declared test patch — **honest failure, not rescued** |
| `pallets__click-2380` | **AMBIGUOUS** — 2 children match the declared test patch — **honest failure, not rescued** |

`pallets__werkzeug-2583` derived with `count_agreement = mismatch`, which is a
live demonstration that the diagnostic no longer gates: under the removed count
gate this row would have been rejected despite a uniquely identified reference
commit.

### Controlled network recovery (4 rows)

The four "no child" clones were **truncated at base** — zero refs, no remote
configured, and no future history at all (distinct from the normally-cloned rows,
which carry remotes and hundreds of refs). Recovery was the minimum network
operation: add the remote and fetch the default branch, no tags.

| Row | default branch | fetch rc | refs | commits | children of base | worktree unchanged |
|---|---|---|---|---|---|---|
| `sinaptik-ai__pandas-ai-1099` | main | 0 | 0→2 | 909→1416 | 1 | **yes** |
| `databricks__dbt-databricks-935` | main | 0 | 0→2 | 1149→1695 | 1 | **yes** |
| `pallets__werkzeug-2583` | main | 0 | 2→2 | 6094→6094 | 1 | **yes** |
| `qiskit__qiskit-ibm-runtime-367` | main | 0 | 0→2 | 884→2634 | 1 | **yes** |

No checkout, reset, rebase, merge, or working-tree modification. Pre/post
`git status --porcelain` was recorded for each and is byte-identical. The fetched
repositories remain **curator-side**: the arm-workspace contamination channel
(future history) is still closed at arm time by `_strip_future_history`.

### Two construct-validity observations for the operator

**(a) Some derived sets include non-code files.** `xknx-470` includes
`changelog.md` and `docs/sensor.md`; `envisage-275` includes `setup.cfg`;
`qiskit-terra-5662` includes a release note; the recovered `databricks-935`
includes `CHANGELOG.md` and two `.sql` macros, and `werkzeug-2583` includes
`CHANGES.rst`. This is *faithful* to the authorized definition (the reference
commit's non-test files) but it means the primary endpoint could be satisfied by
editing a changelog. **Retained exactly as derived — no extension filter.** A
code-only filter would be a **methodology change beyond the Q8 authorization** —
`REQUIRES AUTHORIZATION`.

**(b) RESOLVED in this revision.** The four clone-completeness failures were
recovered under the operator's controlled-fetch authorization.

### Effect on admissibility

`S6_relevant_file_set` now **passes for 12 of 14** tasks (was 0, then 8). Admitted
count remains **0** because `S8_evidence_provenance` fails for all 14 — base/gold
evidence digests require actual base and gold test runs, which is a separate
authorization. **Q8 is delivered; R8/S8 evidence provenance is the next gate.**

---

## 4c. S8 evidence provenance contract

**Module:** `qwen_train/f2_evidence.py` · **Tests:** `tests/test_f2_evidence.py`
**Population integration:** `qwen_train/f2_population.py` (`S8_evidence_provenance`)

### The defect this replaces

S8 previously accepted a task when two strings were non-empty:

```python
bool(base_evidence_digest) and bool(gold_evidence_digest)
```

A digest establishes **integrity only**. It does not establish that the artifact
was produced for *this* task, at *this* base commit, by an *authorized* evaluator,
nor that the pair demonstrates what R5 requires (*"Base and gold runs archived
outside the agent workspace with SHA-256. A substitute probe does not satisfy
this."*). The contract now separates three questions that were previously
collapsed:

| Question | Meaning |
|---|---|
| **Integrity** | Does the record hash to its own digest, and do the referenced artifacts match theirs? |
| **Provenance** | Was it produced for the declared task/repo/base by an authorized evaluator at a declared version, from a declared execution? |
| **Scientific validity** | Does the base/gold pair establish the required relationship (same task; base does not pass; gold does)? |

### Schema (`f2_evidence_record_v1`)

`schema_version` · `instance_id` · `repository` · `base_commit` ·
`execution_state_identity` (`base`|`gold`) · `execution_state_digest` (opaque) ·
`test_command` · `environment_identity` · `evaluator_identity` ·
`evaluator_version` · `execution_started_at` · `execution_finished_at` ·
`execution_result` (`pass`|`fail`|`error`) · `failure_class`
(``|`execution`|`environment`|`evaluator`) · `test_output_artifact` ·
`run_log_artifact` · `evidence_record_digest`.

Each artifact is an `ArtifactRef` (`name`, `digest`, `size_bytes`) — identity and
metadata, never payload. `execution_state_digest` is an **opaque** digest of the
execution state: it commits to which state ran without exposing a gold commit SHA
or any oracle content.

### Canonicalization

Digest = SHA-256 over `json.dumps(payload, sort_keys=True, separators=(",",":"),
ensure_ascii=False)` — the repository's existing convention — **excluding**
`evidence_record_digest` itself, so the digest is never self-referential.
Verified by test: key order does not affect the digest; an unchanged record
reproduces the same digest; the digest is unchanged by rewriting its own field.

### Distinct, non-collapsible outcomes

`VERIFIED` · `MISSING` · `MALFORMED` · `UNKNOWN_SCHEMA` · `DIGEST_MISMATCH` ·
`IDENTITY_MISMATCH` · `EXECUTION_FAILURE` · `ENVIRONMENT_FAILURE` ·
`EVALUATOR_FAILURE` · `UNAUTHORIZED_PROCEDURE` · `ARTIFACT_MISSING` ·
`ARTIFACT_DIGEST_MISMATCH` · `SCIENTIFICALLY_INSUFFICIENT`.

`EvidenceVerification` carries three independent booleans (`integrity_ok`,
`provenance_ok`, `scientifically_sufficient`) plus the state, so a failure
preserves *why*.

### R5 bridge

`r5_from_evidence(verification) -> bool | None`: `VERIFIED -> True`,
`MISSING -> None` (NOT ESTABLISHED, fail-closed default), any other failure
`-> False`. This is the only sanctioned path to `R5=True`; a caller holding only
digests cannot reach `True`. The existing R1–R8 tri-state behaviour is preserved.

### Worker-facing safety

The population manifest entry carries only compact evidence **identities** (the
64-hex record digests) plus the verdicts. Verified by test: no
`execution_state_digest`, no artifact names, no evidence payload, and the only
40-hex token present is the task's `base_commit`.

### Status

**S8 mechanism implemented and hardened.** This is *not* a claim that S8 evidence
exists. Actual evidence has **not** been generated: it requires running the base
and gold evaluations for each task, which is a separate authorization. Population
admission therefore remains **blocked** until authorized evidence is produced and
independently verified. `S6` passes for 12/14; `S8` passes for **0/14**.

### Hardening (research-grade trust model)

| # | Change | Why |
|---|---|---|
| H1 | Evaluator authorization **fails closed**. Absence is `PROVENANCE_NOT_ESTABLISHED`, denial is `UNAUTHORIZED_PROCEDURE`. An empty authorized-version set is rejected. | Previously `authorized_evaluators=None` silently disabled the check, so *any* evaluator was treated as authorized. |
| H2 | The S8 verdict (`evidence_verified`, `evidence_state`, `evidence_scientifically_sufficient`) is **covered by the population identity hash**. | An admission-relevant value outside the integrity anchor makes the anchor incomplete. |
| H3 | `EvidenceVerification` carries a module-private proof token; **direct construction raises `TypeError`**. | The Python type layer is not a trust boundary. Only the verifier can produce a verification. |
| H4 | `load_evidence_record` returns `LOAD_PARSED`/`LOAD_MISSING`/`LOAD_MALFORMED` — never a verification state. | A parsed JSON object is untrusted; it must not read as "verified". |
| H5 | `ArtifactRef` binds `role`; the role must match the slot it occupies. | A test-output artifact can no longer be substituted for a run-log. |
| H6 | Artifact names are **resolved and contained under the trusted root**; absolute paths, `..`, and symlink/junction escapes are rejected (`ARTIFACT_ESCAPES_ROOT`). Digest and size come from **one open handle** (TOCTOU-safe). | `root / name` previously allowed escape via `..` or an absolute path. |
| H18 | A **trusted artifact root is required**; without one the result is `PROVENANCE_NOT_ESTABLISHED`. | Artifact provenance cannot be established from a bare digest. |
| H2/H19 | Timestamps are **excluded from the cryptographic identity** but retained on the record for audit. | A clock is not a trust anchor; formatting/skew must not change what the evidence scientifically is. |
| H12 | `PROVENANCE_NOT_ESTABLISHED` added, distinct from `UNAUTHORIZED_PROCEDURE` and `EVALUATOR_FAILURE`. | "We hold no authority" and "this procedure is denied" are different findings. |
| H15 | Schema versioning fails closed on missing, unknown, or future versions. | No silent interpretation under a different schema. |

### Trust model — what S8 proves and does not prove

**What S8 proves (given a trusted artifact root and an evaluator allowlist):**
that a record is internally consistent; that its artifacts exist under the trusted
root and hash to the recorded digests; that the declared evaluator and version are
authorized; that base and gold describe the same task, repository and base commit
but *different* execution states; that the base state does not pass and the gold
state does; and that the whole base/gold relationship is bound by a deterministic
digest.

**What S8 does NOT prove:** that the evaluation actually ran; that the declared
test command was executed; that the environment matched its declaration; who
physically produced the bytes; that the artifacts were not produced *after* the
fact by an actor with write access to the store; or that the record was not
crafted by someone who could compute a correct digest over fabricated content.
These are properties of the **controlled execution and curation layer**, not of a
hash. The mechanism is deliberately explicit about this boundary rather than
implying more than it establishes.

**What crosses into the worker-facing manifest:** only the two 64-hex record
digests and the three verdict fields, plus the task's own `base_commit`. The
execution-state digest, artifact names, artifact payloads, and any gold patch,
diff, hunk, or reference commit id are withheld. Verified by test, including an
adversarial case whose gold artifacts *are* the patch text.

### Outstanding governance (not implementation)

* **Who is authorized to produce S8 evidence**, and the concrete
  evaluator identity/version, is not yet named in authority.
* **Evaluator implementation identity** (`evaluator_implementation_digest`) is not
  governed; a name+version can be reused for a different implementation. The
  mechanism does not fake this — it is a `GOVERNANCE GAP`.
* **Trusted artifact store / retention**: where S8 artifacts must live, whether
  that location is immutable, retention duration, deletion detectability, and
  whether a later researcher can retrieve the exact bytes are all
  `GOVERNANCE GAP — ARTIFACT RETENTION AUTHORITY NOT ESTABLISHED`.
* **Curator authority and independent-verifier authority** are not yet defined.
* **Execution identity**: there is no immutable run registry, so a run is
  identified by its content, not by a registry-issued id. `GOVERNANCE GAP`.

---

## 4d. F2 evidence governance layer (closes the S8 governance gaps)

**Module:** `qwen_train/f2_governance.py` · **Tests:** `tests/test_f2_governance.py` (49)
**S8 remains frozen** — `qwen_train/f2_evidence.py` is byte-identical
(`F095D051…`); this layer composes the S8 verifier and emits results through the
existing S8 private-proof mechanism.

The S8 mechanism answers *"is this record internally consistent and do the
declared results satisfy the base/gold property?"* It deliberately trusts the
**declared** `execution_result` and a **caller-supplied** evaluator allowlist.
This layer adds the governance that makes those trustworthy.

| Layer | What it establishes |
|---|---|
| **1 Execution producer** | An immutable `ExecutionBundle` + raw artifacts. A declared result is an **input to verification, never a substitute for it**. |
| **2 Trusted artifact store** | One explicit namespace the layer will verify: canonical resolution, containment (`..`, absolute, drive, UNC, symlink escape rejected), role binding, digest+size verification, explicit retention metadata. |
| **3 Authorized evaluator registry** | An evaluator is authorized only when **id + version + implementation digest + procedure id + protocol version** all match. An empty registry authorizes nothing. |
| **4 Immutable execution identity** | Canonical digest over instance/repo/base/state/evaluator identity+version+implementation digest/procedure/protocol/environment/command/artifact identities. **Timestamps are audit metadata, not identity.** |
| **5 Independent verifier** | Loads the bundle, checks schema, authorizes the evaluator, verifies artifacts, **derives the result from retained artifacts**, rejects any declaration that disagrees, and emits the proof through S8. |

**JSON saying `"execution_result": "pass"` is never sufficient.** The verifier
derives the result from the retained test-output artifact via a *registered*
result-derivation protocol (`json_test_report_v1` is a reference implementation).
If the artifact cannot establish the result, or the bundle names an unregistered
protocol, verification fails with `SCIENTIFICALLY_INSUFFICIENT` — the declaration
is never upgraded.

### Trust boundary — the three questions stay separate

* **INTEGRITY** — the bytes match their cryptographic identities.
* **PROVENANCE** — the bytes belong to an *authorized* procedure and the *trusted*
  artifact namespace, for a specific immutable execution identity.
* **SCIENTIFIC VALIDITY** — the retained evidence actually establishes the
  base/gold property.

A SHA-256 proves integrity only. This layer closes the provenance gap as far as
retained evidence permits and states plainly what it still cannot establish.

### Evidence classification

**PROVEN** (implementation + tests): evaluator authorization fails closed on
every component; the evaluator implementation digest **format** is validated;
execution identity is deterministic and mutation-sensitive while timestamps are
excluded; **the execution identity binds each artifact's role, name, digest AND
size**, so mutating artifact bytes without changing the name changes the identity;
trusted-root containment and artifact integrity; role binding; independent
derivation rejects a false declaration; forged JSON verification fields are
ignored; direct construction of a verification result is refused; schema fails
closed; gold material does not enter the identity; no network or execution in the
verification path.

**SUPPORTED** (architecture, depends on controlled deployment): that a real
executor emits conforming bundles; that a real artifact store is operated
immutably.

**NOT ESTABLISHED:** that any *real* execution occurred — no evidence exists;
`S8` passes 0/14. **Evaluator-byte provenance is NOT ESTABLISHED unless the
implementation artifact is supplied and independently hashed**: the registry
check proves only that a *declared* identity matches an authorized entry, not
that those bytes were the ones that ran. The bundle accepts an optional
`implementation_artifact`; when present it is verified in the trusted store and
its digest must equal the authorized implementation digest
(`evaluator_bytes_proven`). When absent, the claim is `False` — never assumed.
Filesystem immutability of the store is likewise `NOT ESTABLISHED` (requires
OS/operator-level controls). `RECONSTRUCTABLE_IDENTITY` holds;
`VERIFIABLE_ARTIFACTS` holds; `DERIVABLE_RESULT` holds only for a registered
protocol; full `REEXECUTION` is intentionally not performed.

**GOVERNANCE GAP:** the authorized evaluator identity and implementation digest;
the real result-derivation protocol and its format; the trusted store location and
OS-level immutability; retention duration and deletion detection; curator and
independent-verifier authority.

**REQUIRES AUTHORIZATION:** naming the authorized evaluator/procedure and
protocol, provisioning the trusted store, and generating any real evidence.

> A SHA-256-shaped `implementation_digest` string does **not** prove evaluator
> implementation provenance. That would be a false scientific claim; it is
> recorded as `NOT ESTABLISHED` above, not as `PROVEN`.

---

## 5. Clean room (R4) — what is now proven, and what is not

**Closed and proven.** The untrusted-subprocess environment builder previously
stripped `TOKEN`/`SECRET`/`PASSWORD`/`PASSWD` but **not** a bare `KEY`, so
`MY_KEY`, `SIGNING_KEY` and `SSH_KEY` reached the learning actor's shell
(D-12). `is_credential_env_name` now matches **whole `_`-separated segments**,
so `KEY`/`KEYS`/`CREDENTIAL`/`SECRET`/`TOKEN`/`PASSWORD`/`AUTH`/`PASSPHRASE`/
`SESSION`/`SIGNATURE` are caught anywhere in a name, while innocent names
containing those letters (`MONKEY_COUNT`, `KEYBOARD_LAYOUT`, `AUTHORS_FILE`) are
**not** over-stripped. `AUTH` moved from substring to segment matching for that
reason. `clean_learning_env` is allowlist-based, and an allowlist entry still
cannot smuggle a credential-named variable through. 35 adversarial tests,
including an unanticipated credential name.

**Still open.** MCP/tool-mediated network access is denied and credentials no
longer leak, but a shell that can execute arbitrary code retains the **host's
network** unless the operator isolates it. MCP denial alone therefore still does
not establish that a learning event was network-free. Closing this needs an
operator-level control (air-gapped run, egress firewall, or an offline-only
task environment) — it is not a code change I can make unilaterally.
**GOVERNANCE GAP.**

---

## 6. Authorization checklist — what the operator must decide

| # | Decision | Recommendation | Consequence if deferred |
|---|---|---|---|
| **Q1** | Population strategy | Import an enlarged, contamination-screened pool (SWE-bench-Live class). Alternative: keep 13–14 and reframe exploratory. | No confirmatory result is possible at 14 pairs. |
| **Q2** | Test sidedness | **Two-sided.** Harm must be reportable. | A harmful lesson becomes unreportable. |
| **Q3** | δ (smallest effect of interest) | **δ = 0.20**, pre-registered, conditional on Q1. | No frozen N is possible. |
| **Q4** | Primary endpoint: keep F0's binary, or move to censored steps-to-first-edit? | **Keep F0's binary** and report the survival analysis as a pre-specified secondary — F0 §16 protects the primary, and saturation is a *hypothesis the calibration tests*, not an established fact. | Either is defensible; this is a scientific judgment. |
| **Q5** | Ratify **block-level** exclusion (invalid arm ⇒ invalid pair) | Ratify. McNemar requires complete pairs. | Analysis undefined for partially-invalid blocks. |
| **Q6** | Infrastructure-failure budget (proposed ≤ 30 %) | Authorize, with stop-and-diagnose on breach. | At F1's 50 % loss rate the design cannot hold together. |
| **Q7** | Distiller model identity + weights digest | Fix both now; `SWARM_DISTILLER_MODEL` and `SWARM_DISTILLER_WEIGHTS_DIGEST` (or explicit unavailability). | Synthesis fails closed; no lesson can be produced. |
| **Q8** | Per-task `relevant_file_set` (R8) | **DELIVERED** — see §4b. Governed reference-modified-file derivation, hash-frozen before any trajectory. | 8/14 derived; 6 fail closed; S8 evidence digests still outstanding |
| **Q9** | Shell network-egress isolation for the learning event | Authorize an operator-level control. | "Clean-room" remains an assumption. |
| **Q10** | **No-lesson X/C0 calibration run** (run count, tasks, replicates, censoring convention) | Authorize — cheapest, highest-value measurement available. | δ and π_d stay unmeasured; N cannot be frozen. |
| **Q11** | `SWARM_RECEIPT_KEY` provisioning | Only **after** Q7 and Q8. | Promotion stays fail-closed; no ACTIVE lesson. |
| **Q12** | The learning event itself (exactly one, clean-room) | After Q9–Q11. | No genuine L; F2 cannot freeze. |
| **Q13** | Confirmatory T/X collection and N=2 | Last. | — |
| **Q14** | Update `LEARNING_EXPERIMENT_STATE.md` (D-12 now partially closed) | Authorize a minimal status update. | The state doc will understate current controls. AGENTS.md §1 protects it. |

---

## 7. Evidence labels used in this document

`PROVEN` — verified in this revision with a cited implementation and test.
`PROVEN IN CURRENT REVISION` — re-verified during this pass.
`SUPPORTED` — strong indirect evidence.
`INFERRED` — reasoned, not verified.
`NOT ESTABLISHED` — evidence absent or insufficient.
`GOVERNANCE GAP` — a rule is needed where none exists.
`REQUIRES AUTHORIZATION` — the action needs authority not held here.

---

## 8. What is NOT authorized by this document

This document authorizes **nothing**. It does not create an ACTIVE lesson,
provision a receipt key, run a calibration arm or a confirmatory arm, select a
population/`n`/`δ`/test, or modify F0 or any frozen document. It records what the
code enforces and what the operator still has to decide.

*Engineering frozen at `1dd07120`. Scientific authorization NOT GRANTED.*