# F2-S8 — Infrastructure-row evidence binding: decision record (DESIGN ONLY)

**Status:** PROPOSAL — **no code applied**. Requires an owner decision.
**Date:** 2026-10-18
**Scope:** how a post-batch audit can be anchored to a hash-chained ledger row
when that row records an infrastructure outcome. This document does **not**
amend F0 (`docs/EXPERIMENT_J.md`), the F2 execution contract, the admission
counting rule, or any historical row.

---

## 1. The observed gap (`PROVEN IN CURRENT REVISION`)

A row that ends as an infrastructure outcome records no evidence identity. The
inconclusive branch of `qwen_train/f2_s8_compose.py::_result_from_bundles`
returns an `ExecutionResult` with the default empty fields:

```python
if inconclusive:
    return ExecutionResult(
        task_id=spec.task_id, outcome="infrastructure_failure", attempt=int(attempt),
        attempt_id=f"{spec.task_id}#{attempt}",
        reason=(...),
    )
```

`qwen_train/f2_s8_factory.py::run_once` then appends a row whose `evidence` is
`None` (because `evidence_name` is empty), and whose `evidence_bundle` is
**not written at all** (that block is guarded by `if admitted:`). The same is
true of the driver's own infrastructure path in
`qwen_train/f2_s8_pilot.py::run_pilot_task`, which appends
`{"outcome": "infrastructure_failure", ...}` with no artifact reference.

Consequences:

- The post-batch audit (D4) can hash-check a declared `base_junit_sha256` /
  `gold_junit_sha256` against the retained file, but the ledger row does not
  carry those hashes, so the audit cannot bind its finding to the chain.
- A row that produced *some* evidence before a later side crashed still stores
  nothing about the evidence it did produce. A report can therefore disagree
  with the chain without the chain showing it.
- The presented batch report is the only place the hashes exist, and D4 must
  trust the report's `manifest_digest`/`run_id` binding to know which bytes it
  should inspect.

This is a **detection gap**, not a credit error: an infrastructure row is not
admitted and does not count toward 300 under
`docs/EXPERIMENT_J_F2_ADMISSION_COUNTING_RULE.md`. But the gap is exactly what
lets a compromised or mistaken report move evidence without the chain
contradicting it.

## 2. Constraints that any option must respect

1. The ledger is append-only. Historical rows cannot be rewritten or
   back-filled (`docs/EXPERIMENT_J_F2_ADMISSION_COUNTING_RULE.md` §4).
2. The frozen artifact contract does **not** make raw JUnit a required trusted
   artifact role, and this decision must not silently promote it.
3. A row's `record_digest` is the hash of its canonical bytes. Any new field
   changes every future row's digest but must not change any existing row.
4. Nothing here may create credit. The admission predicate is unchanged.
5. No option may make a missing field read as “no errors”.

## 3. Option A — bind JUnit/report hashes into the ledger row

**Shape.** Extend the *infrastructure* row (and, for symmetry, every row) with
an optional, explicitly versioned `retained_evidence` object:

```json
"retained_evidence": {
  "schema": "f2-retained-evidence/v1",
  "base_junit": {"sha256": "...", "size_bytes": 1234},
  "gold_junit": {"sha256": "...", "size_bytes": 1234},
  "base_report": {"sha256": "...", "size_bytes": 5678},
  "gold_report": {"sha256": "...", "size_bytes": 5678}
}
```

Present only when the side actually produced the artifact; a crashed side
carries `null` (never a zero hash).

**Advantages**

- The audit's finding is anchored to the chain: given the row's
  `record_digest`, a verifier can reconstruct which bytes were claimed.
- A report that swaps evidence must also contradict a row that is already
  hash-chained, so the detection no longer depends solely on the report.
- It also closes the same gap for a `gold_fail` row (scientific, but currently
  evidence-bearing only on the admitted path).

**Costs and risks**

- It **changes what a ledger row stores**. Whether that is permitted is an
  authority question, not an implementation preference. The prospective
  evidence contract in the counting rule (§3) describes an `EvidenceRecord`
  bound into the record; it does not describe a schema for infrastructure
  rows, and the historical section (§4) explicitly forbids back-filling. The
  safest reading is that a **new, additive, versioned** field on *future* rows
  is a schema change requiring owner authorization; it is **not** a change to
  the scientific predicate.
- Because the ledger is append-only, old rows stay as they are. A verifier
  must therefore treat “field absent” as `NOT ESTABLISHED`, never as “no
  evidence”.
- It increases the digest surface: every row's canonical bytes change, so any
  tool that pins a row digest must be reviewed.
- It does not by itself make the evidence tamper-proof; a writer who can
  rewrite the staging root and the report can also compute new hashes. The
  anchor raises the cost (the chain is append-only) but is not an independent
  trust root.

## 4. Option B — sidecar evidence index outside the chain

**Shape.** Keep ledger rows byte-identical. Write a separate, owner-readable
index that binds a row's `record_digest` to the retained artifact hashes:

```json
{
  "schema": "f2-s8-evidence-index/v1",
  "chain": {"path": "...", "prefix_digest": "..."},
  "entries": [
    {"record_digest": "...", "task_id": "...", "attempt": 1,
     "base_junit_sha256": "...", "gold_junit_sha256": "..."}
  ]
}
```

The index is written by the same post-batch step that writes
`post_batch_audit.json`; D4 and preflight can require both.

**Advantages**

- No frozen-contract change and no ledger-row change; all historical rows and
  digests remain exactly as they are.
- Immediately implementable with the code already present in this phase.
- It can be regenerated from retained JUnit + ledger, so it is reviewable.

**Costs and risks**

- The index is **not** inside the hash chain. A writer who can rewrite the
  report and the index can make them agree; the chain will not contradict
  them. This is strictly weaker than Option A.
- It adds a second artifact that must be retained, versioned and bound to a
  chain prefix; a stale index is a new failure mode (D4 already refuses a
  mismatched `run_id`/manifest digest, and the index would need the same
  treatment).
- It does not solve the original gap for a verifier that has only the chain.

## 5. Comparison

| Dimension | A — in-row | B — sidecar |
|---|---|---|
| Ledger row changes | yes (future rows) | no |
| Frozen-contract risk | **needs owner/authority review** | none |
| Historical rows | untouched (cannot back-fill) | untouched |
| Audit anchored to chain | yes | no (report + sidecar) |
| Tamper resistance | higher: append-only chain | lower: mutable sidecar |
| Implementable now | only after authorization | yes |
| Credit predicate changed | no | no |

## 6. Recommendation

1. **Do not apply Option A in this session.** It changes what a ledger row
   stores and therefore crosses the owner’s standing instruction that the
   agent does not alter frozen rules. It is `REQUIRES AUTHORIZATION`.
2. **D4 already provides the feasible compensating control** within the
   current contract: it fails closed on a missing/mismatched/malformed/
   undeclared artifact and on a report whose binding does not match the
   manifest, staging root, registration and run id. That is detection, not
   chain anchoring; the residual risk is stated in
   `qwen_train/f2_s8_post_batch_audit.py` and must be repeated in the Phase 2
   plan.
3. If the owner wants chain anchoring, authorize Option A as a **new,
   additive, versioned field on future rows only**, with an explicit
   `NOT ESTABLISHED` rule for historical rows, and a migration decision that
   never rewrites history. Option B can be adopted in the meantime without
   touching the chain.

## 7. Decision requested

- **A:** authorize a versioned `retained_evidence` field on future ledger rows
  (state whether this is a frozen-contract change and which document records
  the authorization).
- **B:** authorize the sidecar index only, accepting that it is not
  chain-anchored.
- **Neither:** keep D4 as the sole compensating control and record the
  residual risk explicitly in the Phase 2 plan.

No code in this repository has been changed to implement either option.
