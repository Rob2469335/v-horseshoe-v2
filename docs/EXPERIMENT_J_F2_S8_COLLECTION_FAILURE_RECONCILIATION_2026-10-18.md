# F2-S8 — Historical reconciliation package: collection failures recorded as terminal scientific outcomes

**Status:** evidence package for an OWNER DECISION. It changes no ledger, no grant and no scientific
rule. Nothing here has been applied to any authoritative record.
**Date:** 2026-10-18
**Repository revision at review:** `37cb3bd424b013d7bb1a12110cae440721159087`
**Prepared by:** the implementation agent. **This is not an authorization and not a decision.**

**Related authority:** `docs/EXPERIMENT_J_F2_LEDGER_INVALIDATION_PROPOSAL.md` (reviewed; it predates
this finding and does not cover it), `docs/EXPERIMENT_J_F2_ADMISSION_COUNTING_RULE.md`.

---

## 1. What was established (independently re-derived from primary bytes)

`qwen_train/f2_evaluator.py` resolved every declared `FAIL_TO_PASS` node absent from the retained JUnit
to `STATUS_MISSING`, and `missing` is in `NOT_PASSED` (`f2_evaluator.py:105`). The producer therefore
declared `fail` on **both** sides, and `f2_s8_compose._result_from_bundles` mapped base≠PASS →
`gold_fail`, which `f2_s8_factory` records with `outcome_class="scientific"` — **terminal, never
re-run**.

pytest marks a module it could not IMPORT as
`<testcase classname="" name="<module>"><error message="collection failure">`. The evaluator discarded
that signal. So a **missing dependency produced the identical record as a genuine
"base fails and gold still fails"** — `PROVEN`.

**Correction applied forward (this commit):** a collection failure explains an ABSENT target node, so
the run establishes no scientific result. It is now `error` — environment/infrastructure, retryable —
not `fail`. The rule is deliberately narrow (collection errors present **AND** a declared node
missing), so a module that collected and genuinely failed still scores `fail`. Verified against the
two admitted controls: both still `base=fail, gold=pass`.

---

## 2. The outcomes under review

Every gold JUnit cited is retained at
`C:\Users\rober\F2_EVIDENCE\s8\pilot_staging_host_phase3\<task>.{base,gold}.junit.hostcopy.xml`
(produced by the phase-3 run whose grant record digest was `57935d17…f40e`, now **consumed**).

| # | Task | Ledger seq | Recorded | Collection `<error>` (gold) | Target nodes ran? | Re-derived under the correction |
|---|---|---:|---|---:|---|---|
| 1 | `aws-cloudformation__cfn-lint-4236` | 11 | `gold_fail` / scientific | 1 of 1 case | **no** | `error` |
| 2 | `conan-io__conan-18832` | 12 | `gold_fail` / scientific | 345 of 345 | **no** | `error` |
| 3 | `conan-io__conan-18840` | 13 | `gold_fail` / scientific | 345 of 345 | **no** | `error` |
| 4 | `jlowin__fastmcp-1632` | 15 | `gold_fail` / scientific | 12 of 12 | **no** | `error` |
| 5 | `matplotlib__matplotlib-30467` | 16 | `gold_fail` / scientific | 5 of 5 | **no** | `error` |
| 6 | `matplotlib__matplotlib-30475` | 17 | `gold_fail` / scientific | 5 of 5 | **no** | `error` |
| 7 | `spesmilo__electrum-10172` | 23 | `gold_fail` / scientific | 41 of 41 | **no** | `error` |
| 8 | `stanfordnlp__dspy-8718` | 24 | `gold_fail` / scientific | 2 (+3 skipped) | **no** | `error` |
| 9 | `networkx__networkx-8207` | 18 | `gold_fail` / scientific | **0** (340 skipped) | **no** — nodes `skipped` | **still `fail`** |

**Eight of nine are `PROVEN` environment failures** (a dependency/plugin import failure prevented
collection; the target test never ran). **Task 9 is a separate, unresolved case**: its target nodes
were *skipped* (reported `could not import 'numpy'`), so on the available retained evidence it does
not meet the narrow collection-failure criterion and the correction leaves it `fail`. Whether a
`skipped`-due-to-missing-dependency node should also be infrastructure is **NOT ESTABLISHED** and is
deliberately not decided here.

**Tasks 1–8 were also excluded from Phase-2 build by name or share the same root cause**, and the
`EXTRA_DEPS` table named `stanfordnlp__dspy-8739` while the manifest built `stanfordnlp__dspy-8718` —
the same instance-id drift that `37cb3bd4` fixed for pytest plugins.

---

## 3. Pre-existing Phase-1 outcomes (same class, earlier era)

| Task | Ledger seq | Recorded | Evidence |
|---|---:|---|---|
| `django-guardian__django-guardian-896` | 8 | `gold_fail` / scientific | **evidence not located under the phase-3 staging path** — `NOT ESTABLISHED` |
| `django-guardian__django-guardian-899` | 7 | `gold_fail` / scientific | same |
| `HypothesisWorks__hypothesis-4532` | 0 | `gold_fail` / scientific | already reconciled via the corrected chain; **out of scope** |

These two carry the same *possible* defect but I could not re-derive them from retained bytes in this
task, so they are flagged, not claimed.

---

## 4. Why I did not change any historical record

1. The ledger is **append-only and hash-chained**; every row's `prev_digest` binds it to its
   predecessor. Editing a row is not a correction, it is a forgery, and the chain would no longer
   verify.
2. `outcome_class="scientific"` is a **frozen scientific classification**. Reclassifying a recorded
   outcome is a change to frozen science.
3. `docs/EXPERIMENT_J_F2_LEDGER_INVALIDATION_PROPOSAL.md` already establishes the only sanctioned
   additive mechanism (a correction chain, as used for `hypothesis-4532`) and requires owner
   authorization. That mechanism is **not** an agent decision.
4. Crediting any of these 8 tasks on the strength of "the environment was broken" would be crediting
   an *attempt*, not a result — forbidden regardless of how the record is repaired.

**Therefore the ledger is unchanged: 26 rows on the operative chain, 1 on the corrected chain, both
`ledger chain intact`; credited union 6; `PROVEN` that no historical ledger state changed in this
task.**

---

## 5. The owner decision required

> **How should the eight outcomes recorded as terminal scientific `gold_fail` on the evidence of an
> environment failure be disposed of?**

| Option | Effect | Consequence to weigh |
|---|---|---|
| **(A) Leave the records as-is; draw the remaining 294 from the unrecorded pool.** | No frozen-science change, no ledger mutation | The 8 tasks stay permanently excluded although their tests never ran. 3,803 eligible candidates remain, so the target is not blocked by this alone. |
| **(B) Leave the records; additionally repair the *cause* and re-attempt the same tasks via a NEW forward run.** | The historical rows stand; new evidence is produced under a fresh grant | **Blocked**: a terminal scientific record is never re-run by design, and the ledger will refuse a second terminal record. This option requires the option-C mechanism first. |
| **(C) Authorize an additive correction chain** (the `hypothesis-4532` pattern) recording, for each affected task, that the prior row evidenced an environment failure rather than a task outcome, plus a fresh governed run whose evidence decides the task on its merits. | The original rows remain byte-for-byte; the correction is appended; the task becomes eligible again | Requires an explicit owner authorization and a documented correction rule. It touches the *classification* of a recorded scientific outcome, which is why it cannot be an agent decision. |

**Recommendation (engineering only): Option (A) now, Option (C) only if the owner wants those eight
candidates back.** With 3,803 never-attempted candidates against a 294 deficit, Option (A) does not
block the objective; Option (B) is not implementable without Option (C); Option (C) is the only path
that recovers the eight, and it is an owner act.

---

## 6. What is required to close this out

1. **Owner decision** on §5. Until then the eight rows stand and are excluded.
2. **A decision on `networkx__networkx-8207`** (§2) — whether a node skipped for a missing dependency
   is infrastructure. `NOT ESTABLISHED`; not decided.
3. **No grant action.** No grant was signed, consumed, revoked or expired in this task. The prior
   session's `s8-001-phase3-retry-2026-10-18` (`d4a4b53e…`) remains **unconsumed** and expires
   naturally `2026-10-19T02:48:44Z`; it is bound to the pre-fix revision and the un-rebuilt manifest,
   so it should be superseded rather than consumed.
4. **The forward correction is in place** and prevents recurrence for every future batch.

**Nothing in this package increases the credited count, and none of it should until the owner decides
§5 and a governed run produces real evidence.**

---

## 7. Phase-1 reconciliation, limits and honest labels (2026-10-18)

### 7.1 Population counts (recomputed from authoritative artifacts)

| Count | Value | Meaning |
|---|---:|---|
| Registered candidates | **3,828** | `registered_tasks.json` |
| Distinct tasks in any ledger | **24** | 26 rows (operative) + 1 (corrected), deduplicated |
| **Credited union** | **6** | the counting rule's figure |
| **Deficit to 300** | **294** | 300 − 6 |
| Terminal scientific (excluding the admitted) | **11** | `gold_fail` rows minus the correction |
| Infrastructure-only (retryable/parked) | **7** | incl. `stanfordnlp__dspy-8739` (parked) |
| **Scheduler-eligible** | **3,811** | 3,828 − 6 credited − 11 terminal scientific |
| **Never recorded in any ledger** | **3,804** | 3,828 − 24 |
| Never built *or* attempted | **3,803** | 3,804 − 1 (`QuantEcon__QuantEcon.py-797` ran `base` then hit a channel fault with no row) |

The three previously reported figures are therefore **three different populations**, not a
contradiction: **3,811** = scheduler-eligible, **3,804** = never in a ledger, **3,803** = never built
or attempted. Raw outcome rows: `base_fail_gold_pass` 6, `gold_fail` 12, `base_pass` 0.

### 7.2 Offline compatibility evidence is far thinner than assumed

`data/f2_population/plugin_compat/plugin_compat_report.json` classifies only **59** tasks:
`OBSERVED_COMPATIBLE` **29**, `OBSERVED_INCOMPATIBLE` **30**, `UNKNOWN` **3,762**. Any reachability
estimate that assumes the bulk of the pool is compatibility-verified is **`NOT ESTABLISHED`**.

### 7.3 What the evaluator repair changed, and what it did not

The repair changes **when a result is classified as a scientific failure versus an
environment/inconclusive error**. That is scientifically consequential: a `gold_fail` is terminal and
is never re-run, so mis-classifying an environment failure as one permanently spends a candidate.

**An unresolved risk was found and is NOT fixed.** The rule keys on the *presence* of a collection
error, not its *cause*. The standard SWE-bench shape — the test file imports a symbol that only the
gold patch adds — produces a **base-side** collection error whose cause is the project's own missing
symbol, i.e. a **legitimate base failure**. Under the current rule that pair is classified
`infrastructure_failure` instead of `base_fail_gold_pass`, which **under-credits a genuine admission**.
Verified in `phase1_policy_experiment.py`: a base-only project-symbol `ImportError` yields
pair = `INFRA`, while the cause is `cannot-import-name`, not a missing environment dependency.

A narrower, **cause-sensitive** candidate rule was prototyped out-of-tree and discriminates correctly
in the same harness (project symbol → not environment; `No module named 'fastapi'` → environment).
**It is deliberately NOT implemented** — it is a scientific-classification policy change and requires
the owner's decision.

**`networkx__networkx-8207` remains unresolved**: 0 collection errors, 340 skipped, target nodes
**skipped** (missing `numpy`). Whether a dependency-skip is infrastructure is `NOT ESTABLISHED`.
