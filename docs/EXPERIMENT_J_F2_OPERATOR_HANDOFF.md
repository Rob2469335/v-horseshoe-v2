# Experiment J / F2 — Operator Handoff and Execution Order

**Owner of this document:** the operator (execution) and the experiment authority (authorization).
**Implementation agent:** repository changes only; may not execute or authorize.

**Date:** 2026-10-05
**Describes repository state at:** `3218ffbc` plus this commit.
**Machine-checked by:** `python -m qwen_train.f2_preflight` (`f2_preflight_v1`).

**The one sentence.** Every repository mechanism for F2 exists and is tested, and
**not one prerequisite for actually running it has been satisfied** — the host has no
egress enforcement, there is no S8 evidence, no ACTIVE lesson, no conversion record,
no admitted population, and no curator. Software green is not experimental readiness.

---

## 0. What this document is, and what it is not

This is the **single authoritative operator handoff** for F2. It supersedes any
scattered ordering found elsewhere. Where another document disagrees about *current
state or order*, this one and the two documents below govern:

| Question | Owner |
|---|---|
| What must I provide before we may run? | **this document** |
| Is the repository able to check it? | `docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md` |
| What was decided, and why? | `docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md` |
| Frozen science (do not edit) | `docs/EXPERIMENT_J.md` |
| Live experiment state | `docs/LEARNING_EXPERIMENT_STATE.md` |

This document does **not** restate the frozen statistical design, the McNemar
construction, the population-screening rules, or the evidence schema. Those live in
the documents above and are referenced, not duplicated.

---

## 1. READINESS CHECKLIST — the short answer

> **What exactly must I provide or do before we are allowed to run the experiment?**

Eighteen items are outstanding. Preflight reports them as **18 blocking findings**,
exit code non-zero. Current categories:

| Category | Count | Who |
|---|---|---|
| `OPERATOR ACTION REQUIRED` | 12 | operator |
| `EXTERNAL EVIDENCE REQUIRED` | 4 | external evidence source / operator |
| `PRIVILEGED HOST ACTION REQUIRED` | 1 | privileged administrator |
| `AUTHORIZATION REQUIRED` | 1 | experiment authority |
| `NOT EXECUTED` | 2 | operator (authorize) |
| `IMPLEMENTED` | 16 | done |

Run this yourself — it is read-only and safe:

```powershell
python -m qwen_train.f2_preflight              # human-readable
python -m qwen_train.f2_preflight --json       # machine-readable (schema f2_preflight_v1)
```

Exit code is `0` **only** when the result is `READY`. Today it is `BLOCKED`.

### 1.1 Execution variables you must set (11)

None of these is a secret. Set them in the process environment for the run only.

| Variable | Meaning |
|---|---|
| `SWARM_DISTILLER_MODEL` | distiller model identity |
| `SWARM_DISTILLER_WEIGHTS_DIGEST` | SHA-256 of the distiller weights |
| `SWARM_F2_EVALUATOR_ID` | authorized evaluator id |
| `SWARM_F2_EVALUATOR_VERSION` | authorized evaluator version |
| `SWARM_F2_EVALUATOR_PROCEDURE` | authorized evaluator procedure id |
| `SWARM_F2_EVALUATOR_IMPL` | path to the evaluator implementation artifact |
| `SWARM_F2_ARTIFACT_ROOT` | **absolute** trusted artifact-store root |
| `SWARM_F2_ARTIFACT_RETENTION_DAYS` | retention, whole days |
| `SWARM_F2_EMIT_BUNDLE` | must be enabled or no evidence chain is emitted |
| `SWARM_F2_TASK_OUTCOME_REPORT` | destination the evaluator writes its report to |
| `SWARM_EVALUATOR_STORE` (trusted store) | the store the evidence chain verifies against |

### 1.2 The one secret, and how to handle it

| Property | Value |
|---|---|
| Variable | `SWARM_RECEIPT_KEY` |
| Status | **NOT PROVISIONED** — absent from `.env` and from Process/User/Machine scopes |
| Presence check | preflight tests **presence only** |
| Provisioning | via the approved secret mechanism, for the run only |
| **Must never** | appear in logs, reports, attestations, documentation, or source control |

Until it is present, receipt authority **fails closed** and no promotion can occur.
That is correct behaviour, not an error to work around. No agent may provision it.

### 1.3 Evidence you must obtain from outside this repository (4)

| Item | What it is |
|---|---|
| **S8 base/gold pair** | real executions: same task identity, base = **FAIL**, gold = **PASS**, retained JUnit, run log, artifact digests, evaluator identity/version, clean-room + integrity evidence. **Zero exist today.** |
| **Q9 attestation** | produced by `python -m qwen_train.f2_isolation` **while enforcement is actually in force** |
| **Genuine ACTIVE lesson** | produced by a real clean-room learning event; rehearsal does not count |
| **Conversion record** | see §3 |

### 1.4 Authorization you must obtain (1)

Population acquisition. `F2-IMPL-AUTH-013` **explicitly does not authorize** it.
Until an explicit authorization exists, the admitted population is **zero** and
nothing downstream can be run.

### 1.5 Host control you must obtain (1)

Default-deny outbound egress for the arm's identity. See §4.

---

## 2. THE EXACT EXECUTION ORDER

Sixteen steps. **Do not skip ahead.** Several steps are ordering-dependent: e.g. a
learning event (M) cannot produce a genuine ACTIVE lesson without admitted tasks (B,
C), enforced isolation (F, G) and a receipt key (E).

Status vocabulary is preflight's, so a step's status and the machine report cannot drift.

---

### A. Repository and documentation checkpoint — **IMPLEMENTED**

- **Owner:** repository / agent
- **Inputs:** none
- **Output:** committed, pushed, divergence `0 0`, tracked tree clean
- **Fail-closed if:** tracked tree dirty, or local HEAD ≠ `origin/master`
- **Next:** B

---

### B. Population / task authorization — **AUTHORIZATION REQUIRED**

- **Owner:** experiment authority (not the agent, not the operator acting alone)
- **Inputs:** an explicit authorization to acquire the F2 task population
- **Output:** a recorded authorization entry naming scope, size, and contamination policy
- **Fail-closed if:** no such entry. **Admitted population stays 0.** No task may be admitted.
- **Next:** C

---

### C. Task snapshot and contamination / executability validation — **READY**

- **Owner:** repository machinery (implemented) + operator supplies the tasks
- **Gated on B:** the machinery is ready, but no task may be admitted until population acquisition is authorized
- **Inputs:** authorized tasks; `screen_pool_rows` accepts an external `relevant_file_sets` map and an `evidence_digests` map
- **Output:** a screened, frozen population where every admitted task has a unique immutable identity, source repo, exact base commit, task statement, immutable snapshot, environment identity, test contract, expected behavioral property, contamination metadata, duplicate/leakage detection, broken-task checks and reproducibility
- **Fail-closed if:** a task lacks a frozen `relevant_file_set` — `screen_entry` **rejects** it rather than silently measuring "no qualifying edit". Currently **12 of 14 derived, 2 refused**, admitted **0**.
- **Note:** prefer fresh / private / held-out real-world tasks over contaminated static benchmark artifacts. A task that cannot be executed reproducibly or independently evaluated must not silently enter the population.
- **Next:** D

---

### D. Model conversion-chain provenance completion — **OPERATOR ACTION REQUIRED**

- **Owner:** operator
- **Inputs:** the actual merge and conversion facts (see §3)
- **Output:** a conversion record satisfying all 13 required fields, bound to the artifacts the chain already proves
- **Fail-closed if:** any field absent → link stays `UNRECORDED`; a recorded digest that disagrees with a proven chain link → `MISMATCH`
- **Next:** E

---

### E. Receipt-key provisioning — **OPERATOR ACTION REQUIRED**

- **Owner:** operator, under the approved secret mechanism
- **Inputs:** a key generated and held outside the repository
- **Output:** `SWARM_RECEIPT_KEY` present in the run environment
- **Fail-closed if:** absent — promotion impossible, by design
- **Next:** F

---

### F. Privileged host egress enforcement — **PRIVILEGED HOST ACTION REQUIRED**

- **Owner:** privileged administrator
- **Inputs:** administrator rights
- **Output:** a default-deny outbound egress policy in force for the arm's identity
- **Fail-closed if:** the arm can still reach the network, the run does not proceed. The repository **cannot** impose this and will not pretend to.
- **Next:** G

---

### G. Independent Q9 observation, verification and attestation — **IMPLEMENTED, NOT EXECUTED**

- **Owner:** operator runs it; repository verifies it independently
- **Inputs:** enforcement from F actually in force
- **Output:** a signed attestation bound to arm / rollout / workspace, with digest and required negative control
- **Command:** `python -m qwen_train.f2_isolation`
- **Fail-closed if:** the probe observes anything reachable. There is deliberately **no flag that declares a dimension denied**; `--policy-assert` is recorded as `source=enforced_policy` and rejected without `--policy-identity` and `--policy-sha256`.
- **Next:** H

---

### H. Genuine ACTIVE lesson establishment — **EXTERNAL EVIDENCE REQUIRED / NOT EXECUTED**

- **Owner:** operator, under the learning-event contract
- **Inputs:** admitted tasks (C), enforced isolation + attestation (F, G), receipt key (E)
- **Output:** a genuine `ACTIVE` lesson, verified
- **Fail-closed if:** only rehearsal or synthetic data exists. Rehearsal masquerading as a learning event is the specific failure this gate exists to stop.
- **Next:** I

---

### I. Q5 / Q6 curator authority resolution — **AUTHORIZATION REQUIRED**

- **Owner:** experiment authority
- **Inputs:** a decision on who holds curator and independent-verifier authority
- **Output:** a recorded authority definition
- **Fail-closed if:** undefined. The Q6 **statistical gate is implemented in code**, but a gate with no appointed curator has nobody to answer to it. **Do not let an agent or the operator appoint themselves by default.**
- **Depends on:** Q6's verdict, and the independent regrade's provenance story
- **Next:** J

---

### J. S8 base / gold execution and evidence retention — **EXTERNAL EVIDENCE REQUIRED**

- **Owner:** external evidence source (execution host) + operator retention
- **Inputs:** admitted tasks (C), evaluator identity/version (env), trusted store
- **Output:** retained base-FAIL and gold-PASS evidence pairs in the trusted store
- **Required per pair:** same authorized task identity; correct base/gold binding; correct arm identity; base = **FAIL**; gold = **PASS**; evaluator identity + version + governed procedure; retained JUnit/evaluator evidence; run log; artifact digests and sizes; execution identity; clean-room/integrity evidence; Q9 evidence where required
- **Fail-closed if:** any element missing; artifacts absent; or anyone supplies a caller-asserted `{"verified": true}`. **No re-execution during regrade** — regrade reads retained evidence only.
- **Next:** K

---

### K. Preflight — **IMPLEMENTED** (the final gate)

- **Owner:** repository / agent
- **Command:** `python -m qwen_train.f2_preflight`
- **Output:** `READY` with exit code 0, or `BLOCKED` with an itemized reason per finding
- **Fail-closed if:** result is anything but `READY`. **A non-zero exit blocks everything downstream.**
- **Meaning of READY:** the required evidence and environmental prerequisites have **actually been established**. It never means "the code that checks readiness exists."
- **Next:** L

---

### L. Q10 calibration — **NOT EXECUTED** (machinery implemented)

- **Owner:** operator authorizes
- **Inputs:** READY from K
- **Output:** measured `p_X`, `k`, and the censoring rates
- **Fail-closed if:** run without authorization, or without READY
- **Next:** M

---

### M. Q12 genuine learning event — **NOT EXECUTED**

- **Owner:** operator, under §5 below
- **Inputs:** calibration from L
- **Output:** a real learning event meeting the frozen contract
- **Fail-closed if:** fewer than **≥3 behavioural runs** or **>2 tasks**, no genuine ACTIVE lesson, no verified HMAC receipt, no causal relevance, or rehearsal data presented as learning
- **Next:** N

---

### N. Confirmatory F2 (Q13) — **NOT EXECUTED**

- **Owner:** operator authorizes
- **Inputs:** everything above
- **Output:** confirmatory arms under the frozen design
- **Fail-closed if:** run merely because software gates are green. **This is the single most important prohibition in this document.**
- **Next:** O

---

### O. Independent regrade and statistical analysis — **IMPLEMENTED**

- **Owner:** repository / agent
- **Inputs:** retained evidence only
- **Output:** the frozen endpoint — exact two-sided McNemar, α = .05, 95% CI — from an independent reconstruction that must agree with the producer
- **Fail-closed if:** reconstruction disagrees with the producer, or if anything re-executes during regrade
- **Next:** P

---

### P. Final evidence / provenance package — **READY** (assembly machinery implemented)

- **Owner:** repository / agent + operator sign-off
- **Inputs:** the full retained chain
- **Output:** a provenance package in which every artifact is bound by digest, every claim traceable, and every unestablished item labelled `NOT ESTABLISHED` rather than omitted
- **Fail-closed if:** any item is claimed that was not established
- **Next:** done

---

## 3. Conversion provenance — the exact remedy

**Current real state (verified against the real artifacts):**

| Link | State |
|---|---|
| base model | **PROVEN** |
| adapter | **PROVEN** |
| training corpus | **PROVEN** by recorded metadata |
| served GGUF | **PROVEN** by digest |
| **adapter → merged artifact → GGUF** | **UNRECORDED** |

**There is no conversion script in this repository, and none was invented.**

### 3.1 Timestamps are not conversion evidence

A file or run timestamp ordering shows what happened *when*. It does not show what
produced what. A record whose only content is ordering is refused, and there is a test
asserting exactly that.

### 3.2 The 13 required fields

A conversion record is only a conversion record if it names the **whole operation**.
Naming a merged artifact says what exists, not what produced it.

| Field | Must contain |
|---|---|
| `source_base` | identity of the base artifact that was adapted |
| `source_base_sha256` | SHA-256 of that base artifact |
| `source_adapter` | identity of the adapter that was merged |
| `source_adapter_sha256` | SHA-256 of that adapter's config/weights |
| `merge_operation` | identity of the merge operation (script + arguments) |
| `conversion_tool` | identity of the converter used |
| `conversion_tool_version` | version of that converter |
| `conversion_inputs` | ordered input identities the converter consumed |
| `merged_artifact` | identity of the merged/intermediate artifact |
| `merged_artifact_sha256` | SHA-256 of that merged artifact |
| `served_gguf` | identity of the GGUF actually served |
| `served_gguf_sha256` | SHA-256 of that GGUF |
| `operator` | operator or process identity, where governed |

**Timestamp** is recorded separately as an **observation**, never as proof of conversion.

### 3.3 The record is bound to the chain, not trusted on its word

A record is additionally cross-checked against the links the chain already proves:

| Recorded field | Checked against |
|---|---|
| `source_base_sha256` | the proven `base_model` digest |
| `source_adapter_sha256` | the proven `adapter` digest |
| `served_gguf_sha256` | the proven `served_artifact` digest |

A complete-looking record naming a **different** adapter than the chain proves is
`MISMATCH` — not a pass. Machine-checkable via `validate_conversion_record` and
`CONVERSION_FIELD_REMEDY`.

---

## 4. Q9 — what an administrator must actually provide

The architecture is deliberately layered, and each layer does exactly one job:

```
PRIVILEGED ENFORCEMENT          <- MISSING. Requires an administrator.
        v
UNPRIVILEGED OBSERVATION        <- IMPLEMENTED (f2_isolation probes)
        v
INDEPENDENT VERIFICATION        <- IMPLEMENTED (re-derived, not read from the claim)
        v
CRYPTOGRAPHICALLY BOUND ATTESTATION  <- IMPLEMENTED (bound to arm/rollout/workspace + digest)
        v
READINESS GATE                 <- IMPLEMENTED (refuses a caller-typed "denied")
```

**Enforcement is the only missing layer, and it cannot be written in Python.**

An administrator must apply a default-deny outbound egress policy for the arm's
identity. Then the operator runs `python -m qwen_train.f2_isolation` and supplies the
attestation to the readiness gate.

The repository must not, and does not: change firewall rules, routing or DNS; install
drivers; require Administrator; or infer isolation from a failed probe.

**Current live result on this host: `NOT ESTABLISHED`.** `http`, `https`, `udp`,
`loopback` and `required_service` all observed **REACHABLE**. That is the truthful
reading of a host with no egress enforcement, and it must never be reclassified as
denied to make readiness pass.

---

## 5. Q12 — distinguishing a learning event from rehearsal

Four different things, routinely conflated. Only one satisfies the gate.

| Thing | What it is | Counts for Q12? |
|---|---|---|
| Learning **machinery** | repository code that can record a learning event | No |
| Engineering **rehearsal** | a dry run against synthetic data | **No** |
| **Genuine learning event** | a real event under the frozen contract | Yes |
| **Genuine ACTIVE lesson** | the verified product of such an event | Yes — this is what H must produce |

Required evidence for Q12:

- **≥ 3 behavioural runs**
- **> 2 tasks**
- a **genuine ACTIVE lesson**
- a **verified HMAC receipt** (requires the receipt key, step E)
- **causal relevance** — the lesson must matter to the measured behaviour
- **no rehearsal masquerading as learning**

The reflection daemon is not part of any authorized execution path. A learning run must
not silently hold network or repository-mutating MCP.

**Q12 is not executed and is not authorized in this repository state.**

---

## 6. Q5 / Q6 curator authority — the governance blocker

**What is missing.** A definition of who holds *curator* authority and *independent
verifier* authority. It is undefined. There is **no appointed curator**, and no
mechanism that appoints one.

**What has already been built.** The Q6 *statistical* gate is implemented in code: a
threshold of **0.30**, and it is pinned by tests to **change no statistic and drop no
pair**. That is machinery awaiting an authority, not an authority.

**What decision must be made.** Who may (a) judge curator-side artifacts such as the
frozen `relevant_file_set` and the gold artifact, and (b) sign off the independent
regrade. It must be a role with defined powers, not a default that an agent or the
operator silently inherits.

**Which later gates depend on it.** I (Q5/Q6 resolution), and the provenance story of
O. Q6's verdict has no addressee until this is settled.

**Interim convention in the docs.** Where existing text says "curator-side", read it as
*"the gated side, whose authority is undefined"*. That phrasing is flagged for cleanup
but is not a claim that a curator exists.

---

## 7. What this document does not authorize

It does **not** execute S8, Q10, Q12 or Q13; enforce egress; touch firewall, routing or
DNS; require Administrator; admit a task; fabricate S8 evidence; provision a receipt
key; invent a conversion record or an ACTIVE lesson; appoint a curator; alter F0, F1,
or the frozen statistical design; or place any secret in source control.

---

## 8. Where the truth lives

| I want to know… | Read |
|---|---|
| what to provide, and in what order | **this document** |
| whether the repository can check it, and how the gates work | `docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md` |
| why each decision was taken (F2-IMPL-AUTH-001…-023) | `docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md` |
| frozen science — **never edit** | `docs/EXPERIMENT_J.md` |
| live state and operator decisions D-1…D-16 | `docs/LEARNING_EXPERIMENT_STATE.md` |
| the population / task contract | `docs/EXPERIMENT_J_TASK_READINESS_CONTRACT.md` |
| what happened in the past | `WORK_LOG.md` |

**Re-verify, do not recall.** Preflight output is the machine-readable form of the
state claims above; run it rather than trusting this document's date.