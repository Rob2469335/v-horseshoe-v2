# Experiment J — Evidence-Grounded Transferable Lesson Synthesis (Architecture B)

**Status:** engineering implementation. Not a scientific result. Does not authorize
a learning event.
**Date:** 2026-10-03
**Implements:** §5 of `docs/EXPERIMENT_J_TASK_READINESS_CONTRACT.md` (Option B).
**Code:** `swarm_os/services/lesson_synthesis.py`, integration in
`swarm_os/services/prompt_repairer.py`.
**Tests:** `tests/test_lesson_synthesis.py` (76) plus updated promotion suites.

---

## 1. Authority chain

| Rank | Artifact | Status |
|---|---|---|
| 1 | `AGENTS.md` | repo operating contract |
| 2 | `docs/EXPERIMENT_J.md` (`4EAFD2FAF2BA7907…`) | **FROZEN SCIENTIFIC AUTHORITY** — unchanged by this work |
| 3 | `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` (`FF4A6722…`) | current operational authority — unchanged |
| 4 | `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md` | **scope-limiting**; §95-106 excludes receipt-key provisioning, ACTIVE creation, evaluation, promotion, N=2 |
| 5 | `docs/EXPERIMENT_J_F2_OP_INFRA_004_AUTHORIZATION.md` (2026-10-02, newest F2 authorization) | scoped to the F2 **execution** path; §130 excludes `swarm_os/services/*` from *its* scope |
| 6 | `docs/EXPERIMENT_J_F2_WORKER_EXECUTION_AUTHORIZATION.md` | §7 frozen-delivery invariant; §402-422 exclusions |
| 7 | `docs/EXPERIMENT_J_TASK_READINESS_CONTRACT.md` (2026-10-03, newest) | records §5 lesson-authenticity as an OPEN scientific decision, Option B recommended |
| 8 | `docs/LEARNING_EXPERIMENT_STATE.md` | live state |

**Governing instruction for this work:** the operator's explicit current
instruction, which under `AGENTS.md §1` ranks above the documents and selects
Option B from the readiness contract. It does **not** lift the §4/§5 exclusions,
and this implementation honours every one of them (see §9).

**Does any document supersede another?** `PROVEN` — no. The F0 freeze is
untouched; the F2 authorizations scope *different* subsystems (execution path,
worker seam) and neither claims the learning/governance layer. The readiness
contract is the newest document and explicitly leaves §5 open rather than
resolving it, which is the gap this work fills at engineering level.

---

## 2. The current pipeline, traced

Traced by source inspection of the production path, 2026-10-03.

```
failure (verified)
  │
  ├─ evaluation_bridge.classify_evaluation_failure()      evaluation_bridge.py:23-63
  │     reads ONLY: backend_reachable, model_endpoint_reachable,
  │                 step_count, successful_tool_calls, termination_reason
  │     verdict fields present since 5f703869 but consulted FIRST
  │
  ├─ _build_failure_reason()      :66-86   provenance prose  (trigger)
  ├─ _build_hypothesized_action() :89-91   2 canned strings (action)
  │
  └─ process_failure()            prompt_repairer.py:1196-1294
        guards: model_variability, is_safe_lesson, task_id non-empty,
                one rollout ⇒ one evidence run, watch-loop excluded
        creates candidate {trigger, action, evidence_runs, evidence_tasks}
        EVIDENCE_GATHERING → CANDIDATE at ≥3 runs (:1289)
          │
          ├─ evaluate_candidate()  :1314-1399
          │     evaluator present, pass, no unrelated_regression,
          │     receipt key present ⇒ mint HMAC receipt ⇒ PROMOTABLE
          │
          ├─ _derive_learner_artifact(cand)   :280-330
          │     "<condition>: <action>" when a closed-vocabulary keyword
          │     matches the trigger, else "<action>" alone
          │     7 _CONDITION_KEYWORDS, only 2 reachable from a bridge trigger
          │
          └─ promote()  :1401-…
                MIN_EVIDENCE_RUNS=3, MIN_EVIDENCE_TASKS=2, receipt HMAC +
                state-hash binding, evaluator identity, governance version,
                is_safe_lesson, MAX_RULE_TOKENS, contradiction, duplicate,
                global token budget
                  ⇒ ActiveLesson(rule=_derive_learner_artifact(cand))
                     ⇒ LessonManager.store() ⇒ Qdrant `ActiveLessons`
                        ⇒ _select_for_render() ⇒ "[BEHAVIORAL LESSONS]" block
                           ⇒ worker prompt  (stream_runner.py:697,813;
                               agent_service_v2.py:2818)
```

### 2.1 What the worker would actually receive — `PROVEN`

`ActiveLesson.rule` is set from `rule_text = _derive_learner_artifact(cand)`
(`prompt_repairer.py:1472` region) and is rendered verbatim by
`render_active_lessons_with_records` (`lesson_manager.py:587-619`). For any
candidate produced by the evaluation bridge, that string is one of exactly two
canned, task-agnostic, tautological texts (§7 of the readiness contract).

### 2.2 Weaknesses found

| # | Weakness | Class |
|---|---|---|
| W1 | The bridge hypothesis is one of two fixed strings; no evidence shapes it. | `PROVEN` |
| W2 | `is_safe_lesson` is a **prompt-injection** membrane (9 hostile meta-instructions). It says nothing about solution leakage. | `PROVEN` |
| W3 | Nothing between "run failed" and "rule text" asserts the rule is transferable or non-tautological. | `PROVEN` |
| W4 | Nothing strips source-task identity, so a future richer hypothesis could leak the repair into an ACTIVE lesson. | `PROVEN` |
| W5 | `reflection_loop`'s LLM distiller (the only rich hypothesis source, `DISTILLER_PROMPT`) passes **no `task_id`**, so `process_failure:1230` returns `ignored: untagged_event` — it can never create evidence. | `PROVEN` |
| W6 | `_derive_learner_artifact` ignores `evidence_runs`, so multiple runs' evidence cannot influence the rule at all. | `PROVEN` |

**Note:** bridge genericity alone does *not* prove the final ACTIVE lesson would
be generic — W5/W6 are exactly why an LLM-distilled rule could in principle be
richer. That is why the fix is placed at the *artifact* boundary rather than in
the bridge.

---

## 3. Architecture B, as implemented

```
verified failure evidence (closed dataclass)
  │
  ├─ A. diagnose(ev)                deterministic, fail-closed
  │     7 preconditions, then derives behavioural FEATURES from measurements
  │     → Diagnosis(mechanism, features, evidence_ref)
  │        no features ⇒ no diagnosis ⇒ no lesson
  │
  ├─ B. abstract(diagnosis, distiller)     injected seam, fail-closed
  │     no distiller ⇒ no principle. NO local fallback exists.
  │     → Principle(text, grounded_in=feature_codes)
  │
  ├─ C. scrub(principle, ev)         LAYER 1 — deterministic
  │     lexicon DERIVED from this run's own provenance (not a fixed blacklist)
  │     identity hit ⇒ REJECT · harmless hit ⇒ REDACT · structural ⇒ REJECT
  │     → ScrubReport(text, redactions, identity_hits, passed)
  │
  └─ D. validate(scrubbed, ev)       LAYER 2 — independent
        L1 grounding · L2 diagnosis · L3 transferability · L4 leakage
        L5 actionability · L6 non-tautology · L7 provenance integrity
        L8 independent validation
        + the nine section-8 leakage questions (Q1…Q9)
        → QualityVerdict(passed, checks, validator_id, rationale)
              ⇒ SynthesisAttestation (persisted on the candidate)
                 ⇒ [existing gates] → PROMOTABLE → receipt → ACTIVE
```

### 3.1 Why Stage B has no local fallback

A locally-generated "principle" would be a catalogue lookup dressed as
learning — the precise defect this work exists to remove. `PROVEN` by source
inspection: `abstract()` returns `(None, "no_distiller_available")` when
`distiller is None`, with no other branch producing text.

### 3.2 Data structures

All frozen dataclasses in `lesson_synthesis.py`: `FailureEvidence`,
`EvidenceFeature`, `Diagnosis`, `Principle`, `ScrubReport`, `QualityVerdict`,
`SynthesisAttestation`. `FailureEvidence` is closed by construction, so there is
no path for free-form task text to reach abstraction unmediated.

---

## 4. Provenance boundaries

| Boundary | Rule |
|---|---|
| evidence → diagnosis | measurements only; no task content |
| diagnosis → principle | mechanism + feature codes; task content excluded by construction |
| principle → worker-facing | must survive Layer 1 **and** Layer 2 |
| provenance storage | `SynthesisAttestation` holds `evidence_ref`, `feature_codes`, `mechanism` — **never** delivered to the worker (L7) |
| delivered text | `_derive_learner_artifact` returns `attestation.principle_text` only |

`mechanism` and `evidence_ref` are retained as provenance on the candidate for
audit, and are excluded from the delivered string.

---

## 5. Leakage membrane

**Layer 1 — deterministic provenance scrub** (`scrub`)

The lexicon is built **from the run's own evidence**, because the identifiers that
matter are exactly those no fixed blacklist knows in advance: task id and its
org/repo/instance components, test node ids, test file names, commit shas, URLs,
path-like tokens, symbol names, plus fixed literals (`gold patch`, `test_patch`,
`swe-bench`).

- identity-bearing ⇒ **REJECT** (redaction could leave a resolvable fragment)
- explicitly harmless (`_HARMLESS_PROVENANCE`: python, pytest, posix, linux,
  windows, darwin, ascii, utf-8) ⇒ **REDACT**
- structural patterns ⇒ **REJECT**: line numbers, symbol declarations,
  `test_*` references, benchmark names, URLs, patch artefacts (`@@ -n`, `+++`,
  `---`, `diff --git`, "apply the patch/diff")

A bare short numeric token is deliberately **not** identity: it is unresolvable
without the repository name (already blocked) and treating it as identity would
reject innocuous rules. The composite `<repo>-<number>` form is blocked.

**Layer 2 — independent semantic validation** (`validate`)

Answers §8's nine questions plus L1–L8. Independence is structural: the
signature accepts only `(scrubbed_text, ev, report)`, and its executable body
references no distiller, prompt or `Principle` (proven by AST test).

### 5.1 The three-state distinction (§11)

| State | Test applied | Outcome |
|---|---|---|
| **A. Solution leakage** | prescriptive-solution markers (Q7), patch artefacts (C), symbol/file/test disclosure (Q3–Q6) | REJECT |
| **B. Instance memorization** | L3 transferability (no general condition), Q2 repository reference, identity lexicon | REJECT / fail quality gate |
| **C. Genuine transferable learning** | L3 + L6 + Q9 pass, no identity hit | eligible to continue |

---

## 6. Lesson-quality gate (L1–L8)

| Check | Pass condition | Class |
|---|---|---|
| L1 grounding | non-empty text | `PROVEN` |
| L2 diagnosis | *(structural precondition)* a `Diagnosis` exists with a mechanism and ≥1 feature | `PROVEN` |
| L3 transferability | a general-condition marker present | `SUPPORTED` (heuristic) |
| L4 leakage | no residual derived-identity term | `PROVEN` |
| L5 actionability | ≥6 words and ≤ `MAX_RULE_TOKENS` | `PROVEN` |
| L6 non-tautology | no `_TAUTOLOGY_MARKERS` | `SUPPORTED` (marker-based) |
| L7 provenance integrity | provenance held outside worker-facing text | `PROVEN` |
| L8 independent validation | validator id recorded and distinct from generator | `PROVEN` |

**No numerical quality score is used.** A weighted score would be an invented
threshold. Every check is a boolean over an observable property.

**`REQUIRES AUTHORIZATION`:** L3 and L6 are marker heuristics. Whether they are
adequate measures of *scientific* transferability is a methodology judgment, not
an engineering one. Likewise Q8 ("could a worker solve the task more easily?") is
proxied by prescriptive-solution detection and is labelled `INFERRED` in its own
detail string.

---

## 7. Promotion integration

Added to `promote()` immediately after the `eval_result["pass"]` check and
**before** the receipt verification:

1. `SynthesisAttestation.from_dict(cand.get("synthesis"))` — missing/malformed ⇒
   `rejected: no_validated_synthesis`
2. `attestation.validator_passed` false ⇒ `rejected: synthesis_quality_failed`
3. empty principle ⇒ `rejected: empty_principle`

`attach_synthesis()` additionally enforces non-empty, `validator_passed`,
`≤ MAX_RULE_TOKENS`, and `is_safe_lesson`, and refuses after
`PROMOTABLE`/`ACTIVE` so the evaluated text cannot be decoupled from the
promoted text.

**`_canonical_state` now binds `synthesis`.** Without this the delivered rule was
not covered by the evaluation receipt: synthesis could be swapped after
evaluation and the promoted text would differ from the evaluated text while the
receipt still verified. This closes that hole.

**Nothing was removed.** `MIN_EVIDENCE_RUNS = 3`, `MIN_EVIDENCE_TASKS = 2`,
`GOVERNANCE_VERSION = 2`, `_CONDITION_KEYWORDS`, every receipt check, the
contradiction and duplicate checks, and the promotion state machine are
unchanged.

### 7.1 Consequence: existing tests were updated, not weakened

The prerequisite is fail-closed, so 22 existing promotion tests initially failed
because they built candidates with no synthesis. Each was updated to satisfy the
strengthened contract. Verified by diff: **only 3 assertion lines were removed**,
each replaced by a stronger equivalent:

- `assert expected.startswith("no-edit: ")` ×2 → `assert expected == VALID_PRINCIPLE`
  (exact identity instead of prefix)
- `assert res == "evaluation_passed"` → removed only where the test was
  restructured to bypass evaluation; the token-gate coverage it lost was replaced
  by a **stronger** two-part assertion (attach-side bound + promotion-side
  defense-in-depth).

Two further changes were **strengthenings** prompted by real findings:

- `attach_synthesis` now refuses over-budget and unsafe principles.
- `test_oversized_action_returned_unchanged_and_gate_rejects` now proves the
  token gate fires even when the attach API is bypassed.

---

## 8. Fail-closed behaviour

`synthesize()` returns `(None, reason)` at the first failing stage:

| Stage | Reasons |
|---|---|
| A | `malformed_evidence`, `missing_evaluator_verdict`, `evaluator_reported_success`, `non_capability_verdict`, `infrastructure_unreachable`, `no_observable_trajectory`, `not_behavioral:*`, `no_declared_test_measurement`, `insufficient_evidence_for_mechanism` |
| B | `no_diagnosis`, `no_distiller_available`, `distiller_error:*`, `distiller_returned_empty`, `distiller_returned_too_long`, `distiller_exceeded_token_ceiling`, `insufficient_causal_confidence:*` |
| C | `empty_principle`, `provenance_identity_detected`, `structural_leak:*`, `redacted_to_empty` |
| D | `failed: [checks]` |

There is no degraded-success path. `PROVEN` by inspection of `synthesize`.

---

## 9. Scientific boundaries honoured

| Prohibited | Status |
|---|---|
| F0 modified | **No** — `4EAFD2FAF2BA7907…` verified byte-identical |
| Primary endpoint reinterpreted | **No** — untouched |
| `SWARM_RECEIPT_KEY` provisioned | **No** — 0 assignments in `.env`; absent from Process/User/Machine |
| Receipt minted / verified | **No** — `lesson_synthesis` contains no `hmac`, `_receipt_key`, `_sign_receipt` (asserted by test) |
| ACTIVE lesson created | **No** — no Qdrant contact; `ActiveLessons` never written |
| Learning event / F2 / evaluation run | **No** |
| Promotion gates weakened | **No** — one prerequisite **added**; constants and checks unchanged |
| D1/D2/D3 replaced | **No** — regression tests assert they remain |
| `MIN_EVIDENCE_RUNS=3` / `MIN_EVIDENCE_TASKS=2` changed | **No** — asserted by test |
| AssertionError readiness rule restored | **No** |
| Historical evidence reinterpreted | **No** — untouched; residue remains residue |
| Tests writing production state | **No** — stores byte-identical |

---

## 10. Known limitations

1. **Stage B requires an injected distiller.** In this environment no LLM is
   wired to it, so a live learning event cannot yet produce a principle.
   `PROVEN` — `abstract()` has no fallback.
2. **The distiller prompt (`_B_SYSTEM`/`_B_USER`) is engineering scaffolding, not
   a validated prompt.** Its output quality is `NOT ESTABLISHED` until exercised
   against real failure evidence.
3. **L3/L6 are marker heuristics** — `SUPPORTED`, see §6.
4. **W5 remains open.** `reflection_loop`'s richer distiller still cannot
   contribute evidence because it passes no `task_id`. Routing it through
   synthesis is `REQUIRES AUTHORIZATION`.
5. **No live end-to-end run has occurred**, by instruction. Every property above is
   established by unit/integration tests and source inspection, not by a
   production run.