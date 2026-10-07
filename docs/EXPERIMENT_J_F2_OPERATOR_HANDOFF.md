# Experiment J / F2 — Operator Handoff and Execution Order

**Owner of this document:** the operator (execution) and the experiment authority (authorization).
**Implementation agent:** repository changes only; may not execute or authorize.

**Date:** 2026-10-05
**Describes repository state at:** `3218ffbc` plus this commit.
**Machine-checked by:** `python -m qwen_train.f2_preflight` (`f2_preflight_v1`).

> **UPDATE 2026-10-05 (F2-IMPL-AUTH-024).** The frozen confirmatory design
> **cannot be validly powered**:
equired_pairs rejects delta > discordance, and
> neither delta nor pi_d is obtainable under existing authority (Q10 has no T arm).
> A narrowly scoped **exploratory T/X pilot** is now authorized to estimate them:
> docs/EXPERIMENT_J_F2_EXPLORATORY_AUTHORIZATION.md. It is **NOT EXECUTED** and
> authorizes no execution. Two code changes below now gate on identity.

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

| Variable | Meaning | Owner | Secret? |
|---|---|---|---|
| `SWARM_DISTILLER_MODEL` | distiller model identity (**Q7 decision**) | operator | no |
| `SWARM_DISTILLER_WEIGHTS_DIGEST` | SHA-256 of the distiller weights | operator | no |
| `SWARM_F2_EVALUATOR_ID` | authorized evaluator id | operator (naming what is authorized) | no |
| `SWARM_F2_EVALUATOR_VERSION` | authorized evaluator version | operator | no |
| `SWARM_F2_EVALUATOR_PROCEDURE` | authorized evaluator procedure id | operator | no |
| `SWARM_F2_EVALUATOR_IMPL` | path to the evaluator implementation artifact | operator | no |
| `SWARM_F2_ARTIFACT_ROOT` | **absolute** trusted artifact-store root | operator | no |
| `SWARM_F2_ARTIFACT_RETENTION_DAYS` | retention, whole days | operator | no |
| `SWARM_F2_EMIT_BUNDLE` | must be enabled or no evidence chain is emitted | operator | no |
| `SWARM_F2_TASK_OUTCOME_REPORT` | destination the evaluator writes its report to | operator | no |
| `SWARM_RECEIPT_KEY` | receipt signing key (**Q11**) | operator | **YES — never print** |

`protocol_version` is **not** operator-supplied: it is the repository constant
`f2_protocol.F2_PROTOCOL_ID = "f2_experiment_j_v1"`. Do not set it.

**None of these may be provisioned by the implementation agent.** The
authorization ledger states this repeatedly — *"no provisioning of
`SWARM_RECEIPT_KEY`, `SWARM_DISTILLER_MODEL`, `SWARM_DISTILLER_WEIGHTS_DIGEST`"* -
and *"SECRETS (Q7, Q11). Owner: the operator. Location: the operator's `.env` only."*

**Non-secret verification** (prints no secret values; safe to paste):

```powershell
python -m qwen_train.f2_preflight              # lists every absent variable by name
python -m qwen_train.f2_preflight --json       # machine-readable, same guarantee
```

Preflight reports **presence and shape only**. `_check_weights_digest` validates the
digest is a well-formed SHA-256 and explicitly does not print it.

### 1.2 The one secret, and how to handle it

| Property | Value |
|---|---|
| Variable | `SWARM_RECEIPT_KEY` |
| Status | **NOT PROVISIONED** — absent from `.env` and from Process/User/Machine scopes |
| Provision location | the operator's **`.env` only**, per the authorization ledger. Never the arm workspace, a task file, a log, the bundle payload, or a model prompt |
| Presence check | preflight tests **presence only** and never prints the value |
| Scope | **for the run only** - not persisted into tracked files |
| **Must never** | appear in logs, reports, attestations, documentation, or source control |

**Note on an existing file.** `data/prompt_repairer_receipt.key` exists on this host.
Its presence is **not** provisioning: the readiness gate reads the
`SWARM_RECEIPT_KEY` environment variable, not that file. Whether it is the correct
key for this experiment is **UNVERIFIED**, and no agent has read it or may.

**Non-secret presence verification** (never echoes the value):

```powershell
# True/False only - never prints the key
[bool]$env:SWARM_RECEIPT_KEY
```

Prohibited: `echo $env:SWARM_RECEIPT_KEY`, `Get-ChildItem Env:SWARM_RECEIPT_KEY`,
`Get-Content data/prompt_repairer_receipt.key`, or pasting the key anywhere.

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

### I. Curator office definition — **AUTHORIZATION REQUIRED**

> Q5 and Q6 themselves are **GRANTED** (`F2-IMPL-AUTH-018`, operator, 2026-10-05) and
> enforced by construction. Only the *office* below remains undefined.

- **Owner:** experiment authority
- **Inputs:** a decision on who holds curator and independent-verifier authority
- **Output:** a recorded authority definition naming that role and its powers
- **Fail-closed if:** undefined. Q5's exclusion rule and Q6's 30 % ceiling are settled and enforced; what lacks an addressee is who adjudicates an individual classification and who signs the regrade. **Do not let an agent or the operator appoint themselves by default.**
- **Depends on:** the independent regrade's provenance story (step O)
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

### 3.4 Field-by-field availability — investigated 2026-10-05

A genuine recovery attempt was made against the real artifacts on this host. **It
did not succeed, and the record was therefore NOT created.** What follows is what is
actually recoverable and what is genuinely lost.

**Recoverable (real evidence):**

| Field | Evidence | Source |
|---|---|---|
| `merged_artifact` (name only) | `Robs4B_Merged_Hf` | the served GGUF's **own** `general.name` KV — recorded by the converter in the artifact bytes |
| `conversion_tool` (existence only) | `convert_hf_to_gguf.py` present at `Projects\llama.cpp`, checkout `c0bc859` (2026-07-23) | filesystem |

**Not recoverable — the evidence does not exist on this host:**

| Field | Why it cannot be supplied |
|---|---|
| `merged_artifact_sha256` | the merged HF artifact `Robs4B_Merged_Hf` is **not on disk anywhere**; its digest cannot be computed |
| `source_adapter` / `source_adapter_sha256` | **the crux.** The GGUF records the merged model's *name* but **not which adapter was merged into it**. Nothing on disk states it |
| `merge_operation` | `merge_v6.py` / `merge_verify_pod.py` **do not exist anywhere** on this host |
| `conversion_inputs` | ordered converter inputs were never recorded |
| `operator` | not recorded |
| `conversion_tool_version` (binding) | a llama.cpp checkout exists, but **no record binds that checkout to this GGUF** |
| `served_gguf_sha256` | ✅ recoverable — already PROVEN |

**A trap worth naming.** `AGENTS_LEGACY.md` records a contemporaneous Merge→GGUF
entry for **`v4_lora_q4km.gguf`** (llama.cpp `b10107`, `--no-mtp`, Q4_K_M). That is a
genuine contemporaneous record — but it documents a **different artifact**, and
`qwen_train/v4_lora_q4km.gguf` **no longer exists on disk**. It must **not** be
transferred to `robs4b_q4km.gguf`. Its authority stops at the artifact it names.

**What would actually close this.** Either (a) recovery of the merged
`Robs4B_Merged_Hf` weights so its digest can be computed, plus any surviving
converter run log naming the adapter; or (b) **re-performing the merge and conversion
from the known adapter under a recorded, witnessed operation** and keeping that
record. Option (b) is legitimate; inventing a record for the historical run is not.

### 3.5 Why a name-based guess would be unsafe — 47 adapters on this host

A filesystem sweep of `Projects\` and `models\` finds **47 adapter directories** with
at least **9 distinct `adapter_config.json` digests** and ranks from `r=8` to `r=64`.
Critically, the candidates whose names look most likely are **not** distinguishable:

| Adapter | r / alpha | config digest | declared base |
|---|---|---|---|
| `robs4b_final_adapter` | 16 / 32 | `294feecc…` | `/workspace/hf_cache/…/851bf6e8…` |
| `robs4b_v2_adapter` | 16 / 32 | `a56217cc…` | `/workspace/hf_cache/…/851bf6e8…` |
| `qwen3_5_4b_real68_v6_lora\adapter` | 16 / 32 | `0a12b240…` | `/workspace/hf_cache/…/851bf6e8…` |
| `robs4b_r64_adapter\adapter` | 64 / 128 | `d6516749…` | `workspace/hf_cache/Qwen3.5-4B` |
| most `qwen3_5_4b_*` adapters | 8 / 16 | 9 distinct | `C:\Users\rober\models\Qwen3.5-4B-Base-HF` |

So "which adapter?" is **genuinely underdetermined**, and the served GGUF records
only the merged model's name. Any answer inferred from a name or a timestamp is a
guess between materially different candidates. This is the concrete reason the
`INFERRED` label in `INFERENCE_TOPOLOGY.md` matters.

Note also that the three plausible r=16/α=32 adapters declare a **container path**
(`/workspace/hf_cache/…`) that does not exist on this host, while the base that *is*
present locally is a different path. Whether the local base is the same immutable
revision `851bf6e8…` is **UNVERIFIED**.

### 3.6 Route 2 feasibility — determined, not executed

Per instruction, re-conversion was **assessed but NOT performed**. Prerequisites:

| Prerequisite | State | Note |
|---|---|---|
| Base weights readable | ✅ PRESENT | `models\Qwen3.5-4B-Base-HF` — config + 2 safetensors shards (5.33 GB + 3.99 GB) + index + tokenizer |
| Base = revision `851bf6e8…` | ⚠️ **UNVERIFIED** | local path differs from the adapters' declared container path |
| Adapter weights | ✅ PRESENT | `robs4b_final_adapter\adapter_model.safetensors` (84 968 408 B) — *if* that is the right adapter |
| HF→GGUF converter | ✅ PRESENT | `llama.cpp\convert_hf_to_gguf.py`, checkout `c0bc859` |
| **Merge script** | ❌ **ABSENT** | `merge_v6.py` / `merge_verify_pod.py` do not exist; a merge step must be authored and run |
| **`llama-quantize` binary** | ❌ **NOT BUILT** | required for Q4_K_M; `llama.cpp` must be built |
| Merged artifact | ❌ ABSENT | this is the artifact whose digest the record requires |
| Operator + converter version | ❌ UNRECORDED | human attestations |

**Assessment: route 2 is technically plausible but is NOT a single command.** It
requires building llama.cpp, authoring a merge step, resolving which adapter is
correct, and establishing the base revision — each of which is a decision or a
prerequisite, not a step this repository may take. And even if performed, it
documents a **new** artifact; it does not retroactively document the historical one.

When it is performed, the record is assembled — not hand-typed — with:

```python
from qwen_train.f2_model_provenance import derive_conversion_record
rec = derive_conversion_record(
    source_adapter=..., merged_artifact=..., served_gguf=...,
    conversion_tool=..., conversion_tool_version=...,   # attested
    merge_operation=..., operator=..., conversion_inputs=...,  # attested
)
```

It omits anything it cannot read, so an incomplete operation cannot produce a
passing record.

---

## 4. Q9 — what an administrator must actually provide

> **Isolation-boundary reconciliation (2026-10-07, F2-IMPL-AUTH-025).** The
> enforcement scope below is framed around a host identity / Windows Sandbox. A
> completed investigation recommends a Hyper-V VM on an Internal switch with a
> narrowly scoped host-side model gateway, and marks the older
> model-inside-Sandbox contract (F2-IMPL-AUTH-009 section 3) **PROVISIONAL**. This
> does not change the fact that **egress enforcement is still the missing
> privileged layer**. See
> `docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md`
> (F2-IMPL-AUTH-025; runtime suppression, fail-closed Qdrant/embedding check,
> capability strip and model gateway implemented per **F2-IMPL-AUTH-027**).
> **No F2 execution may use a revised topology until it is formally authorized.**

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

### 4.1 Exact administrator action

An administrator must apply a **default-deny outbound egress policy scoped to the F2
arm's identity** — not to the whole machine, since the model backends, Qdrant and
Qdrant's local port must keep working.

| Requirement | Detail |
|---|---|
| Direction | **outbound** deny by default; loopback and the required local services explicitly allowed |
| Scope | the arm's identity / the F2 execution context only |
| Must actually deny | `http`, `https`, `udp` to any non-local destination |
| Evidence that it is *enforced* | re-running the probes reports them **unreachable** — configuration alone is not enforcement |
| Prohibited | claiming isolation because a firewall **profile** shows `Enabled`. A profile being enabled says nothing about egress deny rules. |

**Not performed here:** the current session is not Administrator
(`WindowsPrincipal.IsInRole(Administrator) = False`, token not elevated). No firewall,
routing, or DNS change was attempted or simulated.

### 4.3 ADMINISTRATOR HANDOFF - minimum privileged setup specification

**Do not execute without an operator instruction.** This specifies what an
Administrator must supply; it is a specification, not a script.

**Why required.** Fresh research established two Microsoft platform facts that
change the design:

1. **Windows Firewall does not filter loopback.** A loopback address cannot even be
   encoded in a rule's address field on current Windows (dedicated semantic-error
   codes exist for exactly that). Therefore *loopback reachability is neither
   evidence of egress enforcement nor a fault* - it must not be read as either.
2. **A firewall `-Program` rule does NOT inherit to child processes.** The
   condition matches one executable image by full path. The F2 arm spawns a child
   backend, so every image must be scoped individually.

**Prerequisite:** a default-deny outbound control for non-loopback traffic, scoped so
the operator's own browsing and the local model/Qdrant services keep working.

**Expected identity/scope.** Choose ONE and record which:

| Scope model | Enforcement scope | Independent verification |
|---|---|---|
| windows_account | a dedicated Windows account running only the arm | rule's LocalUser / account SID |
| irewall_program_path | per-image outbound block on each spawned executable | rule's resolved application path |
| m / windows_sandbox | the whole execution environment | host cannot observe it |

**Child-process inventory required.** A -Program rule does not cover children, so
the administrator must be given the exact image list. The F2 attestation now carries
spawn_image_inventory for precisely this.

**Verification evidence required.** Re-run the probes **under enforcement** and
supply the attestation, which must now bind:

* --enforcement-scope (one of windows_account, irewall_program_path, m,
  windows_sandbox)
* --interpreter-path and --interpreter-sha256
* --executing-user
* --spawn-image for every image the arm will spawn

ssess_enforcement_identity **fails closed**: an attestation whose probes pass but
whose enforcement identity is unbound is **refused** by the readiness gate. A probe
records *that* a connection failed, never *which control* failed it.

**Rollback requirement.** The control must be removable by the Administrator without
touching unrelated rules, and the removal must be verifiable independently of this
repository.

**How F2 consumes the evidence.** 2_readiness._check_no_egress re-derives the
verdict with erify_isolation_attestation and then requires
ssess_enforcement_identity to be bound. Neither the value of any secret nor the
key itself is involved.

**Not established by this repository:** which scope model the operator will choose,
the concrete rule set, and whether the arm spawns any image beyond the interpreter.
Those are operator/Administrator decisions and are recorded as **NOT ESTABLISHED**
rather than guessed.

### 4.2 Post-change probe and the artifact that must be retained

```powershell
python -m qwen_train.f2_isolation
```

The artifact to retain is the **attestation JSON** it writes — bound to
arm / rollout / workspace, carrying a digest and a required negative control. Supply
it to the readiness gate as `no_egress_attestation`.

**Enforcement vs observation — the distinction that matters:**

| | Enforcement | Observation |
|---|---|---|
| What it is | the host actually denies egress | unprivileged probes *see* what happened |
| Who does it | privileged administrator | this repository |
| Can the repo do it | **no** | yes |
| Is it Q9 evidence | yes | **no, on its own** |

An attestation without underlying enforcement is worthless, and the gate is built so
it cannot be believed on its own: `_check_clean_room` re-derives the verdict via
`verify_isolation_attestation` rather than reading the claim. A supplied attestation
"can only ever be confirmed or refuted".

There is deliberately **no flag that declares a dimension denied**.
`--policy-assert` records `source=enforced_policy` and is rejected unless
`--policy-identity` and `--policy-sha256` are supplied.

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

## 6. Q5 / Q6 — granted as rules; the curator *office* is still undefined

> **Corrected 2026-10-05.** This section previously read "Q5 / Q6 curator authority —
> the governance blocker" and described Q6 as a pending decision. Both were wrong.
> `F2-IMPL-AUTH-018` (operator, 2026-10-05) **already granted Q5 and Q6**.

**Q5 — GRANTED.** Block-level exclusion: a block may be excluded only for a
pre-specified, **outcome-independent** reason established before the paired outcome is
observed (missing provenance; failed base/gold verification; infrastructure
invalidity; contamination classification; malformed task; reproducibility failure;
missing required artifact). Excluding a block because T won, X won, or the result is
inconvenient is **FORBIDDEN**. A single invalid arm invalidates the whole pair.
Enforced by construction — `f2_admission.admit_f2_task` requires both T and X bundles
and fails closed otherwise.

**Q6 — GRANTED.** The infrastructure-failure budget ≤ 30 % is an **operational
feasibility ceiling, NOT a statistical property**. On breach: STOP acquisition/execution
and investigate the environment. The ceiling never justifies selectively discarding
observations; all infrastructure failures remain in the ledger; the denominator is
never reset. Implemented at threshold **0.30** and pinned by tests to **change no
statistic and drop no pair** — consistent with it not being a statistical property.

**The missingness rule and the contamination vocabulary** were granted in the same
entry: MISSING is MISSING (never a success, a failure, a silent exclusion, or an
outcome-dependent replacement); CLEAN / POTENTIALLY CONTAMINATED / UNKNOWN with
`UNKNOWN != CLEAN`.

### What is still genuinely missing

A definition of **who holds curator authority and independent-verifier authority**.
That office is **undefined**, there is **no appointed curator**, and no mechanism
appoints one. This is narrower than "Q5/Q6 undefined": the *rules* are authorized and
enforced; what is missing is the *role* that adjudicates individual instances and
signs off the independent regrade.

**What decision remains.** Who may (a) judge curator-side artifacts such as the frozen
`relevant_file_set` and the gold artifact, and (b) sign off the independent regrade.
It must be a role with defined powers, not a default an agent or the operator silently
inherits.

**Which later gates depend on it.** I (Q5/Q6 resolution) and the provenance story of
O. Q6's *rule* is settled and enforced; what lacks an addressee is who adjudicates a
given classification.

**Interim convention in the docs.** Where existing text says "curator-side", read it as
*"the gated side, whose adjudicating authority is undefined"*. That phrasing is flagged
for cleanup but is not a claim that a curator exists.

### 6.1 The minimum role needed to exercise Q5 / Q6 functions

Q5 and Q6 are granted as *rules*. To run, they need someone (or some role) to
adjudicate instances. That role needs **exactly four** responsibilities — no more:

| # | Responsibility | Bound by |
|---|---|---|
| 1 | Adjudicate a proposed **block exclusion** against Q5's pre-specified, outcome-independent reasons, *before* the paired outcome is seen | Q5; `f2_admission.admit_f2_task` fails closed without both bundles |
| 2 | Classify **infrastructure failure** vs genuine outcome, and rule on Q6's 30 % breach → STOP + investigate | Q6; `f2_analysis.evaluate_infrastructure_gate` |
| 3 | Own **contamination classification** (CLEAN / POTENTIALLY CONTAMINATED / UNKNOWN, with `UNKNOWN != CLEAN`) | AUTH-018 vocabulary |
| 4 | Sign off the **independent regrade**'s provenance | step O |

**Explicitly outside this role** — to keep it from becoming a licence to bend results:

- it may **not** exclude a block because T won, X won, or a result is inconvenient;
- it may **not** alter N, b, c, `π_d`, alpha, the confidence level, the McNemar
  method, the Q6 ceiling, or T/X/C0 semantics;
- it may **not** reset the Q6 denominator or discard infrastructure failures;
- it may **not** convert MISSING into pass/fail or trigger an outcome-dependent
  replacement;
- it may **not** be the agent implementing the code, and **not** the operator who
  owns the run, without an explicit, recorded separation of duties.

The last point is the substance: the person authorizing the run must not also be the
sole judge of that run's exclusions. Recording that separation is the whole of the
missing governance decision.

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

---

## 9. The execution order, as a dependency chain

Steps B-P of §2 in strict order. **The order is dependency-driven and must not be
rearranged to make the system appear READY.** Column "gate" names what the step
UNLOCKS; a step whose gate is closed cannot be started, however ready its
mechanism is.

| # | Step | § | Owner | Gate that must be satisfied first | Unlocks |
|---|---|---|---|---|---|
| 1 | Population authorization | B | experiment authority | an explicit authorization exists (AUTH-013 does not grant it) | step 2 |
| 2 | Curator-office decision | I | experiment authority | none - can run in parallel with 1 | steps 9, 15 |
| 3 | Operator environment provisioning | D/A | operator | step 3 only needs step 1 for the artifact-store location | steps 4-8 |
| 4 | Receipt-key provisioning | E | operator | step 3 | step 8, 13 |
| 5 | Privileged Q9 enforcement | F | privileged administrator | none - can run in parallel | step 6 |
| 6 | Q9 attestation | G | operator | **step 5** (probes must run under real enforcement) | steps 8, 9, 13 |
| 7 | Conversion provenance | D | operator | base revision resolved + adapter chosen (see §3.6) | preflight |
| 8 | Genuine ACTIVE lesson | H | operator | steps 1, 4, 6 | steps 9, 10 |
| 9 | S8 base/gold evidence | J | external evidence source | steps 1, 3, 6 | step 10 |
| 10 | **preflight** | K | repository | steps 3-9 all closed; expects **READY**, exit 0 | step 11 |
| 11 | Q10 authorization | L | operator | step 10 == READY | step 12 |
| 12 | Q10 calibration | L | operator | step 11 | step 13 |
| 13 | Q12 genuine learning event | M | operator | step 12 | step 14 |
| 14 | Q13 confirmatory F2 | N | operator | step 13 + step 2 (curator signs exclusions) | step 15 |
| 15 | Frozen statistical analysis / regrade | O/P | repository + operator sign-off | step 14 | final package |

**Three of these are hard external boundaries that no repository change can pass:**
step 1 (experiment authority), step 5 (Administrator), and steps 7-9 (evidence that
does not exist on this host).

**Steps 5 and 2 are independent of everything else** and can be started
immediately, in parallel, because nothing gates them. They are also the two that
unblock the most downstream work.

### The critical path

`
1 population authorization ──┬─> 2 curator office ──────────────┐
                             ├─> 3 env ─> 4 receipt key ─┐      │
                             │                          ├─> 8 ACTIVE lesson ─┐
5 privileged Q9 ─> 6 attest ┴──────────────────────────┘                    ├─> 10 preflight
                             └─> 7 conversion ──────────────────────────────┤      │
                                                                             │      ↓
                                                          9 S8 evidence ─────┘   11 Q10 auth
                                                                                     ↓
                                                                    12 Q10 ─> 13 Q12 ─> 14 Q13 ─> 15 regrade
`

**Shortest real distance to READY:** steps **1**, **5→6**, **3→4**, **7**, **8**,
**9**. Step 10 then reports READY — and only then may step 11 be authorized.
