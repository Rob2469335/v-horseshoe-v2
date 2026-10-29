# F2-S8 Repository State, Publication, and Evidence Handoff

**Date:** 2026-10-19
**Purpose:** Give independent reviewers (ChatGPT, Gemini) the real implementation
state from the live repository, not a stale GitHub snapshot.
**Labels used:** `PROVEN` · `SUPPORTED` · `INFERRED` · `NOT ESTABLISHED` ·
`GOVERNANCE GAP` · `REQUIRES OWNER AUTHORIZATION`

---

## A. Repository identity

| Item | Value |
|---|---|
| Working branch | **`agent/s8-build-hardening`** |
| HEAD at handoff start | `f2ca2bb16c2e8f294a8eae11301ee4c85727d821` |
| `origin/master` | `2aad3d1d81ea3ffff7df2628afb9800e9c93cbf3` (unchanged) |
| `origin/agent/s8-build-hardening` (before push) | `622eb51f554776717c8ce723d75e61e018071fd6` |
| PR #33 head branch | `agent/q9-classification-sota` @ `2689df00` |
| Divergence before push | branch ahead **26**, behind 0 |

> **Correction to the mission brief (`PROVEN`).** The brief states the working
> branch is `agent/q9-classification-sota`. That branch holds **PR #33** and is
> *not* the branch this work is on. The S8 engineering work lives on
> `agent/s8-build-hardening`. PR #33 was left untouched throughout.

### PR #33 — verified open and unmerged (`PROVEN`, `gh pr view 33`)

```json
{"number":33,"state":"OPEN","isDraft":false,"mergedAt":null,
 "mergeable":"MERGEABLE","baseRefName":"master",
 "headRefName":"agent/q9-classification-sota",
 "headRefOid":"2689df0074f062f02378d8ae69fd88387097594e",
 "title":"Q9 classification correction + SOTA scanner repair (do not merge)"}
```

---

## B. Publication inventory

### Committed and pushed (this branch)

The 26 commits already on the branch cover, in order of landing:

| Area | Representative commits |
|---|---|
| Shared identity canonicalisation (pre-check ⇄ scorer agreement) | `cb4b2a79`, `98fc1e3d`, `07a02d3f`, `f7cc7d23` |
| Dependency declaration sources: Poetry groups/extras, declared-command plugins, declared pytest pin, Hatch envs | `7aa4741e`, `0e7aaacd`, `e29e02f9`, `30a97847`, `f2ca2bb1` |
| Per-task curation of declared extras (dvc `hdfs`, networkx `default`) | `aec2da48` |
| Batch cap 10 → 50, per-task digest policy, audit CLI evidence index | `e84b237f`, `8f9c03ac`, `a14d3d8b` |
| Park the evidence-ambiguous task pre-manifest | `b3088578` |
| Ledger-count tests reconciled to the adopted counting rule | `ebab5e78` |
| Builder identity marker tracking | `b1032ff7` |
| **This handoff** | see final SHA below |

### Intentionally **excluded** from publication

| Group | Count | Reason |
|---|---|---|
| Pre-existing modified docs (`WORK_LOG.md`, three `docs/EXPERIMENT_J_F2_*` authority docs) | 4 | **Not authored in this work**; includes an authority document. AGENTS.md §3.5 defaults unproven provenance to NOT PROVEN; publishing another party's edits to an authority doc is `REQUIRES OWNER AUTHORIZATION` |
| Untracked scratch/residue (`FAIL_LOG*.txt`, `full_suite_*.txt`, `test_parser.py`, `check_ports.py`, ad-hoc audit scripts, etc.) | 55 | Investigation residue, not deliverables |

**No secrets and no protected model artifacts were published** (`PROVEN`): the
commit set contains only `qwen_train/*.py` and `tests/*.py` edits plus this
document. `.env` was never read or printed. The protected conversion artifacts
live outside the repository under `C:\Users\rober\F2_EVIDENCE\` and are not
tracked.

---

## C. Verification

| Command | Result |
|---|---|
| `pytest tests/test_f2*.py -q` (100 modules) | **3275 passed, 4 skipped, 0 failed** (870 s) at `ebab5e78` |
| Focused suites after each change | acquisition 64 · build-hardening 52 · evaluator 94 · pre-check 40 · audit 184 |
| `ruff check … --select E9,F` | clean on every edited module |
| `git diff --check` | clean |
| Independent package validation | **60/60 checks passed** (bindings 10, identities 9, workspaces 18, eligibility 19, git 4) |

> A full re-run was launched at handoff time; if it had not completed it is
> labelled `NOT ESTABLISHED` here rather than assumed green. The prior run at
> `ebab5e78` is the last **`PROVEN`** full-suite result; `f2ca2bb1` (Hatch reader)
> was verified by its focused suites and is covered by the re-run.

---

## D. Execution architecture (traced from code)

### D.1 Call graph, selection → accepted credit

```
registration (3,828 tasks, registration order)
  → materialise_batch()            qwen_train/f2_s8_materialise.py:92
  → f2_s8_pilot_build              builds base + gold workspaces, writes build manifest
  → f2_s8_precheck                 collects both sides, applies identity canonicalisation,
                                   writes precheck.json (PASS / FAIL / INCONCLUSIVE)
  → f2_s8_phase_ops pre            phase plan + grant_binding_required   :199-243
  → f2_s8_measure_states           per-task BASE/GOLD digests            :281-339
  → owner signs the canonical record (ed25519)
  → f2_s8_pilot_live               verify_and_consume(...) ONCE, then
                                   `for task_id in manifest:`            :178, :419
  → per-task ledger append         f2_s8_factory AppendOnlyLedger
  → f2_s8_post_batch_audit         batch-level D4 audit — MANDATORY for credit
  → f2_reconcile / counting rule   credited union vs accepted credit
```

### D.2 Effective limits

| Limit | Value | Source | Character |
|---|---|---|---|
| Batch cap (planner) | **50** | `f2_s8_phase_ops.py:48 MAX_BATCH` | operational; comment records "Raised 10 -> 50 on the owner's explicit authorization" |
| Batch cap (pre-check) | **50**, hard ceiling | `f2_s8_precheck.py:79`, `:239` raises above 50 | operational |
| Task-count field in the grant | **none** | `f2_authorization.py` `_REQUIRED` | schema binds a *manifest digest* |
| Post-batch audit task limit | **none** | `f2_s8_post_batch_audit.py` | generic row validation |
| Concurrency | operator-chosen (disk gates in the bulk driver) | `bulk2.py` | operational |

`PROVEN`: **the 50 is an owner-authorized operational cap in code, not a
scientific or protocol boundary.** The authorization schema itself would accept
any task count, because it binds `candidate_manifest_digest`.

### D.3 Authorization consumption (`PROVEN`, `f2_authorization.py:714-729`)

* `verify_and_consume` verifies **then** durably consumes **once**, keyed by the
  canonical record digest, using an atomic create.
* "There is no unconsume: a crash after consumption leaves the authorization
  consumed (the fail-closed direction)."
* A second process presenting the same grant is **denied**.

### D.4 Do internal waves work today?

**Yes — as a single top-level execution.** `f2_s8_pilot_live.py:178` loops over
the whole manifest in one process, consuming the grant once at the boundary
(`:414-419`). Resource-bounded *internal* waves are therefore an implementation
question inside one authorized run, not a new authorization.

**A second top-level invocation is grant reuse** and is refused. `INFERRED` from
the atomic single-consumption design; `SUPPORTED` by the explicit "one durable
consumption PER GRANT" docstring.

### D.5 Crash recovery and idempotency

| Property | Behaviour | Evidence |
|---|---|---|
| Grant after a crash | **stays consumed**; resuming needs a **new** grant | `f2_authorization.py:728-729` |
| Per-task idempotency | **exists** — a terminal record for a task causes it to be skipped | `f2_s8_factory.py:20` |
| Second scientific record for a task | **refused** | `f2_s8_factory.py:257` |
| Double credit across a resume | **prevented** by the terminal-record rule | same |

So a crash does not corrupt accounting, but it does **burn the grant**: the
remainder must be authorized again. That is the fail-closed direction, by design.

### D.6 Evidence retention and cleanup

Pre-check artifacts, build manifests, evidence indexes, JUnit and audit records
live under `F2_EVIDENCE`; workspaces under `C:\f2_pilot`. Preparation-time
pruning of **non-PASS** workspaces is safe only because the input record, both
patches, the manifest and the verdict are retained outside the workspace
(verified per prune and recorded in `run_ledger.jsonl`). **PASS workspaces are
never pruned** before digest derivation.

---

## E. The 300-credit path

### E.1 Accepted credit — recomputed from the authoritative ledgers (`PROVEN`)

| Quantity | Value |
|---|---|
| Ledger-admitted union (counting rule) | **9** |
| r4 admitted rows whose **batch audit FAILED** | 3 (`Tuxemon-3068`, `pgmpy-2314`, `sdv-2656`) |
| **Accepted credit (Reading A)** | **6 / 300** |
| Remaining | **294** |

`admitted=True` is not credit: all three r4 audit artifacts read `FAIL` with a
single remaining discrepancy, `QuantEcon__QuantEcon.py-797 base JUNIT_MALFORMED`.

### E.2 Pipeline state at handoff (not interrupted)

| Metric | Value |
|---|---|
| Confirmed eligible candidates | **5** (`dvc-10842/43/44`, `networkx-8196`, `Kozea-Radicale-1844`) |
| Tranche 2 | pre-check running over 18 materialised tasks |
| Observed yield | ≈10% PASS per build |
| Average PASS workspace | **775 MB** (measured) |
| Free disk | ~128 GB |

### E.3 Remaining technical blockers to 300

1. **Elapsed time (`PROVEN` arithmetic).** 294 more credits at ~10% yield needs
   ≈2,900 builds; tranche 1 took 1,747 s for 24 → **≈60 h+** of build time alone.
2. **Workspace residency (`PROVEN`).** 775 MB average × 300 = **≈232 GB**, against
   ~106 GB usable after the 25 GB safety floor. A 300-task batch **cannot hold its
   workspaces resident**; ~137 is the host ceiling.
3. **Grant cannot survive a crash (`PROVEN`).** A single 300-task execution that
   dies mid-way has burned its grant; the remainder needs a new one.
4. **Batch cap 50 (`PROVEN`)** — operational, raiseable only by owner decision.

### E.4 Smallest safe next step

Run **one bounded batch at the current cap (≤50)** from verified PASS candidates,
with the grant consumed exactly once and the D4 audit run on the complete
manifest. This exercises the entire path end-to-end at a size the hardware can
actually hold, and produces the first credited admissions since r4.

### E.5 Decisions that genuinely require the owner

* **Cap raise** 50 → N (N ≤ ~137 measured-residency bound). Ordinary code change
  in two constants plus the ceiling guard; the *number* is an owner decision
  because the previous raise was itself owner-authorized.
* **300 in one grant** requires a **waved staging/execution design** (resident
  capacity cannot hold 300) *and* an explicit answer on whether one grant may
  cover a waved run. `GOVERNANCE GAP` — cannot be settled by an agent.
* **Publishing the 4 pre-existing modified files**, one of which is an authority
  document. `REQUIRES OWNER AUTHORIZATION`.

---

## F. Reviewer navigation

| What to inspect | Path |
|---|---|
| Grant verification + single-use consumption | `qwen_train/f2_authorization.py` (`verify_and_consume` :714; canonical bytes :153) |
| Phase planner + batch cap + grant binding | `qwen_train/f2_s8_phase_ops.py` (:43-48, :199-243) |
| Pre-check, identity canonicalisation, cap ceiling | `qwen_train/f2_s8_precheck.py` (:79, :239, `_target_collected`) |
| Scorer identity matching | `qwen_train/f2_evaluator.py` (`canonical_node_identity`, `nodeid_to_junit_identity`, `parse_junit_xml`, `_assert_identities_distinct`) |
| Dependency sources | `qwen_train/f2_s8_acquisition.py` (`declared_test_requirements`, `poetry_group_dependencies`, `poetry_extra_dependencies`, `hatch_env_dependencies`) |
| Batch D4 audit | `qwen_train/f2_s8_post_batch_audit.py` |
| Ledger + admission rules | `qwen_train/f2_s8_factory.py` (:20, :234, :257) |
| Counting rule | `docs/EXPERIMENT_J_F2_ADMISSION_COUNTING_RULE.md` |
| Tests | `tests/test_f2_evaluator.py`, `tests/test_f2_s8_precheck.py`, `tests/test_f2_s8_acquisition.py`, `tests/test_f2_s8_build_hardening.py`, `tests/test_f2_s8_batch.py`, `tests/test_f2_preflight_evidence_sources.py`, `tests/test_f2_s8_post_batch_audit.py` |
| Live run evidence | `C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger*.jsonl`, `…\bulk_2026*/`, `…\grant_prep_three_precheck\` |

---

## Statement of boundaries observed

PR #33 open and unmerged · `master` untouched · no force-push or history rewrite ·
protected conversion artifacts unmodified and unpublished · no grant signed,
consumed, or reused · no scored execution · no scientific criterion or eligibility
rule altered · `.env` never read or printed.
