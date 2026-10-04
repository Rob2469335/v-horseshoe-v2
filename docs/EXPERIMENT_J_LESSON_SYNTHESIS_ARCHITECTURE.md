# Experiment J — Evidence-Grounded Transferable Lesson Synthesis (Architecture B)

**Status:** engineering implementation, including W5 production integration.
Not a scientific result. Does not authorize a learning event.
**Date:** 2026-10-03
**Implements:** §5 of `docs/EXPERIMENT_J_TASK_READINESS_CONTRACT.md` (Option B).
**Code:** `swarm_os/services/lesson_synthesis.py`, integration in
`swarm_os/services/prompt_repairer.py`.
**Tests:** `tests/test_lesson_synthesis.py` (85) plus updated promotion suites
(`test_prompt_repairer.py`, `test_learner_artifact_derivation.py`,
`test_experiment_lifecycle.py`).

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
  └─ D. validate(scrubbed, ev, report)   LAYER 2 — deterministic structural gate
        L1 grounding · L2 diagnosis · L3 transferability · L4 leakage
        L5 actionability · L6 non-tautology · L7 provenance integrity
        L8 layer-1 report supplied and passed
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

### 3.3 Synthesis schema and provenance (current)

`SYNTHESIS_VERSION = "ej-lesson-synthesis/2"` (superseding `/1`). The persisted
`SynthesisAttestation` carries:

| Field | Meaning |
|---|---|
| `synthesis_version` | `ej-lesson-synthesis/2` |
| `principle_text` | the delivered, worker-facing rule |
| `feature_codes` / `mechanism` | Stage-A grounding (provenance; never delivered) |
| `evidence_ref` | human-readable summary (not a checkable contract) |
| `validator_id` / `validator_passed` / `rationale` / `redactions` | Stage-C/D provenance |
| `task_id` | **structured** originating task (`attach_synthesis` requires it ∈ candidate `evidence_tasks`) |
| `rollout_id` | **structured** run/rollout identity (`attach_synthesis` requires it ∈ candidate evidence run ids when present) |

The two structured fields were added at `/2`: `evidence_ref` is free-text and is
not a contract, so task/rollout identity is carried as fields and bound to the
candidate by `attach_synthesis`. `_canonical_state` includes the whole
attestation, so the receipt binds the delivered principle.

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

**Layer 2 — deterministic structural validation** (`validate`)

Answers §8's nine questions plus L1–L8. It is **not a semantic judge**: it is a
same-author, deterministic predicate set. What it establishes is structural
independence from the generator — the signature accepts only
`(scrubbed_text, ev, report)`, and its executable body references no distiller,
prompt or `Principle` (proven by AST test). It does **not** establish
statistical/semantic independence, and this document does not claim it does.
`L8` asserts only that the Layer-1 scrub report was supplied **and passed**
(`report is not None and report.passed`); it is not a generator-vs-validator
distinctness proof. `L2` re-derives `derive_features(ev)` and requires a
non-empty feature set, so evidence with no observable mechanism fails here as
well as at Stage A.

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
| L2 diagnosis | `derive_features(ev)` is non-empty (re-derived in D, not trusted from A) | `PROVEN` |
| L3 transferability | a general-condition marker present | `SUPPORTED` (heuristic) |
| L4 leakage | no residual derived-identity term | `PROVEN` |
| L5 actionability | ≥6 words and ≤ `MAX_RULE_TOKENS` | `PROVEN` |
| L6 non-tautology | no `_TAUTOLOGY_MARKERS` | `SUPPORTED` (marker-based) |
| L7 provenance integrity | provenance held outside worker-facing text | `PROVEN` |
| L8 layer-1 report | `report is not None and report.passed` — Layer-1 output supplied and passed. **Not** a generator/validator distinctness proof. | `PROVEN` |

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

The prerequisite is fail-closed, so existing promotion tests that built
candidates with no synthesis failed and were updated to satisfy the strengthened
contract. Two were missed by the original change and repaired separately
(`test_experiment_lifecycle.py::TestEvidenceIdentityCount`, test-only). Verified
by diff for the original change: **only 3 assertion lines were removed**, each
replaced by a stronger equivalent:

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

1. **Stage B requires an injected distiller.** W5 wires a LOCAL-ONLY distiller
   (`lesson_distiller.make_local_distiller`) into `get_prompt_repairer()`, but
   the local model is not exercised by tests (a deterministic fake is used), so
   live principle quality is `NOT ESTABLISHED`. `PROVEN` — `abstract()` has no
   fallback; a missing or failed local distiller fails closed.
2. **The distiller prompt (`_B_SYSTEM`/`_B_USER`) is engineering scaffolding, not
   a validated prompt.** Its output quality is `NOT ESTABLISHED` until exercised
   against real failure evidence.
3. **L3/L6 are marker heuristics** — `SUPPORTED`, see §6.
4. **`reflection_loop`'s cloud-capable distiller is NOT used.** It passes no
   `task_id`, consumes task-specific content, and can call a cloud model, so it
   is excluded from the W5 path. Its prompt structure was not reused.
5. **No live end-to-end run has occurred**, by instruction. Every property above is
   established by unit/integration tests and source inspection, not by a
   production run.
6. **DNS / hostname resolution (`NOT ESTABLISHED`).** The allowlist accepts the
   `localhost` alias in addition to loopback IPs; validation checks the hostname
   *string*, and the socket resolves it. On a host with a tampered resolver this
   alias could point elsewhere. Literal `127.0.0.1`/`::1` are not subject to
   this; the default endpoint is a literal IP.
7. **`0.0.0.0` is accepted as a loopback-equivalent host (`GOVERNANCE GAP`).**
   It is a bind address, not a conventional outbound destination. It does not
   create an off-host escape, but it is retained as an open policy question and
   was not silently changed.
8. **Model-identity default mismatch (`GOVERNANCE GAP`).** See §11.3.

---

## 11. W5 — production integration (implemented)

The real learning path now invokes synthesis. No parallel pipeline was created;
the existing bridge → `process_failure` → `evaluate_and_promote_eligible` →
`evaluate_candidate` → `promote` chain is used, with synthesis inserted before
evaluation.

### 11.1 Structured failure evidence (`PROVEN`)

The bridge (`evaluation_bridge.submit_evaluation_failure`) builds a
`FailureEvidence` directly from the `EvaluationFailure` it already holds and
passes `evidence=<dict>` to `process_failure`, which persists it on the matching
`evidence_runs` entry. Stage A therefore consumes measured fields, never a
reconstruction from failure prose. Only observed fields are copied; the gold
patch and test patch are never present in `EvaluationFailure`. `FailureEvidence`
gained lossless `to_dict`/`from_dict`; `from_dict` fails closed on malformed
input and preserves the three-valued `evaluator_passed`.

### 11.2 Run vs candidate granularity (`PROVEN`)

Stage A is per-run; promotion is candidate-level. `lesson_synthesis.
synthesize_candidate` resolves this explicitly:

* it requires **every** identity-bearing run to carry structured evidence (a
  single missing record fails closed — `_synthesize_eligible` in
  `prompt_repairer`);
* it diagnoses **every** run and requires a **non-empty intersection of feature
  codes across all runs**; no shared mechanism ⇒ no principle. This is the guard
  that prevents one convenient run from masquerading as the whole evidence base;
* the principle is grounded in the shared mechanism only;
* the membrane lexicon is the **union** of every contributing run's task id and
  evaluator reason, so an identifier observed in any run is blocked;
* per-run provenance is preserved: the candidate keeps its full `evidence_runs`,
  and the attestation records `task_id`/`rollout_id` plus a rationale listing
  `shared_mechanism`, `runs=N`, and `distiller=<id>`.

### 11.3 Local-only distiller and transport (`PROVEN` for the mechanism)

`swarm_os/services/lesson_distiller.py` exposes one production constructor,
`make_local_distiller`, which refuses any non-`local` provider, any
non-loopback/non-http `base_url`, and any `base_url` carrying URL userinfo
(`LocalOnlyError`), before any call.

**Endpoint is the Smart Model Proxy, not necessarily local inference.** The
default `http://127.0.0.1:8080` is `model_router.py` ("Smart Model Proxy"),
which forwards to `:8079`. In the RUNPOD topology (`SWARM_ROUTER_PINNED=1`)
`:8079` is an ssh tunnel to a pod GPU, so a loopback `:8080` request would be a
**network call**. The distiller therefore **refuses to construct when
`SWARM_ROUTER_PINNED=1`** (fail closed). In the default LOCAL topology `:8079`
is local `llama.exe`, so the forward stays on-host.

**Transport containment.** `_http_openai_complete` uses an explicit
`urllib.request.build_opener(ProxyHandler({}), _RejectRedirects())`:
`ProxyHandler({})` installs **no proxy dispatch** (CPython `add_handler` skips
`proxy_open`), so `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` and lowercase forms
cannot redirect the request; `_RejectRedirects` refuses every
301/302/303/307/308 (off-host **and** loopback), so a local endpoint cannot
bounce the request elsewhere.

There is no cloud fallback, no provider substitution, and no external credential
lookup; the transport uses the fixed local `Bearer llama` token.
`get_prompt_repairer()` wires this identity; if construction fails, synthesis
stays disabled (fail closed). `make_fake_distiller` is a DEVELOPMENT FAKE
(`provider="fake"`); its identity appears in the attestation rationale.

**`GOVERNANCE GAP` — model identity.** `SWARM_DISTILLER_MODEL` defaults to
`qwen3.5-4b`, but the LOCAL backend serves alias `robs4b`
(`docs/INFERENCE_TOPOLOGY.md` §2; the router `/v1/models` spoofs
`robs4b`/`qwen3.5-0.8b`). llama.cpp serves its single loaded model regardless of
the request `model` field, so the call still runs locally, but the **declared**
identity does not match the served artifact. The experimental distiller model
identity must be confirmed by the operator and set explicitly before any
learning event (`REQUIRES AUTHORIZATION`).

### 11.4 Failure modes (`PROVEN`)

Every stage fails closed: missing/failed distiller, missing structured evidence,
undiagnosable run, no shared mechanism, scrub rejection, validator rejection,
malformed attestation, or provenance mismatch all yield no validated synthesis,
and `promote()` then rejects `no_validated_synthesis`. There is no canned
fallback lesson.

### 11.5 Boundaries unchanged

F0/F1/F2 authority, `MIN_EVIDENCE_RUNS=3`, `MIN_EVIDENCE_TASKS=2`, the receipt
and provenance protections (Fixes 3–5), and the evaluation state machine are
unchanged. No `SWARM_RECEIPT_KEY`, no Qdrant contact, no ACTIVE lesson, no
learning event.

### 11.6 Receipt / provenance integrity (W5 additions)

* **Immutable evaluator snapshot.** `evaluate_candidate` deep-copies the
  candidate and hands the copy to the evaluator and the eval-context artifact;
  the receipt binds the snapshot's hash. The evaluator can no longer observe a
  mutating live object, and a concurrent mutation of the live candidate is
  detected at promotion (`forged_or_mutated_evaluation`). `PROVEN`.
* **Run provenance when `rollout_id` is empty.** `_synthesize_eligible` binds
  the attestation's `rollout_id` to the candidate's evidence identity
  (`rollout_id` or `run_id`) when the representative evidence has no
  `rollout_id`, so `attach_synthesis` enforces run binding for legacy
  run_id-only records instead of skipping it. `PROVEN`.

### 11.7 Tests

* `tests/test_lesson_distiller.py` — local-only boundary, fake vs real, and
  **adversarial transport containment** against real loopback HTTP servers
  (proxy-env escape for HTTP/HTTPS/ALL, off-host and loopback redirect
  rejection, exactly-one-request, normal path, no-fallback, router-pin refusal,
  userinfo refusal).
* `tests/test_w5_integration.py` — candidate synthesis, tick integration,
  membrane against W5 inputs, provenance crossing, bridge adapter,
  empty-`rollout_id` run binding, and evaluator-snapshot isolation.