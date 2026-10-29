# F2-S8 — Authorization request: the S8 runner/evidence hardening, and its publication

**Status:** AUTHORIZATION REQUEST — grants nothing, authorizes nothing, commits nothing.
**Date:** 2026-10-16
**Requested of:** Rob (human operator)
**Repository HEAD at time of writing:** `c2bfa85655b8ced357500a6427c27c657c7a7c0e`
(branch `agent/q9-classification-sota`; `origin/…` == HEAD (0 ahead / 0 behind);
PR #33 OPEN, not draft, unmerged)
**Related:** `docs/EXPERIMENT_J_F2_S8_EXECUTION_REVISION_AND_CHECKOUT_PLAN.md` §1
(the single owner decision) and §5 (entry conditions).

---

## 1. Why this request exists, and why it is not inferred

Three questions were separated by inspection, not assumption.

1. **`F2-IMPL-AUTH-033` does not name the files this change set touches.** Its file
   boundary (`docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md:2489-2496`)
   is exactly: `qwen_train/f2_authorization.py`, `qwen_train/f2_authz_ledger.py` (new),
   `qwen_train/f2_arm_orchestrator.py`, `qwen_train/f2_arm_worker.py`,
   `qwen_train/f2_execution_adapter.py`, `qwen_train/f2_registration.py`,
   `tests/conftest.py`, `tests/f2_authz_testkit.py` (new),
   `tests/test_f2_authz_binding_and_replay.py` (new), "the authorization-related test
   files updated to the new contract", and that document. **PROVEN:** none of
   `f2_s8_pilot_live.py`, `f2_s8_compose.py`, `f2_reconcile.py`, `f2_preflight.py`,
   `f2_evidence.py` is in it.

2. **No other authorization record names them either.** Every `**File boundary.**`
   section in that document (37 entries, `:397`–`:2489`) was enumerated.
   `f2_s8_pilot_live.py`, `f2_s8_compose.py` and `f2_reconcile.py` appear in **no**
   file boundary. `f2_preflight.py` appears only as `F2-IMPL-AUTH-023`'s *creation*
   boundary (`:1137`) and as an explicit **non-authorization** in `F2-IMPL-AUTH-032`
   ("Editing `f2_preflight.py` is `REQUIRES AUTHORIZATION` under `F2-IMPL-AUTH-028`").
   `F2-IMPL-AUTH-034` authorizes one specific `f2_preflight.py` correction — the
   `population.admission` literal — and that correction is **already committed at
   HEAD**, so it is not the change proposed here. `f2_evidence.py` appears as an
   explicit non-authorization in `F2-IMPL-AUTH-001` (`:425`).

3. **`GOVERNANCE GAP` — the durable record is already inconsistent with practice.**
   The commits that implemented the S8 runner's authorization behaviour touched files
   outside `F2-IMPL-AUTH-033`'s declared boundary (**PROVEN**, `git show --stat`):

   | Commit | Files touched outside the `F2-IMPL-AUTH-033` boundary |
   |---|---|
   | `bee8d86a FIX(f2): require a signed S8-001 authorization before any S8 guest work` | `qwen_train/f2_s8_pilot_live.py` (+25), `tests/test_f2_s8_pilot_live_authorization.py` (+99) |
   | `2d5c0640 FIX(f2): consume the S8-001 grant single-use…` | `qwen_train/f2_s8_pilot_live.py` (+15), `tests/test_f2_s8_pilot_live_authorization.py` (+17) |
   | `fc88c406 FIX(s8): close the audit residuals…` | `qwen_train/f2_s8_pilot_live.py` (+10/−2), `qwen_train/f2_s8_compose.py` (+9) |

   That is a record defect, not a licence. Whether or not an earlier, broader owner
   instruction covered those commits, the durable authorization does **not** name the
   file, so landing further changes on the same files under the same reasoning would
   repeat the gap instead of closing it.

Per `AGENTS.md` §3.3/§3.7, authorization is **not** inferred from PR #33, from
precedent, or from the change being safer. Hence this request.

---

## 2. What is requested

**One numbered operator record** (the next free `F2-IMPL-AUTH-0NN`, author: Rob) that
authorizes exactly:

1. **Modification** of the files in §3(A) — and, at the owner's option, §3(B) — for
   the stated behaviour and nothing else; and
2. **Feature-branch publication**: one commit per logical change on
   `agent/q9-classification-sota`, type-prefixed (`FIX(s8):` / `FIX(f2):`), **no merge**
   of PR #33, and no S8-001 grant bound to a revision until that commit exists and its
   SHA has been recorded.

If the owner prefers to narrow it, the smallest coherent subset is **§3(A) alone**: the
two material files plus their tests. §3(B) is recommended but separable.

It does not request, and must not be read as granting, anything in §4.

---

## 3. The change set

`git diff --numstat` against HEAD (`c2bfa856`), verified this session.

### (A) Required for a safe first S8-001-gated run

| File | Δ | Behaviour | Why it is required | Tests |
|---|---|---|---|---|
| `qwen_train/f2_s8_pilot_live.py` | +62/−31 | `main()` validates **before** it consumes: parse manifest → `build_specs` → unknown-`--only` refusal → zero-runnable refusal → `load_governance_from_env()` **before** `verify_and_consume`. `build_specs` skips non-object manifest values. Refusals state "No grant was consumed". Consumption itself is unchanged — still atomic, single-use, before any guest work — and the consumed binding is unchanged. | S8-001 is single-use, keyed on the record digest. In the committed order a manifest / `--only` / zero-runnable / readiness failure **consumes the grant and then refuses** → a new owner signature. This is the first-ever gated run (new gate, new trust root, first `--only` use), so readiness-failure probability is elevated; `F2-IMPL-AUTH-033` §2 itself states the governing principle ("a readiness failure never burns the grant"). | `tests/test_f2_s8_runner_validation_order.py` (7, new); `tests/test_f2_s8_pilot_live_authorization.py` (6, incl. new: replay at the consumer boundary; a readiness failure does not consume) |
| `qwen_train/f2_s8_compose.py` | +19/−5 | `_refs_ok` passes the real artifact-role **key** as `ref.role` and `allowed_roles=tuple(REQUIRED_EVIDENCE_ROLES)`, instead of pinning `ROLE_TEST_OUTPUT` on both sides. | `TrustedArtifactStore.verify_ref` performs **no stored-role comparison** — its role test is only `ref.role in allowed_roles` (`f2_governance.py:438-470`). The committed `role="test_output"`, `allowed_roles=("test_output",)` makes that test **vacuously true for every ref**, on the very evidence path the run exists to exercise. The allowlist is the closed `REQUIRED_EVIDENCE_ROLES`; a key outside it fails closed. | `tests/test_f2_preflight_evidence_sources.py::TestD3RefsRoleHardening` (3); `tests/test_f2_sota_decisions.py::test_s8_compose_does_not_pass_the_reference_digest_as_gold_state` |

### (B) Supporting: evidence-verification and reporting corrections

| File | Δ | Behaviour | Why | Tests |
|---|---|---|---|---|
| `qwen_train/f2_preflight.py` | +202/−15 | `_evidence_findings` evaluates **supplied** sources through the authoritative paths — `f2_evidence.verify_task_evidence` for `evidence.s8_base_gold`, `f2_s8_batch.reconcile_admissions_across_chains` for `population.admission` — with new `--admission-ledger` / `--registered-tasks` / `--s8-evidence`. **With nothing supplied the findings are the same fail-closed defaults.** | Gate G cannot verify supplied S8 evidence before the run while the gates are literals; a digest or a ledger count must never establish provenance. | `tests/test_f2_preflight_evidence_sources.py::TestD1PreflightEvidenceSources` (10) |
| `qwen_train/f2_reconcile.py` | +90/−4 | `_admission_accounting` reports the counting rule's own figures (operative / corrective / credited union / remaining) **separately** from the registration census, and reports `None` **with a reason** (never `0`) when unmeasured. | The capacity report hard-coded `admitted = 0` and `shortfall_to_300 = 300`, conflating "not measured" with "zero admitted"; the owner's 2026-10-15 option-B counting rule is the measure. | `tests/test_f2_preflight_evidence_sources.py::TestD2CapacityAdmissionAccounting` (3) |
| `qwen_train/f2_evidence.py` | +10/−3 | `verify_task_evidence` **docstring only**: `expected_gold_state_digest` is an execution-**state** digest (an operator assertion), and the Q8 `reference_digest` (a SHA-256 over `repo@commit`) is a different semantic domain and MUST NOT be passed. | Prevents asserting a provenance relationship the Q8 digest does not establish. No behaviour changes. | `tests/test_f2_sota_decisions.py::TestDecisionBGoldStateDigest` (5) |
| `tests/test_f2_s8_pilot_live_authorization.py` | +79/−6 | Tests for (A). `_provision_governance` makes a request READY so the authorization gate is actually reached; the replay case now runs at the `verify_and_consume` consumer boundary. | (A) cannot be validated without it. | — |
| `tests/test_f2_s8_runner_validation_order.py` | 156 lines, new | Pins the order and each non-consuming refusal. Needs **no** grant, key or trust root: with no trust root `verify_and_consume` fails closed, so a validation message proves validation ran first. | — | 7 |
| `tests/test_f2_preflight_evidence_sources.py` | 193 lines, new | Pins D1/D2/D3. | — | 16 |
| `tests/test_f2_sota_decisions.py` | 253 lines, new | Pins decisions A–D (evidence format; gold-state digest domain; Ed25519+pinned-trust-root grant primitive; the **broad** untracked-inclusive dirty-tree predicate). | — | 11 |

**Provenance (§3.5 classification) of the three untracked test files:** PROVEN as
test-only artifacts authored in this workstream, imported by no production code,
required to pin §3(A)/(B). They are **not yet authorized for inclusion**; inclusion
follows only from the record requested here.

### Not part of this request

`docs/EXPERIMENT_J_F2_S8_IPV6_GATE_DECISION_RECORD.md` (+193/−1) is **excluded**. It is
a protected governance document and needs its own authorization
(`docs/EXPERIMENT_J_F2_S8_ACL_RECONCILIATION.md` §9.1). Its SHA-256 remains
`1a8501d6f0ba2036f8c8cf4045a8a80ca21b54c8b684d47e6e49426a972b1748` and must not change
under this request.

---

## 4. Explicitly NOT requested

- **No execution of any kind**: no Stage-1 build, no clone, no dependency install, no
  VM/guest/network action, no pilot task, no Q10/Q12/Q13, no T/X arm, no model
  inference.
- No trust root, key, grant, signature, attestation or credential; no change to host
  security or networking.
- No change to F0/F1, `docs/EXPERIMENT_J.md`, the frozen statistical design, the
  endpoint, `k`, α, T/X/C0 semantics, or any admission criterion.
- No change to `qwen_train/f2_authorization.py`, `qwen_train/f2_authz_ledger.py`, or the
  ledger's deliberately non-environment-selectable root.
- No merge of PR #33; no push beyond the branch's own feature-branch publication.
- No relaxing of any gate; no payload, key or secret value is printed, committed or
  placed in the repository.

---

## 5. Evidence that the candidate change set is validated (this session)

- `git diff --check` → **clean** (rc 0).
- Focused F2 suite `tests/ -k f2` (includes the three new test files):
  **2763 passed, 4 skipped, 3149 deselected, 0 failed** — 17 m 34 s.
- The four directly-affected test files alone: **40 passed**.
- **No production store was touched.** SHA-256 snapshots taken before and after the run
  over `data/prompt_repairer_candidates.json`, `data/prompt_repairer_{snapshots,audit,journal,rollouts}`,
  `data/evolution/fitness.jsonl`, `data/f2_authz_ledger`, and
  `C:\Users\rober\F2_EVIDENCE\s8\trusted_store` were **byte-identical**; the two
  registered chains read `5FB0AA8E…` / `E030C05A…` throughout. Zero mutations observed
  between the two snapshots. `data/f2_authz_ledger` remains **ABSENT** — the S8-001 gate
  has still never consumed a grant on this host.
- **No fabricated authorization.** No test creates a real trust root, grant, signature,
  successful protected run or bypassed gate: the runner tests exercise either
  **structural order** or the **authorization refusal** (no trust root ⇒ fail closed),
  and the evidence tests use ephemeral `tmp_path` stores.
- **No secret exposure.** The run log contains **zero** occurrences of
  `SWARM_RECEIPT_KEY` and **zero** failure/error lines.

**Scope honesty.** This proves the focused F2 surface passes on the current working tree
and that the run mutates no production store. It does **not** prove a real build, a real
signing ceremony, real isolation, or that F2 is ready. A passing synthetic test is not
evidence for any of those.

---

## 6. The single owner decision this unblocks

The request does not replace the decision in
`docs/EXPERIMENT_J_F2_S8_EXECUTION_REVISION_AND_CHECKOUT_PLAN.md` §1.6; it is the
authorization instrument that option **(a)** of that decision requires. The owner
decides **only**:

> **(a)** grant the record above, land the S8 hardening as revision(s) on
> `agent/q9-classification-sota` (PR #33 stays OPEN and unmerged), re-establish the
> focused-suite evidence **for the resulting commit**, then bind the S8-001 grant to
> that SHA; **or**
> **(b)** execute `c2bfa856` as-is, accepting that a readiness/manifest/`--only`/env
> failure consumes the single-use grant and requires a new owner signature, that the
> artifact-role check stays vacuously true, and that the preflight evidence gates stay
> literal.

Everything else in §5 of the execution plan (Gates A–G) is unchanged.

---

*This document grants no authorization, marks nothing approved, and creates nothing
outside itself. It changes no code, no test, no authority document and no protected
document.*
