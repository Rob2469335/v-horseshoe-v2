# Experiment J/F2 — Exploratory T/X Authority (narrowly scoped amendment)

**Entry:** `F2-IMPL-AUTH-024` — exploratory T/X pilot
**Authorizing role:** experiment authority / operator, recorded on the operator's
explicit instruction of 2026-10-05
**Recorded by:** implementation agent, in the repository's existing ledger format
**Does not modify:** F0, F1, the frozen endpoint, T/X/C0 semantics, `k`, α, the
confidence level, the McNemar method, or the Q6 ceiling.

---

## 1. Why this entry exists

The frozen confirmatory design **cannot currently be validly powered**, and this was
established from evidence rather than convenience.

`qwen_train/f2_statistics.required_pairs(delta, discordance, ...)` refuses any design
with `delta > discordance`:

> `delta=0.8 exceeds discordance=0.5: the paired risk difference cannot be larger than
> the fraction of pairs that differ at all.`

Both planning parameters are **empirical and unobtainable under existing authority**:

* **δ is `NOT ESTABLISHED`.** No authorized run measures a treatment effect.
* **π_d is `NOT ESTABLISHED`.** Q10 is a no-lesson calibration with
  `CALIBRATION_ARMS = ("X", "C0")` — **no T arm**. π_d = P(T xor X) requires both arms.
  The earlier `p_X`-derived estimator was **withdrawn as mathematically impossible**.

Fresh SOTA research **agrees with the repository's own finding**: Schlesselman (1992)
states an investigator "is unable to specify the probability of discordance" from
marginals alone; Miettinen (1987) treats π_d as a **nuisance parameter**; the
literature's recommended remedy is an **internal/blinded pilot or external evidence** —
neither of which exists in the repository.

A confirmatory N therefore **cannot be computed**. Rather than fabricate δ or π_d,
this entry authorises a **genuine paired T/X contrast run as exploratory**, which is
the only measurement that observes π_d = (b + c) / m directly.

## 2. Scientific objective and estimand

* **Objective:** estimate the paired discordance probability π_d and the paired risk
  difference δ for the T/X contrast, on a screening population, to inform a future
  confirmatory design.
* **Estimand:** the paired risk difference in per-pair task success, and the
  proportion of discordant pairs — both **observational quantities of this pilot**,
  not estimates of a population effect.

## 3. Paired unit of analysis

**One pair = one task**, run once under T and once under X
(`F2-READINESS-AND-STATISTICAL-PLAN.md:146`). Repeated runs of the same task are
**replicates within one clustered unit** and **MUST NOT** be counted as additional
independent pairs. This follows the standard position that repeated runs of one
benchmark item are not IID (Bowyer et al., PMLR v267, ICML 2025) and that treating
them as independent overstates effective sample size (arXiv 2510.05709).

## 4. Population and contamination policy

Tasks are drawn from the existing screened pool. **Every task in
`qwen_train/curriculum/swe_pool.jsonl` has contamination status `UNKNOWN`** — the file
carries no contamination field, no model training cutoff, and no unseen-by-model
evidence. Per current authority, `UNKNOWN != CLEAN`.

**Consequence, binding:** no task from this pool may support a **confirmatory**
claim. This pilot is exploratory, which is consistent with standard practice of
allowing exploratory analyses on data of unestablished provenance provided they are
**labelled** as such.

`pytest-dev__pyfakefs-916` and `pallets__click-2380` remain **REFUSED** under
`screening` and MUST NOT be admitted.

**Population acquisition is still `REQUIRES AUTHORIZATION`** — this entry does not
authorise it.

## 5. Analysis — reuse existing machinery

No new statistical machinery is created. The pilot reuses, unchanged:

* `f2_statistics.mcnemar_exact` for the paired test and its **95% Clopper–Pearson
  exact conditional interval** (the one authoritative confirmatory interval);
* `f2_statistics.exact_power` / `required_pairs` for reporting;
* the Q6 operational gate for infrastructure failures.

Reported for every pilot: `m`, `b`, `c`, discordant count, π_d = (b+c)/m, δ̂ = b/m − c/m,
the exact two-sided p-value, and the 95% interval.

**Mandatory reporting rule.** A pilot p-value is reported as a **descriptive quantity
with an interval**. It is **never** reported as a confirmatory significant result, and
no hypothesis is tested at α for decision purposes.

## 6. Prohibitions (binding)

1. **No confirmatory significance claim.** No pilot result may be reported as
   evidence that treatment works, or as a confirmatory endpoint result.
2. **No post-hoc task selection.** No task may be dropped, reordered, replaced,
   filtered or added after observing any pilot outcome. The task set is frozen in the
   pilot authorization **before** the first T or X rollout. (Standard practice:
   pre-specify the eligible set and selection rule; post-hoc selection under a
   confirmatory banner is the documented p-hacking hazard.)
3. **No pseudo-replication.** Repeated runs of one task never increase `m`.
4. **No pooling with Q10.** Q10 is X/C0 and is not T/X evidence.
5. **No alteration of frozen design.** T/X/C0 semantics, `k`, α, confidence level,
   McNemar method, and the Q6 ceiling are unchanged.
6. **No promotion of this pilot into the confirmatory record.** Confirmatory F2
   remains gated on a separately authorized design with obtainable planning
   parameters.

## 7. Prerequisites that remain external (unchanged)

This entry authorises **no** execution. Every external prerequisite still stands:

* **Q9 privileged egress enforcement** — `PRIVILEGED HOST ACTION REQUIRED`. The
  attestation now additionally requires a bound **enforcement identity**
  (`assess_enforcement_identity`); see the handoff §4.
* **`SWARM_RECEIPT_KEY`** — `OPERATOR ACTION REQUIRED`; not provisioned, not guessed.
* **Genuine ACTIVE lesson** — requires Q9 + receipt key + an admitted population.
* **The 11 execution variables** — operator-owned.

## 8. Governance — separation of duties (minimum viable)

Per `EXPERIMENT_J_F2_OPERATOR_HANDOFF.md:655-656` and `:658-660`, the operator may
hold the exclusion-adjudication role **only** with an explicit, recorded separation.
This entry requires a **written record** — the minimum needed for auditability, not
organisational theatre — naming:

| Role | Requirement |
|---|---|
| 1. Run authority | the experiment authority |
| 2. Operator | executes the pilot |
| 3. Exclusion adjudicator | a party **other than** the sole run operator, OR the purely mechanical pre-specified rule set in `F2-IMPL-AUTH-018` §Q5 |
| 4. Independent regrade | a party other than the pilot operator |
| 5. Q5/Q6 adjudication | per `F2-IMPL-AUTH-018` §Q6 |

**Mechanically enforced:** `qwen_train/f2_governance` refuses a governance record
whose exclusion adjudicator is identical to its run authorizer
(`SeparationOfDutiesError`). Absence of the record fails closed.

## 9. What remains `REQUIRES AUTHORIZATION`

* Population **acquisition** (`F2-IMPL-AUTH-013`).
* Any **confirmatory** F2 or Q13 execution.
* Any change to the frozen design.
* Appointing a curator where a genuinely independent third party is required — the
  repository does not name an appointing authority (`GOVERNANCE GAP`).

## 10. Live status at the time of recording

`python -m qwen_train.f2_preflight` → **BLOCKED, 18 blocking findings** (unchanged by
this entry, which authorises an analysis design, not an execution). The pilot is
**NOT EXECUTED** and cannot begin until §§4–7 external prerequisites are satisfied.

---

*Implementation status: authority recorded; enforcement-identity binding implemented
(`f2_isolation.assess_enforcement_identity`) and gated in `f2_readiness`; separation of
duties mechanically enforced. No experiment step executed.*