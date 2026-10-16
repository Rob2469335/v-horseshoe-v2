# Experiment J / F2 — Admission Counting Rule

**Status:** ADOPTED by the experiment owner, 2026-10-16 (owner-approved decision package).
**Scope:** the definition and measurement of the F2 target of **≥ 300 admitted pairs**, and the
cross-chain crediting rule. It changes no frozen scientific criterion, no evaluator semantics, and
no admission predicate; it defines how an admitted pair is *counted* toward the target.

---

## 1. What is counted

**An admitted pair** is one task that holds a `base_fail_gold_pass` scientific record in a
**registered** ledger chain. In the repository's own record contract
(`qwen_train/f2_s8_factory.py`) that is:

* `outcome == "base_fail_gold_pass"` (the value of `ADMITTED_OUTCOME`) **and**
* `outcome_class == "scientific"`,
* counted on the **first** scientific record for that task (the append-only ledger's canonical
  `admitted()` rule — a later attempt for the same task never adds a second credit).

**The target of ≥ 300 admitted pairs is measured as the UNION of admitted pairs across all
registered chains, each task counted exactly once.** A task admitted in more than one chain is
counted once; the fact that it appeared in more than one chain is reported, never added.

### 1.1 Registered chains

A chain is *registered* only when it is named here, by the experiment owner, together with its
SHA-256 at registration. As of 2026-10-16:

| # | Role | Path | SHA-256 at registration |
|---|---|---|---|
| 1 | Operative chain (the code-bound canonical count) | `C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger.jsonl` | `5fb0aa8eda37f220ea2fce5ca96688bb4a7edf94576b73b32b1678c1a1971430` |
| 2 | Corrective chain (one sanctioned correction) | `C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger_corrected.jsonl` | `e030c05a21fd22baf840f80a9cbd4645dd6c1aa8492350281f563a835f96d5ae` |

A chain that is not named above **does not credit**. Enforcement: the chain-name
allowlist and the SHA-256 pins above **are** enforced in code —
`qwen_train/f2_s8_batch.py` pins them as `REGISTERED_CHAINS` (exact ledger filename →
the SHA-256 above, in this order), and `reconcile_admissions_across_chains` refuses a
reconciliation whose chains are not that registered set supplied in that order, and
refuses a chain that does not actually contain its registered prefix digest. An
*unbound* cross-chain override (a second-chain admission for a task that already holds
a terminal non-admitted record in an earlier chain, carrying no `correction_of_*`
fields) is likewise refused. `unregistered_admitted` continues to report tasks
**absent from the operative registration**. No enforcement decision is read from this
document at runtime.

## 2. Cross-chain credit requires a proven binding

A corrective record may be credited only when it binds to the chain it corrects. All four
conditions must hold; any failure aborts the whole reconciliation (fail closed) rather than
crediting on trust:

1. `correction_of_chain` names a chain **registered above** (and part of the reconciliation);
2. `correction_of_record_digest` names a record that **exists** in that chain;
3. that record is for the **same `task_id`** as the corrective record — a correction may only
   correct the same task;
4. the corrected record occurs **at or before** the verified prefix boundary identified by
   `correction_of_chain_sha256` — i.e. it was already in the chain when the correction was written.
   `correction_of_chain_sha256` is the SHA-256 of the corrected chain's bytes **as they stood at
   correction time** (the first N non-empty rows, line endings included); later appends grow the
   chain, so the recorded value is a *historical line-prefix* digest of the current file.

Enforced by `qwen_train/f2_s8_batch.py::reconcile_admissions_across_chains` +
`_require_correction_binding` (read-only; no ledger is mutated, no admission predicate changes).

## 3. Prospective evidence requirement (Phase 2 onward)

From Phase 2 onward, a pair counts **only if BOTH** hold:

1. a valid hash-chained ledger record per §1; **and**
2. a persisted, **canonically verified** per-task `EvidenceRecord` meeting the prospective evidence
   contract (`docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md` §1.6 / `f2_evidence`): the retained
   evaluator report **and a real run log** with their digests and sizes, `environment_identity`,
   the base/gold `execution_state_digest`, the evaluator identity + implementation digest, and the
   repository revision + registration digest — all bound into the ledger record.

Placeholder logs (e.g. a 12-byte `"base run log"`) and placeholder timestamps (e.g. `"t0"`) are
**refused**, as are missing evidence, inconsistent digests, and mismatched ledger bindings.

## 4. Historical grandfathering (legacy pilot evidence contract)

The four historical pilot pairs below are **grandfathered** under a documented legacy contract —
the retained **JUnit XML**, the retained evaluator `report.json`, and the session governance
artifact `pilot_governance_env_used.json` — because that is what the historical runs actually
retained:

| task | chain |
|---|---|
| `openai__openai-agents-python-1633` | operative |
| `openai__openai-agents-python-1636` | operative |
| `python__mypy-19767` | operative |
| `HypothesisWorks__hypothesis-4532` | corrective (attempt 2) |

**This is a historical governance exception, not a finding that they satisfy §3.** Their evidence
passes pointer integrity, report-digest integrity, JUnit→report reproducibility, the
base-FAIL/gold-PASS relationship, and evaluator-identity binding, but **not** the canonical
verifier: the per-task `EvidenceRecord` was never persisted, and the ledger row carries no
`evidence_bundle`. (Note the distinction: the *session*-level `environment_identity` and the
`base_state_digest`/`gold_state_digest` *were* recorded in `pilot_governance_env_used.json`; what
is missing is the *per-task* binding.)
Nothing here rewrites a ledger row or an evidence artifact, and no missing historical field may be
back-filled.

## 5. Counting today

| Figure | Value | Basis |
|---|---|---|
| Operative-chain admissions | **3** | `openai-1633`, `openai-1636`, `mypy-19767` |
| Credited historical admissions (union) | **4** | + `hypothesis-4532` (corrective, §2 binding proven) |
| Population-census admissions | **0** | no registration row carries attached S8 evidence |
| **Additional admitted pairs required** | **296** | 300 − 4 |

The operative count and the credited count are **different questions** and are always reported
separately; neither is the census count, and no count of candidates, attempts, or compatible
classifications is an admission.
