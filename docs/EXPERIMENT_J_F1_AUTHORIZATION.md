# Experiment J — F1 Operational Authorization (NEW Parameters, NOT Recovered)

**Status:** COMPLETE — F1-OP-001, F1-OP-002, F1-OP-003 AUTHORIZED; F1-OP-004 FULLY AUTHORIZED (F1-OP-004a horizon + F1-OP-004b censored treatment)
**Date:** 2026-09-24
**Authorizing body:** Human operator (Rob) — issued as NEW F1 operational authorization, NOT a recovered historical parameter.
**Frozen scientific source of truth:** `docs/EXPERIMENT_J.md` (frozen F0, commit `20a1989b`). **This document does not amend F0.**

---

## 0. Provenance & Non-Recovery Statement

Repository archaeology established that the four parameters below were **never historically specified** (git history, commit-message and content searches, prior documentation, and the A–J tool-selection session records all return no prior values). They are therefore recorded as **NEW F1 operational authorizations** and MUST NOT be described as recovered F0 parameters.

- F0 = frozen scientific design (`docs/EXPERIMENT_J.md`).
- F1 = operationalization required to execute the frozen design.
- The frozen k-selection rule (F0 §5) is: *"k is selected during F1 (no-lesson pilot) as the 95th percentile of steps-to-first-edit across pilot runs, capped at [8, 12]."* This rule and the inherited definitions (step, steps_to_first_edit, k, C0) are NOT changed by this document.

Frozen inherited definitions (unchanged, for reference):

```
step:
One ATIF trajectory decision step (one ATIF step record).

steps_to_first_edit:
The ATIF step position of the first filesystem action where
operation ∈ {write, patch, edit, create} and the path belongs to the
F1-frozen relevant_file_set.

k:
P95(steps_to_first_edit), bounded to [8, 12].

C0:
No-lesson control with ActiveLessons empty/absent.
```

---

## 1. F1-OP-001 — Pilot Run Count

**STATUS: NEW — NOT RECOVERED — AUTHORIZED**

**Value:** 20 independent no-lesson pilot runs.

**Constraints:**
- Each run must have a distinct fresh `rollout_id` / `run_id`.
- No run may reuse a prior session/checkpoint/semantic-cache state (per F0 §7 Fresh-Worker Definition and §8 Contamination Framework).

**Rationale (recorded, not recovered):** 20 independent observations provide the calibration sample from which the frozen P95 rule can be instantiated. This is a new operational choice, not a historical F0 parameter.

---

## 2. F1-OP-002 — Pilot Task

**STATUS: NEW — NOT RECOVERED — AUTHORIZED**

**Value:** `repair_task1` / `repair_task1_sandbox_bounds`

**Task:** Repair `sandbox_bounds()` in `swarm_os/lib/paths.py`.

**Canonical buggy base commit:** `45d9f619` ("INFRA: bug1 sandbox_bounds relative_to inverted").

**Known bug:** the inverted `relative_to` coverage check. The expected correction is the existing task fixture's `root.relative_to(base)` relationship.

**Task substrate:** the canonical `repair_task1` task substrate identified by the read-only archaeology:
- `qwen_train/run_repair_task.py` (runner thin-adapter over `cli_baseline_swe` + `run_curriculum._attempt_once`)
- `tests/test_sandbox_bounds.py` (F2P gate; byte-identical in the canonical clone)
- `qwen_train/curriculum/curated_bugs.jsonl` row `sandbox_bounds_relative_to`
- Prior execution evidence: `qwen_train/results/tool_selection_experiment.json`, `qwen_train/results/EXPERIMENT_SESSION_2026-09-18.md`

**Do NOT substitute:** a SWE-rebench task, any other curriculum bug, or the fs_patch bug family.

**Workspace requirement (explicit):**
- Use a **FRESH isolated clone** created from the canonical buggy commit `45d9f619` for the pilot, rather than reusing the existing `swe_probe_work/repair_task1` or `swe_probe_work/sandbox_bugs` working copies — both contain pre-existing untracked runner debris (`a.txt`, `temp_react.txt`, etc.).
- Each pilot run must begin from a clean copy of the same buggy `45d9f619` tree (per F0 §8 Workspace/code state REQUIRED CONTROL: `git reset --hard` per arm, `git status --porcelain` clean).
- Keep per-task workspace isolation (`SWARM_WORKSPACE_ROOT` scoped to the isolated task clone — never the shared `swe_probe_work` corpus).

---

## 3. F1-OP-003 — Frozen relevant_file_set

**STATUS: NEW — NOT RECOVERED — AUTHORIZED**

**Value:**

```
{
  "swarm_os/lib/paths.py"
}
```

- This is the ONLY path whose filesystem edit can satisfy the first-edit endpoint.
- The following file is NOT part of relevant_file_set: `tests/test_sandbox_bounds.py`. It is a verification/test gate only and must not count as the first-edit endpoint.
- The relevant_file_set MUST be frozen before the first pilot trajectory begins and MUST NOT be expanded, narrowed, or changed after observing pilot results.
- An immutable/hashable representation of this set MUST be recorded in the authorization/pilot provenance (e.g., canonical-sorted, serialized set + SHA-256) so later analysis can prove the set was unchanged.

**Frozen-set hash (recorded at authorization time):**
*To be filled by the pilot harness at first-run time; the set shall be serialized as the canonical JSON array of its sole member and hashed with SHA-256. The frozen F0 document and this authorization both forbid post-hoc modification.*

---

## 4. F1-OP-004 — P95 Computation Method

**STATUS: NEW — NOT RECOVERED — FULLY AUTHORIZED (F1-OP-004a + F1-OP-004b)**

### 4.1 Authorized components (unchanged)

- The pilot consists of **20 independent observations** (F1-OP-001).
- Method: **deterministic nearest-rank empirical P95** over the 20 pilot observations.
- A **qualifying first edit** is measured as the first edit-type filesystem action (`write`, `patch`, `edit`, or `create`) targeting the frozen relevant_file_set (`{"swarm_os/lib/paths.py"}`).
- One ATIF decision step = one ATIF trajectory decision step / step record. Do not substitute raw model-generation tokens, wall-clock time, or generic MAX_TURNS for the step unit.
- For a successful qualifying first edit, the observation is the ATIF decision-step ordinal of that first qualifying edit.
- Do not count edits to files outside relevant_file_set.
- Do not count test-file edits as first-edit observations (tests are outside the relevant_file_set).
- A run that reaches the authorized pilot observation horizon without producing a qualifying first edit MUST NOT be silently discarded. It is a **right-censored observation** and MUST remain in the raw/provenance evidence, recorded with:
  - `run_id` / `rollout_id`
  - censoring status
  - last observed decision step
  - reason no qualifying edit occurred

### 4.2 F1-OP-004a — Pilot observation horizon (AUTHORIZED)

- **Horizon = 12 ATIF decision steps.**
- **The horizon is inclusive.** A qualifying first edit occurring at step 1 through step 12 is an observed endpoint.
- A run that reaches the end of step 12 without a qualifying first edit is classified as **right-censored at step 12**.
- **Inclusive step-12 boundary, explicit:** an edit occurring *at* step 12 is a qualifying observed endpoint; only a run with no qualifying first edit through the end of step 12 is right-censored.
- This value is a **NEW F1 operational authorization, NOT recovered from F0/history**.
- **Rationale (recorded, not a claim of optimality):** the frozen F0 endpoint permits final `k` only in `[8,12]`; a 12-step observation window ensures the entire frozen admissible k range can be observed. This does **not** establish 12 as scientifically optimal.

### 4.3 F1-OP-004b — Censored-observation treatment (AUTHORIZED)

- A right-censored run contributes the value **12** to the 20-observation empirical P95 set.
- It remains fully identified as censored in raw/provenance evidence.
- Censored runs are NOT silently discarded.
- All 20 pilot runs therefore contribute one value to the operational P95 calculation.
- This is a **NEW F1 operational authorization, NOT recovered from F0/history**.
- **Explicit convention note:** using the horizon value as the operational observation for P95 is a **chosen convention for this pilot**, not a claim that F0 specified a survival-analysis estimator.

### 4.4 P95 calculation (authorized, applies the frozen F0 clamp)

- Use the frozen nearest-rank empirical P95 rule.
- n = 20.
- rank = ceil(0.95 × 20) = 19.
- Sort the 20 observation values ascending.
- P95 = the 19th ordered observation.
- Then apply the frozen F0 clamp: `k = min(12, max(8, P95))`.
- The frozen `[8,12]` rule is NOT changed.

### 4.5 Audit language

> The repository/history contained no recoverable Experiment-J pilot horizon or censored-observation estimator. The 12-step horizon and horizon-value treatment of right-censored observations are NEW F1 operational authorizations. They were not inferred from MAX_TURNS, wall-clock timeouts, historical trajectory lengths, or the frozen k-cap.

**F1-OP-004 is now fully authorized.** With F1-OP-001 through F1-OP-004 complete, the no-lesson pilot's operational parameters are all recorded; the pilot itself remains NOT RUN, and F1 (freezing `k`, `n`, and the practical-effect criterion) remains pending until the pilot executes.

---

## 5. Change-Control Boundary

- This document does NOT modify `docs/EXPERIMENT_J.md` (frozen F0, commit `20a1989b`).
- The frozen scientific design is unchanged: the research question, causal contrast (T vs X), T/X definitions, treatment artifact identity, primary endpoint, rediscovery rule, fresh-worker definition, contamination rules, primary interpretation, and confirmatory exclusion rules remain as frozen in F0 §16.
- Any change to the four parameters above is a NEW operational authorization and must be appended to this record with a dated entry; it is not a change to F0.

---

## 6. Post-Authorization Evidence/Checks (standing)

1. Frozen-definitions verbatim check: this record reproduces F0's step / steps_to_first_edit / k / C0 definitions unchanged.
2. `relevant_file_set` immutability: hash of the set recorded at first pilot run; verify unchanged after pilot results are observed.
3. Read-only state floor maintained: Qdrant `ActiveLessons` empty (C0); `SWARM_EVAL_TICK` unset/0; `SWARM_RECEIPT_KEY` unset; candidate `140bd53f9430` unevaluated and unpromoted.
4. No Twine / Click / Pyfakefs / Sandbox Bounds / 25-rollout runs as experiment steps in this phase.
5. Pilot runs keyed by distinct fresh `rollout_id` / `run_id`, fresh isolated clones at `45d9f619`.

---

## 7. Authorization Log

| Ref | Item | Status | Date | Notes |
|-----|------|--------|------|-------|
| F1-OP-001 | Pilot run count = 20 | AUTHORIZED | 2026-09-24 | NEW, not recovered |
| F1-OP-002 | Pilot task = repair_task1 / repair_task1_sandbox_bounds, base `45d9f619`, fresh isolated clones | AUTHORIZED | 2026-09-24 | NEW, not recovered |
| F1-OP-003 | relevant_file_set = `{"swarm_os/lib/paths.py"}`; test file excluded; immutable | AUTHORIZED | 2026-09-24 | NEW, not recovered |
| F1-OP-004a | Pilot observation horizon = 12 ATIF decision steps, inclusive; right-censor at end of step 12 | AUTHORIZED | 2026-09-24 | NEW, not recovered |
| F1-OP-004b | Censored-observation treatment: censored run contributes 12 to the P95 set; retained as censored in provenance; all 20 runs contribute one value | AUTHORIZED | 2026-09-24 | NEW, not recovered (chosen convention, not F0 survival analysis) |
| F1-OP-004 | P95 = nearest-rank empirical P95, n=20, rank=ceil(0.95×20)=19; `k = min(12, max(8, P95))` | FULLY AUTHORIZED | 2026-09-24 | Frozen `[8,12]` clamp unchanged |
| F1-OP-004-CLARIFICATION | Interpretation of "successful qualifying first edit": action-based. A qualifying first-edit endpoint is the first ATIF decision-step record where function=`filesystem`, operation ∈ {write, patch, edit, create}, and target path ∈ the frozen relevant_file_set. "Successful" means successfully satisfying those qualification criteria. It does NOT require filesystem tool acceptance, file mutation, patch application, tests passing, or repair correctness. A rejected filesystem action can qualify as the endpoint when it satisfies the criteria above. Consequence for Observation 2: qualifying first-edit endpoint = ATIF step 4; patch rejected; file not mutated; repair correctness = UNKNOWN; capability credit = +0 / -0. This is a recorded authorial interpretation, not a change to F0 or to the F1 operational parameters. | CLARIFICATION | 2026-09-25 | Dated authorial interpretation per §5 change-control; does not modify F0 or F1-OP-001 through F1-OP-004 |
| F1-OP-INFRA-001 | SWARM_F1_NO_WEB_TOOLS=1 is active in the pilot harness for the 20-run C0 pilot. This strips web_search and web_fetch from the coder agent's tool surface. Introduced as an infrastructure correction after the original F1 authorization to prevent web-tool drift (Observation 1). It is NOT a change to the scientific endpoint, treatment, task, model, or horizon. The pilot measures the current corrected runtime condition, not the original pre-correction runtime. Historical observations 1-4 must NOT be relabeled to appear as if they ran under this condition. | INFRASTRUCTURE CONDITION | 2026-09-26 | Operational provenance record; does not modify F0, F1-OP-001 through F1-OP-004, or the scientific protocol |
| F1-OP-INFRA-002 | Workspace-routing correction. The evaluator's per-run SWARM_WORKSPACE_ROOT must be transported to the backend filesystem execution path via the existing per-request HTTP header mechanism (X-Swarm-Workspace-Root), credential-gated and validated, then propagated as a request-scoped context variable. The authorized transport chain is: evaluator environment variable → CLI HTTP header → credential-gated backend reception → validated request-scoped workspace context → runtime_v2/services/tool_executor.py containment and path logic → filesystem_handler() receiving the request-scoped workspace root. The implementation MUST cover: (1) request-scoped workspace-root context variable, (2) _norm() resolution, (3) _contained() read-before-write containment guard using the per-request workspace root when present rather than the module-level _ROOT, and (4) filesystem_handler() call sites. Backend validation MUST require: absolute path, existing directory, resolved canonical path, containment under the configured Experiment J workspace parent/root, and valid harness authentication before honoring the header. When the header is absent, behavior MUST fall back to existing project-root behavior. The existing filesystem sandbox containment logic in swarm_os/lib/mcp/filesystem.py MUST remain unchanged. This amendment does NOT authorize: classifier changes, PromptRepairer changes, lesson retrieval changes, lesson instrumentation or logging, scientific endpoint changes, treatment changes, task changes, model changes, model parameter changes, timeout changes, horizon changes, scoring changes, F0 changes, observation-protocol changes, or retrospective relabeling of N=1 or N=2. N=3 remains prohibited until the correction is implemented and independently verified against the complete pre-N=3 verification checklist. The confirmed routing defect causes the agent to operate on the wrong workspace directory when the evaluator workspace is not propagated to the backend, making those behavioral results uninterpretable as model-performance evidence. N=1 and N=2 historical results must not be retroactively relabeled as valid model-performance observations. | INFRASTRUCTURE CONDITION | 2026-09-27 | Operational provenance record; does not modify F0, F1-OP-001 through F1-OP-004, or the scientific protocol |

**END OF F1 OPERATIONAL AUTHORIZATION (WORKING RECORD)**