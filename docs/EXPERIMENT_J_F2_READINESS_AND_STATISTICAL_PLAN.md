# Experiment J — F2 Readiness and Frozen Statistical Analysis Plan

**Status: ENGINEERING MACHINERY IMPLEMENTED — METHOD AND SCIENTIFIC AUTHORIZATION
NOT GRANTED.** Corrected 2026-10-05: this line previously read "ENGINEERING + METHOD
READY", which overstated readiness while Q5/Q6 remain unauthorized (§6) and while
this document's own §2.4 states its statistical section governs nothing until
authorized. **A green software suite is not experimental readiness.**
**Operator handoff (what to provide, and in what order):**
`docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md` — the single authoritative answer to
"may we run yet?". This document explains *how the gates work*; the handoff owns the
execution order and the readiness checklist.
**Machine-readable status:** `python -m qwen_train.f2_preflight` (`f2_preflight_v1`).
**Date:** 2026-10-04 (reconciled 2026-10-05 against `3218ffbc`)
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
  See §5. **GOVERNANCE GAP — enforcement only;** observation, independent
  verification, attestation and the readiness gate are IMPLEMENTED
  (`F2-IMPL-AUTH-022/023`).
* **`web_search` outside a governed run.** Unchanged and intentional.

---

## 2. Frozen statistical analysis plan

**This section is a proposal. It governs nothing until authorized (§6, Q1–Q4).**

### 2.1 Estimand (unchanged from F0)

> The **paired risk difference** in the probability of a qualifying
> first-relevant-edit within the first `k` ATIF decision steps, strictly after
> (F0 §5: `k` is selected during F1 from the no-lesson pilot by the
> pre-registered 95th-percentile rule and capped within `[8, 12]`; it is NOT
> fixed at 12 by F0, and is not frozen yet.)
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

> **CORRECTION (2026-10-08, `F2-CLARIFICATION-005`) — what the helper's return
> value actually is.** `required_pairs` does **not** return "the number of pairs
> needed". It returns the **first** `m` at which power clears the target when
> power is evaluated at the *rounded* cell `n = round(m·π_d)`,
> `b = round((n + m·δ)/2)`, `c = n − b` — a **conditional** power (the discordant
> count is held fixed, not integrated over `Binomial(m, π_d)`) on a
> **non-monotone** curve, because `n` and `b` are rounded and the exact two-sided
> critical value is a step function of `n`. At δ = 0.20, π_d = 0.50, α = 0.05,
> power = 0.90 the first crossing is **116** (power 0.900969), but power at
> `m = 118` is **0.839027**, at `m = 122` **0.895089**, and at `m = 130`
> **0.930641** — all three values are produced by the repository's own
> `exact_power` and are pinned by
> `tests/test_f2_statistics.py::TestRequiredPairsInterpretation`.
>
> **Consequence for `n`: none.** The frozen rule is
> `n = max(300, required_pairs(...))`, and `required_pairs` returns **below 300
> at every point of the authorized π_d range** (28 / 66 / **116** / 191 / 261 for
> π_d = 0.20 / 0.30 / 0.50 / 0.75 / 1.00), so `n = 300` regardless of π_d. The
> invariant `max(300, required) == 300` is pinned by
> `test_every_authorized_sensitivity_point_stays_below_the_frozen_minimum`.
> **116 must never be quoted as an unconditional total-pair requirement.**
> The exact design calculations behind the choice of 300 (conditional 130,
> unconditional 135, worst-case π_d = 1.00 requires 261) are recorded in
> `F2-CLARIFICATION-005`.

Two facts that govern everything downstream:

1. **`δ` and `π_d` are both empirical.** Neither may be fixed by assertion.

   > **CORRECTION (2026-10-05) — THIS CLAIM IS CONTRADICTED; THE DESIGN IS BLOCKED.**
   > The sentence that followed asserted that `π_d` "must come from the no-lesson
   > calibration (§3)". That is mathematically impossible. For a task-PAIRED design,
   > `π_d = P(T xor X) = p_T + p_X − 2·P(T=1, X=1)`, which requires BOTH arms and
   > the joint association. `p_X` alone leaves `π_d` anywhere in `[0, 1]`, and the
   > no-lesson calibration has no T arm (`CALIBRATION_ARMS = ("X", "C0")`). This also
   > conflicts with the frozen sequence: F0 §10 has the no-lesson pilot inform `k`/`n`,
   > then F1 freezes `k`, `n` and the practical-effect criterion, and the learning event
   > comes AFTER F1 — so no T observation can exist when `n` is frozen. An earlier
   > proposed `p_X`-derived bound on `π_d` was INVALID (it assumed T/X independence)
   > and is withdrawn. **Resolution requires scientific authorization; no replacement
   > estimator is asserted here.** The pilot legitimately yields `p_X` (headroom),
   > steps-to-first-edit (which informs `k` per F0 §5, capped [8, 12]), task
   > heterogeneity, and censoring / invalid / infrastructure rates — NOT `π_d`.
   >
   > Also note: `horizon_steps = 12` in the implementation is the observation horizon
   > and the F0 cap; it is NOT the frozen primary-endpoint `k`.

   (Superseded text, retained for provenance:) `π_d` must come from the no-lesson
   calibration (§3). A default would fabricate the sample size.
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

1. the **X-arm base rate at the measurement horizon of 12 steps** — note that
   `12` here is the **observation horizon and the F0 §5 cap**, NOT a frozen primary
   endpoint `k`: F0 §5 selects `k` during F1 as the 95th percentile of
   steps-to-first-edit across pilot runs, capped within `[8, 12]`, and F1 freezes it.
   F1 reached the endpoint in **9 of the 10** valid
   no-lesson observations (all nine at ATIF step 4). The tenth valid
   observation — `f1_observation_5.jsonl` L2, invocation `32a0965d7b108e25` —
   made **no qualifying edit** inside the 12-step horizon; its trajectory
   contains `lsp diagnostics`, a `filesystem **read**`, a duplicate `lsp` call and
   an `mcp` call, and no `write`/`patch`/`edit`/`create`. Re-verified 2026-10-08
   by re-deriving every endpoint from the raw ATIF trajectories with the
   repository's own `pair_observation_ok` + `find_qualifying_first_edit`
   (`F1_FINAL_RECONCILIATION.md` §18). Corrected 2026-10-08: this previously
   read "10/10 … all at ATIF step 4", which the raw artifacts contradict.
   **Consequence for `k`: none** — a valid observation with no endpoint is
   right-censored at the horizon and contributes 12, so the P95 set is 9×4 +
   11×12 and `P95 = 12`, `k = 12` are unchanged.
   If X is similarly saturated the
   binary endpoint has **zero headroom** and no positive risk difference is
   attainable at any N. This must be *measured*, not assumed;
2. ~~the **empirical discordance `π_d`** that sets required N (a ~3× swing across
   its plausible range).~~ **CONTRADICTED — see §2.4 (correction 2026-10-05).** The
   no-lesson pilot does **NOT** empirically estimate `π_d`. `π_d = P(T xor X)` needs
   both arms and the joint association; the pilot has no T arm
   (`CALIBRATION_ARMS = ("X", "C0")`), and `p_X` alone leaves `π_d` anywhere in
   `[0, 1]`. What the pilot legitimately supplies is: `p_X` / endpoint-headroom
   information, steps-to-first-edit (which feeds the F0 §5 / F1 `k`-selection rule),
   task heterogeneity, and censoring / invalid / infrastructure rates. `π_d` remains
   **unidentified before T exists**, and the final `n` remains an **F1 scientific
   authorization decision**. No replacement estimator is asserted here.

**Contract** (enforced in `qwen_train/f2_calibration.py`): no lesson · no ACTIVE
transition · no promotion · no receipt key · fresh process · fresh workspace ·
same execution membrane · same endpoint detector · same task population ·
identical infrastructure rules. Reruns only for one of eight predefined
infrastructure causes. Records are frozen and content-hashed. Records measured
by different endpoint specifications are refused as incomparable.

**Status: the schema, guard rails and analysis are implemented and tested. The arm
runner is implemented** (`run_calibration`, `make_calibration_runner` in
`f2_calibration.py`, fail-closed on `CalibrationAuthorization`) **but was deliberately
not executed.** Corrected 2026-10-05: this previously read "The arm runner is
deliberately absent"; the absent thing was the *execution*, not the runner.

**Why nothing was executed.** Executing it creates Experiment J observations.
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
| Tasks with a frozen `relevant_file_set` (R8) | **12 derived, 2 refused** — updated 2026-10-05; see §4b. This row previously read "0 — none designated". |
| Admitted today | **0** (R8 is delivered for 12; the remaining eligibility gates are not) |
| Admitted once evidence digests are supplied | 12 of 14 (endpoints already delivered) |
| Refused (correct, fail-closed) | 2 — `pytest-dev__pyfakefs-916` (no child of the base has a test-file diff equal to the declared reference test patch) and `pallets__click-2380` (reference fix commit AMBIGUOUS: 2 children match). Both **refused rather than guessed**. Corrected 2026-10-05: this row previously named `pyqtgraph__pyqtgraph-1845`. |

Two independent reasons the current population cannot be confirmatory:

1. **S8 evidence provenance is unmet for every task.** `S8` passes **0/14** — no
   verified base/gold execution pair exists, and base/gold evidence digests require
   actual base and gold test runs. This is the *sole* remaining eligibility gate for
   the 12 tasks whose `relevant_file_set` is already derived; **R8 is delivered for
   12 of 14** (§4b). Corrected 2026-10-05: this item previously read "R8 is unmet for
   every task", which contradicted rows above and §4b.
2. **Arithmetic.** Even fully remediated, 13–14 pairs against a requirement of
   **19–85** (δ = 0.20…0.30, π_d = 0.30…0.50, two-sided) is short by roughly an
   order of magnitude, and at ≤ 5 pairs it is *arithmetically impossible* to reach
   significance at any effect size (§2.4).

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
repositories remain on the **curator side** (authority undefined): the arm-workspace contamination channel
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

**GOVERNANCE GAP:** the **operator act of naming** the authorized evaluator identity and
implementation digest for a specific run; the trusted store location and OS-level
immutability; retention duration and deletion detection; curator and
independent-verifier authority. Corrected 2026-10-05: this list previously also
included "the real result-derivation protocol and its format", which is no longer a gap
— `qwen_train/f2_evaluator.py` implements it and it is registered as the authoritative
protocol `f2_evaluator_report_v1`.

**REQUIRES AUTHORIZATION:** naming the authorized evaluator/procedure and
protocol, provisioning the trusted store, and generating any real evidence.

> A SHA-256-shaped `implementation_digest` string does **not** prove evaluator
> implementation provenance. That would be a false scientific claim; it is
> recorded as `NOT ESTABLISHED` above, not as `PROVEN`.

---

## 4e. Authorized F2 evaluation protocol — `f2_experiment_j_v1`

**Module:** `qwen_train/f2_protocol.py` · **Tests:** `tests/test_f2_protocol.py` (42)
**Frozen inputs unchanged:** F0 (`4EAFD2FA…`), S8 (`F095D051…`), the Q8 endpoint
detector (`7BDCCFC8…`), and the governance layer (`2717d8de`).

F2 is a provenance-first, **independently regraded**, paired T/X protocol. The
worker/producer is **not** authoritative for the scientific result.

```
CONTROLLED EXECUTION -> IMMUTABLE EVIDENCE BUNDLE -> TRUSTED ARTIFACT STORE
  -> INDEPENDENT REGRADER -> FROZEN F2 ENDPOINT -> PAIRED T/X STATISTICS
```

### Scientific endpoint (F0, unchanged)

First qualifying filesystem edit within the first **k** decision steps (F0 §5: `k`
is selected during F1 and capped within `[8, 12]`; not fixed at 12 by F0 and not
frozen yet), **strictly after** lesson
delivery, path in the task's frozen `relevant_file_set`, operation in
`{write, patch, edit, create}`; no qualifying edit by step `k` ⇒ **censored at
`k`**. Task success is **secondary** and is never synonymous with the endpoint.
The endpoint is computed by the frozen detector
`f2_endpoint.qualifying_first_edit`; this protocol does not reimplement it, and
delivery ordering is enforced by filtering step records to those strictly after
the verified delivery timestamp.

### Protocol identifier

`f2_experiment_j_v1` — the single authorized F2 result protocol.
`json_test_report_v1` remains the generic reference deriver and is **not** the
scientific F2 protocol.

### Result schema (`F2Result`, machine-readable)

`protocol_id · instance_id · arm · execution_identity · delivery_evidence_identity ·
relevant_file_set_hash · horizon_k · qualifying_operations · first_edit_step ·
first_edit_path · first_edit_operation · delivery_timestamp · endpoint · censored ·
task_success · behavioral_artifact_digest · task_outcome_artifact_digest ·
treatment_artifact_digest` (+ `result_digest`). PRIMARY (`first_edit_step` /
`endpoint`) and SECONDARY (`task_success`) are structurally distinct fields.

### Evidence inputs

Raw ordered behavioral step records
`{step_id, timestamp, function_name, operation, path}`; the task-outcome report
`{fail_to_pass: {node: passed|failed|error|skipped}}`; the rendered treatment
artifact; the lesson block artifact (arm T); the evaluator implementation
artifact. The `relevant_file_set` is supplied on the **curator side** (authority undefined) from the frozen Q8
manifest and must hash to the bundle's declared `relevant_file_set_hash`.

### Verifier procedure

schema/gold-boundary → protocol → evaluator authorization → evaluator
**implementation bytes (required for F2)** → artifact integrity (containment,
role, digest, size) → delivery ordering → **endpoint reconstruction from raw
behavioral evidence** → **independent task-outcome derivation** → declaration
comparison (any disagreement rejected) → frozen S8 proof.

### Evaluator provenance rule

F2 **requires** `evaluator_id`, `evaluator_version`,
`evaluator_implementation_digest`, the **evaluator implementation artifact**,
`procedure_id`, `protocol_version`, and that the authorization's
`protocol_version` equals `f2_experiment_j_v1`. The retained implementation bytes
must hash to the authorized digest. **A SHA-256-shaped string alone is not
evaluator-byte provenance.** If the bytes are absent, F2 evidence is **not
admissible**.

### Clean-room rule

X must be a pure deterministic derivation of T with only L removed: same
instance/repository/base commit/`relevant_file_set_hash`/`horizon_k`/protocol/
evaluator; arm T carries `lesson_block_hash` and a lesson block artifact that
hashes to it; arm X carries none; and
`T_treatment_text.replace(lesson_block_text, "", 1) == X_treatment_text`.
Any unauthorized difference fails closed.

### Delivery provenance (independently authenticated)

The delivery event is established from a **retained delivery-evidence artifact**
(`ROLE_DELIVERY_EVIDENCE`), not from a producer-declared field. The verifier reads
the artifact from the trusted store, verifies its digest, parses it, and requires
its canonical content to equal the bundle's declared delivery record **exactly**.
Endpoint ordering then uses the timestamp from the **verified** artifact. The
verifier never substitutes its own wall-clock time.

Bound in the delivery record: `arm · lesson_block_hash · final_prompt_hash ·
delivery_timestamp · treatment_artifact_hash`. The retained treatment bytes must
also hash to `treatment_artifact_hash`. Delivery cannot be inferred from the
existence of an upstream lesson. A producer that edits a declared field without
editing the retained bytes fails closed (`PROVENANCE_NOT_ESTABLISHED`).

### Identity semantics (T / X / C0)

`EXECUTION_STATE_BY_ARM`: **T → `treatment`**, **X → `control`**, **C0 →
`control_empty`**. X is the control arm (the identical artifact with L removed) and
is **not** the gold/reference state; gold is curator-side (authority undefined) and never enters the
worker-facing identity model. Changing the arm changes the execution identity.

### Timestamp semantics

Two kinds of time are distinguished:

* **Audit timestamps** (`started_at`, `finished_at`) are excluded from the
  scientific identity: changing them does not change the derived result.
* **The verified delivery event** IS scientifically relevant and cryptographically
  bound: the endpoint is defined as the first qualifying edit *strictly after*
  delivery, so the authenticated delivery artifact's digest and timestamp
  participate in the execution and result identity. A genuinely different
  retained delivery event changes the result identity.

The earlier blanket statement that "timestamps never affect scientific identity"
is **superseded** by this distinction.

**One canonical timestamp domain.** Event ordering does NOT use lexical string
comparison. `parse_canonical_timestamp` requires RFC 3339 / ISO-8601 **with an
explicit timezone designator** and normalises to UTC; a missing, non-string,
malformed, or timezone-**naive** value fails closed (`SCIENTIFICALLY_INSUFFICIENT`
— the ordering cannot be established unambiguously). Offsets compare correctly
(`01:00+01:00 == 00:00Z`), sub-second precision does not reorder events, and the
F0 strict-after rule is preserved exactly (an event at the delivery instant is
**not** after it).

### SOTA alignment (2026)

Principles **adopted** from current SWE-bench-family practice:
* the agent's internal gate is never the verdict — grading comes from an
  independent grader over the *retained* artifact (`swebench-verified`,
  `swebench-pro` METHODOLOGY);
* the grader is run **unmodified** and pinned; verdicts must not come from a local
  reimplementation (same source);
* **raw trajectories are retained** so a third party can verify protocol
  compliance (SWE-bench-Live evaluation protocol);
* re-runs are permitted only for external/infra faults, never to re-roll a
  reasoning loss; every run is retained, wins and losses;
* preregistration/procedure outranks prose; the denominator is honest.

Principles **rejected** as unnecessary or conflicting with F0: container-based
grading and patch-capture (J's endpoint is behavioural, not patch-based);
leaderboard/resolve-rate framing (J's primary endpoint is the first qualifying
edit, with task success secondary); model-agnosticism-by-grep as a substitute for
a frozen distiller identity (J freezes the distiller separately).

**Experiment-J-specific** (not borrowable from any benchmark): the causal T/X
contrast with surgical lesson removal, the delivery seam and its ordering rule,
the behavioural endpoint, and the paired within-task design.

### Open engineering blockers (this pass)

* **BLOCKER A — timestamp domain: FIXED.** `_reconstruct_endpoint` compared
  timestamps lexically (`str(r["timestamp"]) > ts`). It now parses both sides with
  `parse_canonical_timestamp` into timezone-aware UTC and compares datetimes,
  failing closed on malformed/naive values. 11 adversarial tests.
* **BLOCKER B — delivered-byte binding: CLOSED (mechanism).** `assemble_f2_bundle`
  stores the **authoritative delivered seam text** (`f2_replay.get_delivery_artifact()`)
  as the treatment artifact, in canonical form, so the verifier's
  `sha256(retained treatment bytes) == delivery.treatment_artifact_hash` is a
  **real delivered-byte binding** — the retained bytes ARE the bytes the
  model-facing seam produced — not metadata consistency. Canonicalisation follows
  the SOTA prompt-binding rule (`canonical_text_bytes`: UTF-8, no BOM, NFC, LF), so
  whitespace/encoding/line-ending changes break the binding as intended.
* **BLOCKER C — production bundle assembly: CLOSED (mechanism).** `assemble_f2_bundle`
  is the missing production→bundle step: given the live seam bytes, ordered
  behavioural records, task-outcome report, lesson block and evaluator
  implementation, it writes every artifact into the trusted store and returns the
  governed `F2Bundle` that `regrade_f2_pair` consumes. Proven by
  `TestBundleAssembly` (9), including a full assemble→regrade round-trip and the
  T/X clean room.

**Wiring: COMPLETE.** `f2_arm_worker.py` calls `assemble_f2_bundle` with its receipt
data (it produces `delivery_identity`, `delivered_artifact`, `outcome_evidence`, and
reads P2 delivery evidence from the trajectory) and persists the bundle, behind the
opt-in `SWARM_F2_EMIT_BUNDLE` gate. Corrected 2026-10-05: this previously read
"Remaining wiring … must call `assemble_f2_bundle`", which was closed by `F2-IMPL-AUTH-017`
and exercised end-to-end.

> **No property above is upgraded to PROVEN merely because a test exists.** The
> assembly and regrade mechanisms are PROVEN. Running them against a **real model in a
> real arm** remains `NOT ESTABLISHED` — for external reasons (no S8 evidence, no
> admitted population, no enforced isolation), not for want of wiring.

### What a digest proves (SOTA honesty)

Per the current prompt-attestation standard, a digest binds the exact byte
sequence and is **necessary, not sufficient**: it proves the retained bytes are
unchanged and that they equal the canonical delivered bytes. It does not prove the
execution was faithful, that the evaluator was honest, or that the trajectory is
complete. Those remain `GOVERNANCE GAP` / `NOT ESTABLISHED` as recorded above.

### Artifact roles

`ROLE_TEST_OUTPUT` / `ROLE_RUN_LOG` (behavioral and task-outcome evidence, and the
treatment artifact) · `ROLE_DELIVERY_EVIDENCE` (delivery evidence) ·
`ROLE_LESSON_BLOCK` (the lesson block; **not** evaluator implementation) ·
`ROLE_EVALUATOR_IMPLEMENTATION` (the evaluator implementation, F2-required). Roles
are bound per slot, so a lesson artifact cannot masquerade as evaluator code, or
vice versa.

### Gold boundary

No gold patch, diff, hunk, source, reference/fixing commit, oracle or hidden
evaluator internals may appear anywhere in an F2 bundle; a recursive scan rejects
such a bundle with `MALFORMED`. The worker receives only the minimum
cryptographic/admission facts.

### Fail-closed conditions

Wrong protocol (`UNKNOWN_SCHEMA`); wrong/unauthorized evaluator or wrong
implementation bytes (`UNAUTHORIZED_PROCEDURE`); missing evaluator implementation
artifact (`PROVENANCE_NOT_ESTABLISHED`); artifact escape/missing/digest/size
(`ARTIFACT_*`); arm or identity mismatch (`IDENTITY_MISMATCH`); no post-delivery
event stream, changed `relevant_file_set` digest, or any declaration disagreement
(`SCIENTIFICALLY_INSUFFICIENT`); gold material (`MALFORMED`). **A missing event
stream is never inferred as a censored run.**

### Operational immutability

The trusted artifact root is defined explicitly (`TrustedArtifactStore` +
`RetentionPolicy`), but **application code does not prove OS/filesystem
immutability**. That is an operator/OS requirement, recorded below, not a code
guarantee.

### Classification

**PROVEN:** evaluator authorization and byte provenance; execution identity
(arm-aware, `treatment`/`control`/`control_empty` — X is not gold); artifact
containment/integrity/**role** (lesson ≠ evaluator implementation; delivery
evidence has its own role); **delivery provenance authenticated from retained
bytes**, not from a producer field; delivery ordering enforced from the verified
event (audit timestamps excluded from identity, the verified delivery event
included); endpoint reconstruction from raw evidence; independent task-outcome
derivation; declaration rejection; T/X clean room; gold boundary; deterministic,
side-effect-free verification; fail-closed taxonomy.

**SUPPORTED:** the pipeline's sufficiency once a real executor emits conforming
bundles into an immutable store.

**INFERRED:** paired T/X statistics consume the derived `F2Result` without
further trust assumptions.

**NOT ESTABLISHED:** that any real F2 execution has occurred; OS-level
immutability of the store.

**GOVERNANCE GAP:** the authorized evaluator identity + implementation digest +
procedure; the trusted store location, retention duration and deletion
detection; curator and independent-verifier authority.

**REQUIRES AUTHORIZATION:** naming the authorized evaluator/procedure, the
population/`n`/`δ`/α decisions, provisioning the receipt key, and running F2.

> Contamination is **not** claimed solved: training contamination of gold patches
> remains a disclosed limitation.

---

## 4f. F2 admission gate

**Module:** `qwen_train/f2_admission.py` · **Tests:** `tests/test_f2_protocol.py::TestAdmission` (10)

`admit_f2_task(...)` is the single gate that decides whether a task's paired T/X
evidence may enter the F2 confirmatory analysis. It composes the frozen layers and
introduces no new scientific semantics. It fails closed at the first unmet layer:

1. **R1-R8 readiness** (`runtime_v2.services.task_readiness.evaluate_readiness`) —
   authoritative and enforced first. A missing declaration/evidence is
   `NOT ESTABLISHED`, never a pass.
2. **Readiness/evidence agreement** — the declaration must match the T and X
   bundles on `instance_id`, `base_commit`, and its own `relevant_file_set` must
   hash to the frozen task set used for regrading.
3. **Paired regrade** under `f2_experiment_j_v1` — evaluator authorization,
   evaluator-byte provenance, artifact integrity, delivery provenance, endpoint
   reconstruction, task-outcome derivation, declaration rejection, and the T/X
   clean room.
4. **Gold boundary before parsing** — a raw bundle mapping is scanned for gold
   keys *before* it is parsed, so gold material cannot slip past the scan by being
   converted to a bundle first.

`F2Admission` reports `admissible`, the readiness verdict, the protocol state, the
clean-room result, and both derived `F2Result`s. Admission is therefore real: it
cannot be reached from declarations alone.

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

**Still open — and only enforcement.** MCP/tool-mediated network access is denied and
credentials no longer leak, but a shell that can execute arbitrary code retains the
**host's network** unless the operator isolates it. MCP denial alone therefore still
does not establish that a learning event was network-free.

Four unprivileged layers now exist beneath that gap (`F2-IMPL-AUTH-022/023`): probe
**observation** (`python -m qwen_train.f2_isolation`), **independent verification**,
a **cryptographically bound attestation** carrying a required negative control, and a
**readiness gate that refuses a caller-typed `"denied"`**. What remains is
**PRIVILEGED enforcement**, which is an operator/administrator action and cannot be
written in Python. Corrected 2026-10-05: this previously read "it is not a code change
I can make unilaterally", which understated the four layers now implemented.
**GOVERNANCE GAP — enforcement only.**

---

## 6. Authorization checklist — what the operator must decide

| # | Decision | Recommendation | Consequence if deferred |
|---|---|---|---|
| **Q1** | Population strategy | Import an enlarged, contamination-screened pool (SWE-bench-Live class). Alternative: keep 13–14 and reframe exploratory. | No confirmatory result is possible at 14 pairs. |
| **Q2** | Test sidedness | **Two-sided.** Harm must be reportable. | A harmful lesson becomes unreportable. |
| **Q3** | δ (smallest effect of interest) | **δ = 0.20**, pre-registered, conditional on Q1. | No frozen N is possible. |
| **Q4** | Primary endpoint: keep F0's binary, or move to censored steps-to-first-edit? | **Keep F0's binary** and report the survival analysis as a pre-specified secondary — F0 §16 protects the primary, and saturation is a *hypothesis the calibration tests*, not an established fact. | Either is defensible; this is a scientific judgment. |
| **Q5** | Ratify **block-level** exclusion (invalid arm ⇒ invalid pair) | **RATIFIED** — `F2-IMPL-AUTH-018` (operator, 2026-10-05). Enforced by construction: `f2_admission.admit_f2_task` requires both T and X bundles and fails closed. Excluding because T won, X won, or the result is inconvenient is **FORBIDDEN**. Corrected 2026-10-05: this row previously still read "Ratify" as a pending decision. | Analysis undefined for partially-invalid blocks. |
| **Q6** | Infrastructure-failure budget ≤ 30 % | **AUTHORIZED** — `F2-IMPL-AUTH-018` (operator, 2026-10-05). An **operational feasibility ceiling, NOT a statistical property**: on breach, STOP and investigate the environment; the ceiling never justifies selectively discarding observations; all infrastructure failures stay in the ledger and the denominator is never reset. Implemented at threshold 0.30 and pinned by tests to change no statistic and drop no pair. Corrected 2026-10-05: this row previously still read "Authorize" as a pending decision, and mislabelled the gate as *statistical*. | At F1's 50 % loss rate the design cannot hold together. |
| **Q7** | Distiller model identity + weights digest | Fix both now; `SWARM_DISTILLER_MODEL` and `SWARM_DISTILLER_WEIGHTS_DIGEST` (or explicit unavailability). | Synthesis fails closed; no lesson can be produced. |
| **Q8** | Per-task `relevant_file_set` (R8) | **DELIVERED** — see §4b. Governed reference-modified-file derivation, hash-frozen before any trajectory. | 12 derived; 2 refused (fail-closed); `reference_digest` recorded. Corrected 2026-10-05: this cell previously read "8/14 derived; 6 fail closed" and contradicted §4b. |
| **Q9** | Shell network-egress isolation for the learning event | Authorize an operator-level control. **PRIVILEGED enforcement** is the only missing layer; observation, independent verification, cryptographically bound attestation and the readiness gate are already IMPLEMENTED. | "Clean-room" remains an assumption. |
| **Q10** | **No-lesson X/C0 calibration run** (run count, tasks, replicates, censoring convention) | Authorize — cheapest, highest-value measurement available. | δ and the other F1 statistical parameters remain to be authorized. `π_d` is NOT identifiable from the lesson-free pilot (no T arm), so the `π_d` planning value must be an **F1-authorized planning decision** (or another explicitly F1-authorized resolution); `N` cannot be frozen until then. Corrected 2026-10-05. |
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

*Engineering frozen at `cea3c1d1`. Scientific authorization NOT GRANTED.* (Updated 2026-10-05; this line previously read `1dd07120`.)