# F2 S8 ledger — how to record a defect-invalidated result (SOTA-grounded proposal)

**Status: PROPOSED — NOT IMPLEMENTED — REQUIRES AUTHORIZATION.**
The change alters a *frozen scientific control* (the anti-re-run rule in
`qwen_train/f2_s8_factory.py`), so it is an authorization boundary, not an agent decision.

## 1. The exact problem

`AppendOnlyLedger.append` refuses a **scientific** row for a task that
`terminal_scientific()` already finds:

```python
def terminal_scientific(self, task_id):
    for record in self.attempts(task_id):
        if record.get("outcome_class") == "scientific":
            return record
    return None

# in append():
if body.get("outcome_class") == "scientific" and self.terminal_scientific(task_id):
    raise LedgerError(f"task {task_id!r} already has a terminal scientific record; "
                      "append-only history may not be rewritten")
```

Attempt #1 recorded `HypothesisWorks__hypothesis-4532` as `gold_fail` (seq 0,
`record_digest 725ee071…`) — a result **proven** in Session 34 to be an artifact of a harness
defect, not a measurement. Run #3 produced the *correct* pair, which the guard refuses to
record. The guard is right to exist; it currently cannot express "this recorded outcome was
invalidated by a demonstrated defect".

## 2. Primary sources (read for this proposal)

| source | statement | what it fixes here |
|---|---|---|
| **W3C PROV-O** (W3C Recommendation, 30 Apr 2013) — `prov:Invalidation`, `prov:wasInvalidatedBy`, `prov:invalidatedAtTime`, `prov:invalidated`, `prov:qualifiedInvalidation` | invalidation is a **first-class, additive provenance event**; an entity is *invalidated by an activity*, never deleted | gives the record its shape: `invalidates → <digest>` + `invalidatedAtTime` + `wasInvalidatedBy <agent>` |
| **21 CFR 11.10** (FDA, eCFR) — (e) | *"secure, computer-generated, time-stamped audit trails … **Record changes shall not obscure previously recorded information**"* | the original row stays byte-identical; the correction is appended |
| 21 CFR 11.10 (a) | *"the ability to **discern invalid or altered records**"* | a first-class invalid marker is *required*, and a reader must be able to report it |
| 21 CFR 11.10 (g) | *"**authority checks** to ensure that only authorized individuals can … alter a record"* | the invalidation must name an authorized party — reusing the repo's existing `SeparationOfDuties` / AUTH-024 §8 roles |
| **RFC 6962** (IETF, Certificate Transparency) §1/§2.1.2/§7.1 | logs are *"publicly auditable, **append-only**"*; append-only-ness is **provable** (consistency proofs: a later version *"is a superset of any particular previous version"*); misissue is handled by *"existing business mechanisms"* **outside** the log — the log is never edited | confirms: do **not** touch the chain; add a record; keep `verify()` as the consistency proof |
| **Center for Open Science — Registered Reports** | review/registration happens **prior to observing outcomes**; in-principle acceptance is *"not revoked based on the outcomes, but only on failings of quality assurance, following through on the registered protocol, or unresolvable problems"* | the anti-p-hacking rule: an invalidation must be **pre-registered, one-shot, quality-assurance-grounded** — never outcome-driven |

## 3. Proposed design (the smallest coherent change)

Add a **second record kind** to the same chain. Existing rows carry no `record_kind`, so they
read as `"outcome"` — attempt #1's bytes and their digests are untouched.

**(a) An invalidation record**

```json
{
  "record_kind": "invalidation",
  "task_id": "HypothesisWorks__hypothesis-4532",
  "invalidates": "725ee0714095a83d6bc8ba09cd02d707aaed2ab6b881da8b60f175f85ac5cc36",
  "defect": {"class": "harness_per_side_environment", "evidence_digest": "<sha256>",
             "summary": "gold ran under the base venv; PEP 660 editable install bound it to the base tree"},
  "authorized_by": "<party that is NOT the run operator>",
  "operator": "<party that ran the invalidated attempt>",
  "ts": "…"
}
```

**(b) `terminal_scientific` excludes invalidated rows** — the *only* semantic change

```python
def invalidated_digests(self) -> set[str]:
    return {str(r["invalidates"]) for r in self.records()
            if r.get("record_kind") == "invalidation"}

def terminal_scientific(self, task_id):
    dead = self.invalidated_digests()
    for record in self.attempts(task_id):
        if (record.get("outcome_class") == "scientific"
                and str(record.get("record_digest")) not in dead):
            return record
    return None
```

**(c) Guard rails on the invalidation itself (the anti-p-hacking core)**

An invalidation is refused unless **all** hold:
1. it names an existing **scientific** record *of that task*, by `record_digest`;
2. that record is **not already invalidated** (a record is invalidated at most once);
3. the task has **at most one invalidation in total** (no re-rolling a task until it reports
   what you want);
4. it carries **defect evidence** (`defect.evidence_digest`) — a *quality-assurance* failure,
   not disappointment with the result (Registered Reports: outcomes may not drive the decision);
5. it names an **`authorized_by` distinct from the `operator`** — the authority check
   (21 CFR 11.10(g)), reusing `f2_governance.verify_separation_of_duties` and AUTH-024 §8's
   role table (run authority / operator / independent regrade);
6. **ordering is the pre-registration**: the invalidation must appear in the chain *before* the
   corrected scientific row, which the chain already proves — so a corrected result can never
   be selected after the fact.

`verify()` is unchanged: invalidations are chained exactly like outcomes, so RFC 6962's
"provable superset" property continues to hold, and a reader can list per task
`[outcomes…, invalidations…]` — satisfying 21 CFR 11.10(a).

## 4. The evidence for THIS invalidation is already assembled

- invalidated row: seq 0, `record_digest 725ee0714095a83d6bc8ba09cd02d707aaed2ab6b881da8b60f175f85ac5cc36`
- defect: `f2_s8_pilot_live.per_task_env` returned **one** `path_prefix` for both sides, so the
  gold command ran under the **base** venv; an editable install binds a project to one source
  tree (**PEP 660**), so the gold patch could not take effect.
- evidence: the two attempt-#1 reports are **byte-identical** (316,631 B each — the fingerprint);
  the in-guest probe (*gold+gold venv → node PASSES; gold+base venv → node FAILS*); the fix's
  diff and its regression tests.
- the corrected pair is now **verified correct**: gold report 316,547 B ≠ base 316,631 B.

## 5. Alternatives considered

| option | rule change? | verdict |
|---|---|---|
| **(A) fresh ledger chain for the corrected run**, attempt #1 preserved verbatim | **none** | **available now**; zero scientific-control change; the only cost is that the two chains must be read together |
| (B) the invalidation record above (this proposal) | yes — scientific | the SOTA shape; needs authorization; strictly better long-term auditability |
| (C) edit/rewrite the ledger row | yes — and it destroys the invariant | **rejected**: violates RFC 6962's append-only property and 21 CFR 11.10(e) |
| (D) leave hypothesis refused | none | permitted; the task simply has no recorded corrected pair |

## 6. What is NOT proposed

No change to: the frozen design, evaluator identity, registration, task scope, `tests/conftest.py`,
Q10/Q12/Q13, T/X arms, or confirmatory F2. No deletion, no rewrite, no weakening of any other gate.
`AppendOnlyLedger.verify()` keeps proving the chain.

**Authorization requested:** either (A) — proceed with a fresh chain for corrected runs — or
(B) — authorize this invalidation record kind and its six guard rails. Nothing is implemented
until you choose.
