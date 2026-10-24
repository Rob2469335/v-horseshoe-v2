# F2 S8 Execution Boundary and Feasibility-Pilot Package

**Status: PROPOSED — NOT EXECUTED — REQUIRES ROB'S AUTHORIZATION.**
Nothing in this document has been run. No VM was created, started, attached or
reconfigured; no task code was executed; no network or security control was
changed. Every command below is marked `NOT EXECUTED`.

Baseline: `b84faae38cca6574a4de90520d8afd073142000b`.

---

## 1. Why the boundaries exist

Two environments, deliberately different, because they have opposite trust
requirements:

| | **Acquisition/build boundary** | **Offline evaluation boundary** |
|---|---|---|
| Purpose | resolve pips/checkouts/build deps for a declared task | run base and gold test commands and retain evidence |
| Trust | runs upstream build scripts → **disposable, no secrets, no experimental network** | runs task test code → **no internet, no secrets, pinned inputs only** |
| Internet | only if separately authorized | **none** |
| Secrets / host credentials | none | none |
| Shared host folders | none writable by task code | none |
| Route to `10.72.0.0/24` | **prohibited** | **prohibited** |
| Authority to mark CLEAN / admitted | none | none |
| Lifetime | created → build → export artifacts → destroyed | created → evaluate → export evidence → destroyed |

The existing F2 internal network (`10.72.0.0/24`) and the existing
`F2-Isolation-VM` are **not** part of this design. An internet-reachable build
runner must never be joined to that network.

**No isolation claim is made.** A VM is not secure because it is a VM. Isolation
is `NOT ESTABLISHED` until measured on this host under an authorized procedure
(AUTH-032 records the host↔guest channels as disabled; that is a *configuration
statement*, not a measurement of the proposed new environment).

---

## 2. Measured capacity (read-only, this session)

| Item | Value | Measured |
|---|---|---|
| `C:` free | **171.77 GB** (159.97 GiB) | 2026-10-10 |
| RAM total / free | **31.43 GB / 8.83 GB** | 2026-10-10 |
| CPU | Core Ultra 5 135U, 12C/14T | 2026-10-10 |
| `data\f2_population` | 19.45 GB / 3,609 files | 2026-10-10 |
| `F2_EVIDENCE` | 0.178 GB / 7,404 files | 2026-10-10 |

Per-task storage (decimal GB, low/central/high): clone 0.20/1.00/3.00 ·
env+deps 0.50/2.00/6.00 · wheelhouse 0.30/1.00/3.00 · JUnit 0.01/0.05/0.20 ·
logs 0.01/0.05/0.10 · evidence 0.01/0.05/0.10 · temp 0.20/1.00/3.00 →
**1.24 / 5.15 / 15.40 GB per task**.

**Recommendation: ONE VM, sequential task execution.** Two concurrent 4 GB
guests do not fit the measured 8.83 GB free RAM once host OS, Hyper-V, the
resident model services (:8080–:8083, Qdrant :6333) and peak build memory are
counted. Sequential single-guest execution is the defensible capacity choice:
it needs no host-memory headroom analysis beyond ~4–6 GB for the guest plus a
scratch disk, and it removes cross-task contamination entirely.

Batch sizing at 5.15 GB/task central: a 150 GB scratch allocation supports ~23
tasks after a 20 % margin (see the OS memo §8 for the full table). At 3,828
evaluator-compatible tasks, 300 pairs needs ~13 batches at that size — or fewer
with a larger scratch volume.

---

## 3. Resource limits and timeouts (proposed)

Estimates, not measurements — no task has been run.

| Phase | Limit | Rationale |
|---|---|---|
| acquisition (network) | 15 min | wheelhouse + repo tarball only |
| checkout | 5 min | blobless/shallow; no history walk |
| dependency install | 20 min | wheels preferred; see §5 |
| base test run | 20 min | pytest on the declared node ids |
| gold-patched run | 20 min | same |
| artifact export | 5 min | JUnit + logs, size-capped |
| **total wall clock / task** | **90 min** | stop-and-classify, never auto-retry into a pass |
| CPU | 4 vCPU | leaves host headroom |
| RAM (guest) | 8 GB | peak build working set |
| disk (guest) | 60 GB dynamic | clone+deps+cache |
| processes | 512 | fork-bomb ceiling |
| output size | 256 MB | JUnit + logs, hard cap |

**A timeout, crash, missing artifact or infrastructure failure is never a
scientific outcome.** Such a task is recorded as
`infrastructure_failure_not_an_outcome` and is excluded from the analysis
population with that reason — it is not a fail, not a pass, and not a pair.

---

## 4. Evidence transport

1. Guest writes JUnit XML + a run log + a JSON descriptor to its scratch disk.
2. The descriptor is wrapped in the existing envelope (`f2_s8_record`,
   `F2S8REC1`, 48-byte header, SHA-256, 1 MiB cap) and read host-side by
   `qwen_train.f2_s8_channel.read_channel_file`.
3. The channel validates **transport** only: bounded read, magic, version,
   length cap, digest, UTF-8, JSON, strict schema, identity, replay.
   It can no longer assert `completed`/`failed` (see §6).
4. The JUnit file itself is evaluated **only** by
   `qwen_train.f2_evaluator.produce_report`, whose identity is bound to the
   module's own bytes by `implementation_digest()`.
5. Admission remains `qwen_train.f2_evidence.verify_task_evidence` +
   `f2_governance`. **A valid transport record is not evidence.**

**Known limitation:** the guest-written-disk path has been tested only against
synthetic fixtures. A hostile guest-written disk has **not** been tested live
(`NOT ESTABLISHED`).

---

## 5. Dependency policy and its hard limits

A Python wheelhouse covers pure-Python and wheel-published native deps. It
**cannot** cover: system packages, C/C++ toolchains and headers, database
servers, browser binaries, JVM/Node/Rust/Dart ecosystems, or anything the task's
`install_config` requests via `apt-get`/`yum`/`choco`.

Measured on the pinned population: only **3,870 of 9,268** metadata-eligible
tasks are evaluator-compatible at all (pytest command + pytest node ids), and
**3,828** of those are Python-confirmed (every declared node path ends `.py`).
The other 5,398 metadata-eligible tasks are excluded by the evaluator contract,
not by preference — see §7.

The proposed pilot is therefore **Python + pytest only**, and its eligibility
rule excludes every task whose declared command is not a pytest invocation or
whose declared node ids are not pytest node ids.

### 5.1 Offline build backends are part of the wheelhouse contract (measured 2026-10-15)

A wheelhouse must also carry the **build backend** of every task whose declared
command is `uv run …`. Measured live on the two uv tasks
(`openai__openai-agents-python-1633/1636`): `uv run pytest` reaches the *build*
step before running any test, and with the network disabled uv resolves the build
requirements **only** from its cache or from `--find-links`. The guest has no uv
cache, so the tasks failed with:

```
× Failed to resolve requirements from `build-system.requires`
╰─▶ No solution found when resolving: `hatchling`, `editables~=0.3`
    hint: Packages were unavailable because the network was disabled.
```

`pyproject.toml` declares `[build-system] requires = ["hatchling"]`; the EDITABLE
install additionally needs `editables~=0.3`. Both, plus their transitive deps
(`packaging`, `pathspec`, `pluggy`, `tomlkit`, `trove-classifiers`), are delivered
into `C:\f2_tooling\wheelhouse`, and the pilot sets **`UV_FIND_LINKS`** to that
directory alongside `UV_OFFLINE=1` (`f2_s8_pilot_live.WHEELHOUSE`, `per_task_env`).
`UV_FIND_LINKS` is documented as *"Equivalent to the `--find-links` command-line
argument … additional locations to search for packages"*
(https://docs.astral.sh/uv/reference/environment/).

Verified in-guest after delivery, offline: base tree → declared `FAIL_TO_PASS`
node **FAILS**; gold tree → **passes**; both write a JUnit. Wheel digests are
recorded in `F2_EVIDENCE\s8\pilot_deliver\DELIVERY_MANIFEST_EXTENSION_wheelhouse.json`.

---

## 6. S8 channel change delivered with this package

**Defect (reproduced):** a channel payload with `status: "completed"` (or
`"failed"`) and **no** execution block validated as OK — a transport record
could assert a finished execution with zero execution evidence.

**Fix:** `validate_payload` now refuses any outcome status with
`OUTCOME_STATUS_NOT_TRANSPORTABLE`. Lifecycle-only states
(`planned`, `attempted`, `rejected`, `not_established`) remain transportable.
Outcome evidence travels only through `f2_evidence` with retained artifacts.

**Regression proof:** with the guard removed, the two new tests fail
(`assert 'OK' == 'OUTCOME_STATUS_NOT_TRANSPORTABLE'`); with it restored, all
pass. The envelope wire format (`f2_s8_record`) is unchanged.

---

## 7. Evaluator contract (read from `f2_evaluator.py`, not assumed)

| Property | Behaviour |
|---|---|
| Runner | **pytest only.** `augment_test_command` appends `--junitxml=<path>` and **refuses** a command that already contains `--junitxml`. |
| Node ids | `nodeid_to_junit_identity` **raises** unless the id is `<path>::[<Class>::]<test>` with non-empty segments. Applies to **FAIL_TO_PASS and PASS_TO_PASS**. |
| Verdict | `pass` **only** if every declared FAIL_TO_PASS node appears with status `passed`. A resolved-but-cheating run cannot pass: a node absent from the JUnit is `missing`. |
| PASS_TO_PASS | evaluated and reported, but never changes `declared_result`. |
| Fail-closed | malformed XML, duplicate emitted identity, unresolved declared node id, empty FAIL_TO_PASS → non-passing outcome. |
| Identity | `evaluator_id=f2_pytest_junit_evaluator`, `version=1.0.0`, `implementation_digest()` = SHA-256 of the module bytes, `procedure_id=f2_pytest_junit_identity_v1`, `protocol_version=f2_experiment_j_v1`. |
| Binds | report digest over its own canonical content; `execution_state_identity` recorded but never affects the verdict. |

**Consequence:** `SWE-rebench-V2` declared outcomes are log-parser identifiers,
not pytest node ids, so rebench tasks generally raise in
`nodeid_to_junit_identity` → **not evaluator-compatible**. Of the 3,870
evaluator-compatible metadata-eligible tasks, 1,800 are `swe-bench-live` and
2,070 `swe-rebench-v2` (rebench tasks whose declared ids happen to be pytest
node ids, e.g. Python repos).

---

## 8. Five-task feasibility pilot (PROPOSED — NOT EXECUTED)

**Eligibility predicate (outcome-independent, no run outcome consulted):**

> metadata-eligible (every screen except S8 passes) **and** `test_cmd` matches
> `\bpytest\b` with no existing `--junitxml` **and** every declared FAIL_TO_PASS
> and PASS_TO_PASS entry is `<path>::[<Class>::]<test>` with non-empty segments
> **and** every declared node path ends `.py` **and**, within that set, the
> first five in registered order (`created_at` desc, `instance_id` asc).

**Selected tasks (deterministic; positions 1–5 of the PROPOSED registration):**

| # | instance_id | created_at | source |
|---|---|---|---|
| 1 | `openai__openai-agents-python-1636` | 2025-09-02T09:55:04 | swe-bench-live |
| 2 | `openai__openai-agents-python-1633` | 2025-09-02T01:10:29 | swe-bench-live |
| 3 | `HypothesisWorks__hypothesis-4532` | 2025-09-01T08:37:47 | swe-bench-live |
| 4 | `python__mypy-19767` | 2025-08-31T12:59:36 | swe-bench-live |
| 5 | `stanfordnlp__dspy-8739` | 2025-08-30T07:39:31 | swe-bench-live |

These are **not** chosen for a known outcome; they are the head of a
deterministic order. They span materially different dependency profiles
(agent framework, property-based testing with native extras, a compiled mypy
build, a DSPy/LLM-stack project) — deliberately not five trivial pure-Python
packages. The rule may be re-run by anyone against the same pinned inputs.

**Measurements to take:** environment build success · baseline test validity ·
gold-patch evaluation · whether the intended tests actually ran · JUnit parsing
by the pinned evaluator · evidence integrity (digest round-trip) ·
reproducibility (same task twice) · wall clock per phase · peak RAM/disk ·
isolation enforcement · offline dependency availability · infrastructure
failure rate.

**Stop conditions (any one stops the pilot):** isolation cannot be demonstrated ·
evidence integrity fails · the evaluator cannot distinguish a failed run from a
missing one · a task exceeds its resource limits · unexpected host access
occurs · scientific validity of a result is uncertain.

**What five tasks cannot establish:** no yield, no confidence interval, no
generalisation to the other 3,823 compatible tasks, no statistical power, no
T-vs-X conclusion. Five is a feasibility probe, not a sample.

**Authorization text requested (pilot):**
> "I authorize executing the five named F2 feasibility-pilot tasks above, in a
> disposable isolated environment that has no route to `10.72.0.0/24` and no
> host secrets, using only the pinned inputs at the recorded digests. I
> understand real task code will execute inside that environment, that this is a
> feasibility probe only, and that it authorizes no confirmatory F2 run and no
> expansion to thirty tasks."

**Commands (NOT EXECUTED):**
```powershell
# 1. build the disposable acquisition/build guest            (NOT EXECUTED)
# 2. pre-fetch wheels for the five task requirements         (NOT EXECUTED)
# 3. per task, inside the offline evaluation guest:          (NOT EXECUTED)
#      git clone --filter=blob:none <repo> && git checkout <base_commit>
#      pip install -r <requirements>   (offline, pinned wheelhouse)
#      <test_cmd> --junitxml=base.xml                       # expect FAILURE
#      git apply <gold_patch>
#      <test_cmd> --junitxml=gold.xml                       # expect PASS
# 4. host-side: read the record with f2_s8_channel.read_channel_file   (NOT EXECUTED)
# 5. host-side: python -m qwen_train.f2_evaluator ...        (NOT EXECUTED)
```

---

## 9. Thirty-task follow-up (SEPARATE grant — NOT EXECUTED)

Requires a separate authorization **after** the five-task report is reviewed.
It does not follow automatically. Same predicate, next 25 tasks of the same
deterministic order (positions 6–30), same environment and limits.
**No automatic expansion.** The 30-task pilot does not authorize the
confirmatory experiment.

---

## 10. Feasibility arithmetic (ARITHMETIC PROJECTIONS ONLY)

| Stage | Count | Basis |
|---|---|---|
| raw source rows | 33,967 | pinned acquisitions |
| accepted | 33,770 | union + dedupe |
| metadata-eligible | 9,268 | S1–S10 with S8 absent |
| **evaluator-compatible** | **3,870** | pytest cmd + pytest node ids |
| Python-confirmed compatible | 3,828 | every declared node path ends `.py` |
| S8-complete | 0 | no execution has occurred |
| admitted / analyzable | 0 / 0 | — |

For 300 analyzable pairs: at 25 % yield → 1,200 tasks needed; 50 % → 600; 75 % →
400. **All three are inside the 3,828-task compatible population**, so the
population is arithmetically sufficient; the binding constraints are S8
evidence production, evaluator/timeout behaviour, and infrastructure — none of
which are yet measured. **These are arithmetic projections, not observed
yields, and they do not establish that 300 pairs are achievable.**

---

## 11. Reproducible Preflight Command & Evidence Paths

Preflight executes with strict fail-closed verification against actual captured evidence:

```powershell
.\.venv\Scripts\python.exe -m qwen_train.f2_preflight `
    --json `
    --attestation "C:\Users\rober\F2_EVIDENCE\s8\isolation_run_2026-10-15\attestation_reval_bound.json" `
    --enforcement-capture "C:\Users\rober\F2_EVIDENCE\s8\isolation_run_2026-10-15\enforcement_capture_revalidate.json" `
    --conversion-record "C:\Users\rober\F2_EVIDENCE\s8\conversion\conversion_record.json"
```

The 2026-10-15 pair above is the current *bound* attestation/capture: the two files are
ONE binding unit. The attestation records the capture's **canonical policy digest**, and
the host verifier recomputes that digest from the capture file, so an old attestation
paired with a new capture is refused rather than silently accepted.

**Digest kinds are not interchangeable — never compare values across columns.**

| Artifact | Bytes | File SHA-256 | Canonical digest (its own binding identity) |
|---|---|---|---|
| `isolation_run_2026-10-15\attestation_reval_bound.json` | 3543 | `38897b8b4316ab6f124893844a45ac1d9ea6bc4bf8192e961bbabd3c033f06d7` | — (claims the capture's canonical digest) |
| `isolation_run_2026-10-15\enforcement_capture_revalidate.json` | 3733 | `8a786ff7895e248d06722a45f9b18b1e29d2defbbb4dbbeccd65c123396643fe` | `ef9dd5ca86bf537332be862aa9cc96824b49d647c2622604fae3b33f6e612d24` |
| `conversion\conversion_record.json` | 1146 | `f1d7cb99ddbe2e4c35677ee275f97db0d4da3ec30d10381957f3381755fb0f81` | — |
| `qwen_train/curriculum/f2_registration/registration_manifest.json` | 1939 | `60c4af6789017acbde40aa640cfafe248d3f5ce0f0e3a484f64cee6abad1af01` | `5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042` (`registration_content_sha256`) |

A capture's **file** SHA-256 is never its **canonical** digest, and
`canonical_policy_digest` / `canonical_enforcement_digest` include `captured_at`, so a
re-capture of *unchanged* rules yields a *different* canonical digest. Use
`f2_isolation.canonical_rules_digest()` — the rules-only digest — to prove **policy
equivalence**:

- **2026-10-13 captures: 12 rules** (no IPv6 rules at all).
- **2026-10-14 → present: 19 rules** — seven IPv6 rules were added on 2026-10-14,
  including `Outbound Deny ::/0 w=340`. Adding them is what moved `ipv6` from
  `NOT ESTABLISHED` to *enforcement-backed*.
- 2026-10-14 → present: rules digest stable at
  `7eb584eafec098839a8b31917b75a03087882aeb11ec3adeee72072632bb99fa`.

The older `isolation_run_2026-10-13` pair (attestation `1e6e3e91…`, capture canonical
digest `94373017…`, 12 rules) is therefore a **historical pairing superseded by the IPv6
rules**, not the current one. Do not mix the 2026-10-13 attestation with a 2026-10-15
capture: the binding check is designed to refuse exactly that.

---

## 12. Verified Model & Artifact Manifest

| Component | Identifier / Path | Size (Bytes) | SHA-256 Digest | Status |
|---|---|---|---|---|
| Served GGUF (adopted) | `qwen_train/robs4b_q4km_reconverted.gguf` | 2,708,804,032 | `3e5ba031f0b50e98450d1575aa19780bc5b4b13f48bf3eca38f3c915a442a337` | `PROVEN` |
| Original GGUF (preserved, **not** served) | `qwen_train/robs4b_q4km.gguf` | 2,708,803,840 | `65202f372110dde854b40ce15dcd1b6ab56a1fe9ea542b84b6a9cc745b242d41` | `PROVEN` |
| LoRA Adapter Config | `qwen_train/robs4b_final_adapter/adapter_config.json` | 1,228 | `294feecc592b5a7abca7913dcb58ea797e62c2e3fe3a42abf3fe5e59db506cfd` | `PROVEN` |
| Base Model Identity | `Qwen3.5-4B` (snapshot `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a`) | — | Declared in adapter config | `PROVEN` |
| Training Corpus | `qwen_train_data/robs4b_mixed_final.jsonl` | 1,472,356 | Metadata verified from `training_runs.jsonl` | `PROVEN` |
| Intermediate FP16 Weights | `Robs4B_Merged_Hf` | — | Produced by the witnessed reconversion (operator-authorized 2026-10-14) | `PROVEN` |
| Conversion Link | `adapter -> merged -> GGUF` | — | Witnessed reconversion recorded in `conversion_record.json` (llama.cpp `b11624`, `--no-mtp`, Q4_K_M) | `PROVEN` |

`SERVED_GGUF_SHA256` in `qwen_train/f2_preflight.py` and `EXPECTED_GGUF_SHA256` in
`swarm_os/services/experiment_model_identity.py` both bind to the adopted artifact
(`3e5ba031…`). The two artifacts share a basename stem but are distinct bytes and must
never be conflated; the original stays untouched, exactly as `HANDOFF §3.4` requires.

---

## 13. Deterministic Pilot Launch Command & Environment

The pilot execution entry point is `qwen_train.f2_s8_pilot_live`. It requires all 8 authorization gates to evaluate to `True`:

```powershell
# Sourcing operational environment variables (see docs/EXPERIMENT_J_F2_OPERATIONAL_ENV_SPECIFICATION.md)
$env:SWARM_DISTILLER_MODEL = "robs4b"
$env:SWARM_DISTILLER_WEIGHTS_DIGEST = "3e5ba031f0b50e98450d1575aa19780bc5b4b13f48bf3eca38f3c915a442a337"
$env:SWARM_F2_EVALUATOR_ID = "f2_pytest_junit_evaluator"
$env:SWARM_F2_EVALUATOR_VERSION = "1.0.0"
$env:SWARM_F2_EVALUATOR_PROCEDURE = "f2_pytest_junit_identity_v1"
$env:SWARM_F2_EVALUATOR_IMPL = "C:\Users\rober\Projects\v-horseshoe-v2\qwen_train\f2_evaluator.py"
$env:SWARM_F2_ARTIFACT_ROOT = "C:\Users\rober\F2_ARTIFACTS"
$env:SWARM_F2_ARTIFACT_RETENTION_DAYS = "365"
$env:SWARM_F2_EMIT_BUNDLE = "1"
$env:SWARM_F2_TASK_OUTCOME_REPORT = "C:\Users\rober\F2_ARTIFACTS\pilot_task_outcome_report.json"

# S8-only governance inputs (no F2 equivalent -- see qwen_train/f2_s8_compose.py
# S8_GOVERNANCE_ENV / S8_ENV_FALLBACKS).  The six shared inputs above are read
# through their established F2 names; these three have NO fallback and must be
# supplied or load_governance_from_env() fails closed, naming each one.
$env:SWARM_S8_ENVIRONMENT_IDENTITY = "<operator-supplied execution-environment identity>"
$env:SWARM_S8_BASE_STATE_DIGEST     = "<sha256 digest of the base execution state>"
$env:SWARM_S8_GOLD_STATE_DIGEST     = "<sha256 digest of the gold execution state>"

# Launch execution command (PROPOSED — NOT EXECUTED — REQUIRES ROB'S EXPLICIT AUTHORIZATION)
.\.venv\Scripts\python.exe -m qwen_train.f2_s8_pilot_live `
    --manifest "C:\f2_pilot\build_manifest.json" `
    --inputs "C:\Users\rober\F2_EVIDENCE\s8\pilot_inputs\full" `
    --registration-id "evaluator_compat_python_confirmed_v1" `
    --registration-sha256 "5f3c9f47eb622d7d983cfdcb6ebd3d240f6936087a049781c741ba37e237f042" `
    --ledger "C:\Users\rober\F2_EVIDENCE\s8\trusted_store\s8_pilot_ledger.jsonl" `
    --staging "C:\Users\rober\F2_EVIDENCE\s8\staging" `
    --out "C:\Users\rober\F2_EVIDENCE\s8\pilot_results" `
    --vm "F2-S8-VM1" `
    --credential "C:\Users\rober\F2_EVIDENCE\s8_cred.xml" `
    --gates "C:\Users\rober\F2_EVIDENCE\s8\pilot_gates.json"
```

**Required Gate JSON Structure (`C:\Users\rober\F2_EVIDENCE\s8\pilot_gates.json`):**
```json
{
  "authority_current": true,
  "d4_budget_approved": true,
  "d4_storage_cap_approved": true,
  "approved_subnet": true,
  "d5_credential_available": true,
  "evaluator_identity_named": true,
  "isolation_verified": true,
  "pilot_authorized": true
}
```

---

## 14. Pilot Pre-Flight Checklist (Isolation & Authorization)

Before any command touches a live VM or executes task code:
1. **Explicit Owner Authorization:** Verify formal written grant for the 5 named tasks.
2. **Isolation Resolution:** Confirm either active IPv6 host ACL enforcement or formally signed Path B administrative waiver.
3. **VM State Inspection:** Confirm `F2-S8-VM1` is cleanly powered off before session initiation.
4. **Network Verification:** Confirm internal switch `F2-S8-NAT` has no route to `10.72.0.0/24` and `w=350` Outbound Deny rule is active.
5. **Tooling & Delivery Package:** Validate `DELIVERY_MANIFEST.json` hashes in `C:\Users\rober\F2_EVIDENCE\s8\pilot_deliver`.
6. **Secret Provisioning:** Verify `SWARM_RECEIPT_KEY` presence in session scope only (never printed or logged).

---

## 15. Result Collection, Integrity Verification & Rollback

- **Transport Envelope Validation:** Handled host-side by `qwen_train.f2_s8_channel.read_channel_file` (`F2S8REC1`, SHA-256 integrity check, 1 MiB cap).
- **Independent Evaluator Report:** Evaluated by `qwen_train.f2_evaluator.produce_report`.
- **Governed Admission:** Evaluated by `qwen_train.f2_s8_compose.produce_governed_task`, checking base=FAIL and gold=PASS.
- **Stop & Rollback Conditions:**
  - Any observed network reachability halts execution immediately.
  - Infrastructure failure, timeout, or missing artifact records `infrastructure_failure_not_an_outcome` and excludes the task from analysis.
  - Frozen scientific criteria, eligibility rules, and verifier semantics are never altered during or after the pilot.
- **Strict Boundary Firewall:** Feasibility pilot results serve solely to validate infrastructure, evaluator plumbing, and delivery mechanics; they authorize neither confirmatory F2 execution nor automatic expansion.

---

## 16. Session 39–40 evidence reconciliation: the execution path is proven; two tasks are scientifically unresolved

**Status: OBSERVED / independently reconciled. No scientific criterion was changed, and no ledger row was rewritten.**

### 16.1 Execution-path milestone (`PROVEN`)
Two consecutive registered tasks exercised the complete workflow —
selection → acquisition/validation → build → identity verification → delivery → guest execution →
evaluation → independent re-derivation → governed ledger append:

| task | registered pos. | build | delivery | guest pilot | total wall |
|---|---|---|---|---|---|
| `django-guardian__django-guardian-899` | 8 / 3828 | 81 s | 12 s (zip 42 MB) | 79 s | ≈180 s |
| `django-guardian__django-guardian-896` | 9 / 3828 | 90 s (3+29+28+30) | 16 s (zip 42 MB) | 38 s | ≈247 s |

Per-task artifacts (patches, ids, identities, JUnits, reports) are retained under
`F2_EVIDENCE\s8\slice1\`. **A functioning execution path is not scientific success and is not
admission readiness** — the distinction matters and is recorded here deliberately.

### 16.2 Task dispositions (unchanged from their ledger rows)
| task | ledger | outcome | class |
|---|---|---|---|
| 899 | seq 7, `record_digest 493b7557…` | `gold_fail` | scientific, unresolved |
| 896 | seq 8, `record_digest 5a1662b9…` | `gold_fail` | scientific, unresolved |
Both are **terminal scientific records** in this chain and must not be re-scored here.

### 16.3 Missing declared node — verified, and the cause
For BOTH tasks and BOTH sides, re-derived from the retained JUnit bytes:

| task | side | testcases collected | declared node present | testcases from the declared module |
|---|---|---|---|---|
| 899 | base / gold | 15 / 15 | **False / False** | **0 / 0** |
| 896 | base / gold | 16 / 16 | **False / False** | **0 / 0** |

The declared `PASS_TO_PASS` for these tasks is 293 / 308 entries, so **the suite was largely not
collected at all**. The evaluator's `missing` status is therefore factual, not a defect: a declared
node absent from the JUnit is `missing`, and the pair cannot resolve.

**Classification vocabulary (do not conflate).**
* **node absent from collected JUnit (`missing`)** — *this case* (`PROVEN` from the JUnit bytes).
* **test failing after execution** — the node ran and reported failure (this is a real gold regression).
* **test collection failing** — pytest reports a collection error.
* **infrastructure failure** — no usable JUnit / command crash (`no_artifact_reported`, `worker_crash`).
* **scientifically unresolved** — the declared pair did not establish base-fail + gold-pass.
`gold_fail` here is a *scientifically unresolved* outcome; it is **not** an infrastructure failure and
**not** a build/delivery/isolation/ledger defect.

**Cause of the truncated collection (`INFERRED`, not proven).** These are database-backed Django
tests and the declared command is the bare `pytest -rA` with no Django settings/database
configuration, so collection omits most of the suite. The retained artifacts prove the *effect*
(0 testcases from the declared module) but not the *reason*; proving the reason would need the
retained guest-side stdout/stderr or an explicit collection-error entry.

### 16.4 Population-level uncertainty — OPEN research item (`NOT ESTABLISHED`)
> **How many of the 3,638 selectable tasks declare test nodes that depend on databases or services
> not configured in the approved guest environment?**

Two observed tasks establish nothing population-wide. No yield percentage is recorded. A future
authorized investigation would need: a declared-node inventory method; job classification evidence
(imports, fixtures, settings, service calls) at plan time, not run time; explicit uncertainty
handling for unclassifiable tasks; independently reproducible counts; and a *selection-independent*
scope so the classification cannot bias which tasks are attempted. **Do not provision services or
alter the guest test environment for this.**

### 16.5 Measured optimization opportunity (`PROVEN` observation)
`build identity` cost ≈23–30 s/task, dominated by re-hashing the two corpora (~3 GB total) to compute
provenance digests. Any cache must be keyed by *verified content digest* (plus file metadata) and must
never replace content verification; it is **not implemented** — this pass records the opportunity only.

### 16.6 Operational measurements (measurements, not a forecast)
Totals: 180 s (899) and 247 s (896); workspace 98.5 MB for both. **Two tasks are insufficient** to
estimate throughput or scientific yield. The `mypy-19767` outlier (2694 s) remains part of the
distribution and must not be excluded when a real distribution is eventually computed.

---

### 16.7 (Session 41) — the missing-node CAUSE is PROVEN, and a builder defect was fixed

**Retained traceback (`message="collection failure"`, first `<error>` element of both sides of both tasks):**
```
guardian\testapp\tests\test_admin.py:10: in <module>
    from django.contrib.contenttypes.models import ContentType
...
E   django.core.exceptions.ImproperlyConfigured: Requested setting INSTALLED_APPS,
    but settings are not configured. You must either define the environment variable
    DJANGO_SETTINGS_MODULE or call settings.configure() before accessing settings.
```
JUnit shape: **899 → tests=15, errors=15; 896 → tests=16, errors=16** — *every* collected entry is an
`<error>`, and the declared module contributes **0**. So this is **`collection failure`**, proven —
not "the gold patch did not fix the test".

**Chain of evidence (`PROVEN`):**
1. the project's own `pyproject.toml` declares
   `[tool.pytest.ini_options] DJANGO_SETTINGS_MODULE = "guardian.testapp.testsettings"`, a key
   honoured **only by `pytest-django`**;
2. it declares `pytest-django>=4.9.0` in `[dependency-groups] dev` (**PEP 735**);
3. the built environment contained `django` (installed by `-e .`) but **not `pytest_django`**
   (`False` in both `.venv` and `.venv_gold`) — the builder installed only `pytest` + `-e .`;
4. therefore pytest ignored the ini key, the framework raised at import time, collection failed,
   every declared node was absent, and the pair was unresolved.
The earlier Django/database hypothesis is **refuted as stated** (settings *are* declared); the cause
is a **missing declared pytest plugin in the build**.

**Defect fixed (builder, not science).** `qwen_train/f2_s8_acquisition.declared_test_plugins()` reads
the project's **own** declared metadata and selects only members whose distribution starts with
`pytest-` (deliberately narrow: a `dev` group also names mkdocs/mypy/ruff/pre-commit, which must not
be installed). `qwen_train/f2_s8_pilot_build.py` now installs those plugins for both sides. Nothing
invented; the declared command, patches and node lists are unchanged, and the build identity already
covers `[dependency-groups]`/extras, so the change is identity-visible.
**End-to-end validation of the fix requires the next authorized pilot; none was started here.**

### 16.8 (Session 41) — the 185 registration conflicts, classified from the pinned corpora

Reproduced by streaming both corpora and comparing the **canonical source-adapter view**:

| count | class |
|---|---|
| **156** | cross-source; canonical fields disagree: `_patch`, `language`, `pass_to_pass`, `test_cmd` |
| **39** | cross-source; also disagree on `fail_to_pass` |
| **11** | same disagreement but **outside** the operative registration |
| **1** | same-source duplicate (the known `conan-io__conan-18153`), disagreeing on `test_cmd` |
| **185** | **in the operative registration** (the reported figure, reproduced) |

**No class is a normalisation bug in this repository** — the upstream corpora genuinely disagree on
the gold patch, the declared language, the node lists and/or the declared command for the same
`instance_id`. **The disagreement does not leave the task unresolved.** `qwen_train/f2_population_build.py`
already defines a deterministic cross-source policy (`SOURCE_PRIORITY = ("swe-bench-live", "swe-rebench-v2")`),
and the union applies it: for a conflicting `instance_id` present in more than one source it keeps the
highest-priority source's record and records the other as `conflicting_cross_source_priority_selected`
(within-source conflicts are the branch that removes every copy).

**Reconciled 2026-10-15 (read-only, against the actual local artifacts).** All **185 of 185**
in-registration conflicts carry exactly that disposition (`kept_source='swe-bench-live'`,
`excluded_source='swe-rebench-v2'`) and the kept copy survived to screening (195 cross-source plus 1
within-source = the 196 corpus-level disagreements; minus the 10 cross-source ids outside the
registration = 185 in-registration). The earlier claim here was wrong
and is corrected — *"No existing policy names a winner ... a `GOVERNANCE GAP` requiring a rule, not a code fix"* — the rule exists (the union's `SOURCE_PRIORITY`), it is deterministic, and
it is applied to every in-registration conflict. The raw source-level disagreement remains visible
(that is what `scan_cross_source_conflicts` detects); it is **not** an open governance gap.

**Effect on the eligible population — conservative, and now labelled.** The classifier's Selectable
figure still *subtracts* the 185 (§16.9: `3,828 − 185 − 7 = 3,636`). With the disposition above that
subtraction is visibly **conservative**: the 185 are resolved registered rows, not unavailable ones.
Whether to keep excluding them from selection is a **policy choice for the owner**
(`REQUIRES AUTHORIZATION`); no population figure was changed here.

### 16.9 (Session 42) - install-plan repair, regression suite, and the offline plugin classifier over all 3,636 selectable tasks

**Implementation repair (`PROVEN IN CURRENT REVISION`).** The latest edit to `qwen_train/f2_s8_acquisition.py` was re-inspected end-to-end before any classification ran:

- `declared_test_plugins()` has exactly ONE implementation, backed by `declared_test_plugins_report()`; no early-return variant remains.
- `dependency_inputs_from_pyproject()` is the single shared extractor: `static_dependency_inputs()` parses `requirements*.txt` (comment stripping, `-r`/option-line skipping, name-only extraction - semantics unchanged) and CALLS it, so the checked-out-tree reader and the git-object reader cannot diverge. PEP 735 `include-group` entries are preserved as structured tables through extraction and recursively expanded by `resolve_dependency_groups()`, which detects cycles, missing groups, malformed entries and empty requirements as BLOCKING problems.
- every declaration is parsed exactly once per use through `packaging.Requirement` (PEP 508: version specifiers, extras, environment markers, direct references) and normalised via PEP 503 before any comparison; the duplicate re-parse of pinned/task-extra declarations inside `builder_install_plan()` was removed.
- install-plan de-duplication never silently drops a constraint: one distinct declaration passes through verbatim; compatible declarations of one distribution are ANDed into a single pip argument; provably empty version bounds, marker-vs-plain and direct-reference-vs-plain collisions raise `AcquisitionError` (FAIL CLOSED) instead of first-one-wins.
- the mechanisms stay distinct: pinned pytest baseline; task-specific extra deps; dependency groups (pip mode installs only declared `pytest-*` members; uv mode installs uv's default groups); optional extras ONLY when the contract selects them (`uv sync --all-extras`; `pip install -e .` never); plus `[project].dependencies` via the project install itself. `[build-system].requires` and `requirements*.txt` are recorded for identity but NEVER counted as installed - build isolation is discarded and this builder never runs `pip install -r`.
- builder and classifier share ONE canonical plan: `f2_s8_pilot_build.build()` takes its `plugin_requirements` from `builder_install_plan()` (the same function the classifier reads) and records the executed plan for both sides in the build manifest, so provenance equals the plan actually run. `TestPlanEquivalence` pins reader-equality and that no second hand-maintained package list returns to the builder.

**Regression coverage.** 95 tests pass across `tests/test_f2_s8_acquisition.py` and `tests/test_f2_s8_plugin_compat.py`: nested/cyclic/missing/malformed `include-group`; distribution-name normalisation; compatible and incompatible duplicate constraints; markers and direct references; optional extras selected vs not selected; `requirements*.txt` handling (including a plugin declared ONLY there, and the PEP 508 parse shared by builder and classifier); a project declaring `pytest-django` in `dev`; a declared plugin the builder does not install; builder/classifier plan equivalence; requirements-file evidence read through local git objects only; delegated-metadata precedence (a requirements-only plugin under dynamic metadata stays UNKNOWN); and fail-closed UNKNOWN whenever the dependency structure cannot be trusted. Broader F2 slice (all `tests/test_f2_s8_*` + registration + population + reconcile + doc consistency): **829 passed**. Lint (`ruff check --select E9,F`) clean on all five touched files; `compileall` clean; `git diff --check` clean. Two TEST-side corrections were required and are recorded rather than hidden: (a) the classifier tests drive real local git, so they restore the genuine `subprocess.Popen` over `tests/conftest.py`'s autouse mock using the established `_REAL_POPEN` fixture pattern (`tests/test_f2_arm_workspace_isolation.py`); (b) one assertion required the default report to include an extras-only plugin, contradicting the Session-41-corrected contract-scope rule pinned in `tests/test_f2_s8_acquisition.py`; it now asserts exclusion. Production semantics were NOT changed to satisfy either.

**Population reconciliation (every count independently reproduced from canonical inputs):**

| quantity | count | basis |
|---|---|---|
| registered ids | 3,828 | counted directly from `full_ordered_metadata.json` (sha256 `35b704b7921086853a0857e2b5bad7d020ca8edb7932e8cfca3f0e0a25421eeb`) |
| cross-source conflicts, BOTH sources | 195 | `scan_cross_source_conflicts().cross_source` streaming both pinned corpora |
| ... within a single source (all copies excluded) | 1 | `scan_cross_source_conflicts().within_source` (`conan-io__conan-18153`) |
| ... in the operative registration | 185 | 195 cross-source minus 10 outside the registration; reproduces 16.8 |
| ledger rows | 9 | read-only; sha256 `5fb0aa8eda37f220ea2fce5ca96688bb4a7edf94576b73b32b1678c1a1971430` |
| ledger DISTINCT task ids | 7 | 5 pilot tasks + 2 django-guardian; the provisional "9 recorded ledger task IDs" were ROWS - 2 are repeats (`openai__openai-agents-python-1633` and `openai__openai-agents-python-1636`, twice each) |
| overlap conflicts (in reg) AND ledger | 0 | nothing is subtracted twice |
| **selectable** | **3,636** | 3,828 - 185 - 7 + 0 = 3,636: the provisional count is REPRODUCED. The apparent discrepancy (3,828 - 185 - 9 = 3,634) came from subtracting ledger ROWS instead of distinct task ids |

**Rule v1 -> v2 (why the verdicts moved).** The first full-population run used rule `f2-plugin-compat-v1` (27 COMPATIBLE / 23 INCOMPATIBLE / 3,586 UNKNOWN, generated 2026-10-15T16:28:48Z). Adversarial review of its 27 COMPATIBLE results found two false positives: `python-telegram-bot__python-telegram-bot-4617` and `pylint-dev__pylint-9346` declare pytest plugins ONLY in root `requirements*.txt` - a file the build contract never installs (this builder runs `pip install -e .` / `uv sync`, never `pip install -r`) - which v1 never read, so their declared-plugin lists were wrongly empty. Rule v2 therefore (a) reads root `requirements*.txt` from the same pinned local git objects via `git show` (no network, no checkout, no tree execution) and the SAME `parse_requirements_names()` path the builder uses, recording them as DECLARED-BUT-NEVER-INSTALLED sources, and (b) refuses to promote a requirements-only mismatch to a verdict under delegated metadata: when `dynamic` includes `dependencies`/`optional-dependencies` (or legacy setuptools delegation applies), `setup.py` may read requirements.txt at build time, so absence is NOT provable and the task stays UNKNOWN (`requirements_plugin_under_delegated_metadata`, 11 tasks). Only the rule changed; population, builder, and ledger are untouched.

> **Superseded 2026-10-15 (owner decision).** The 185 in-registration cross-source
> conflicts are records whose operative source the existing `SOURCE_PRIORITY`
> policy already selected, so the classifier now **includes** them rather than
> subtracting them. The `selectable`/`classified` figures and every count in this
> section describe the pre-inclusion run and are retained as history; see **§16.10**
> for the recomputed result.

**Classifier result** (rule `f2-plugin-compat-v2`, generated 2026-10-15T16:57:42Z, elapsed 91.6 s, 3,636/3,636 classified; reproduced with `python -m qwen_train.f2_s8_plugin_compat`):

| class | count | % |
|---|---|---|
| OBSERVED_COMPATIBLE | 25 | 0.688 |
| OBSERVED_INCOMPATIBLE | 25 | 0.688 |
| UNKNOWN | 3,586 | 98.625 |
| NOT_APPLICABLE | 0 | 0.00 |

- COMPATIBLE: 20 declare no pytest plugin at all; 5 declare plugins the plan DOES install: `pypa__cibuildwheel-2458`, `apify__crawlee-python-1181`, `sooperset__mcp-atlassian-298`, `lss233__kirara-ai-1445`, `openai__openai-agents-python-357` - the class the Session-41 defect broke, still intact under v2.
- INCOMPATIBLE (all swe-bench-live): 72 declared-plugin mismatch notes across 25 tasks: 21 tasks whose plugins live ONLY in an optional extra the build contract does not select, plus 4 whose plugins live only in `requirements*.txt` under complete static metadata (`dbcli__mycli-1238`, `python-telegram-bot__python-telegram-bot-4617`, `geopandas__geopandas-3240`, `pylint-dev__pylint-9346`; mycli's and geopandas's notes also record the unselected extra). Note split: 62 extras-only + 10 requirements. Top plugins: pytest-cov 18, pytest-xdist 11, pytest-mock 8, pytest-asyncio 6, pytest-timeout 6. Task ids are listed in the report JSON.
- UNKNOWN reasons (per task; they sum exactly to 3,586): `project_tree_unavailable` 3,527 (no local clone - missing evidence, never a verdict); `project_table_absent` 33; `pyproject_absent` 14; `requirements_plugin_under_delegated_metadata` 11 (requirements-file plugins whose absence is NOT provable under delegated metadata - see the v1 -> v2 paragraph); dynamic/delegated dependency metadata 9 tasks (21 co-occurring codes); `base_commit_object_unavailable` 2; `project_root_not_found` 1. No UNKNOWN is ever counted compatible.
- by source: every decisive result (50) is swe-bench-live; swe-rebench-v2 has no local tree, so all 2,065 of its selectable tasks are UNKNOWN.
- NOT_APPLICABLE 0: every selectable registered command is a pytest command (registration eligibility).

**Report + provenance hashes.** `data/f2_population/plugin_compat/plugin_compat_report.json` sha256 `80ce4a786a1ad35405803dd7b04c60be5b2f0abc7c707465661d48ffecc6e61d`; companion `.md` sha256 `9ca1b0648201da381c4a06903ca17f9f7c84720ec107fe53af736b8c34743fd5`; corpus `swe-bench-live` `9f22aa07a6510cb9aee455423a7aaa418c2417d488823b858d8fc1888bab2785`; corpus `swe-rebench-v2` `577b56ff483885ac3e22d78759f88453f33a3586fab4dd84b8b38a41eb3f5198`. The ledger SHA-256 was captured before the v1 run, after the v1 run, after the v2 run, and at session end - all four captures identical (`5fb0aa8eda37f220ea2fce5ca96688bb4a7edf94576b73b32b1678c1a1971430`): **zero mutations observed between captures** (the v2 run reports its own `generated_at` as 2026-10-15T16:57:42Z).

**Evidence labels.** Counts and hashes above are `PROVEN IN CURRENT REVISION`. Per-task labels are `OBSERVED_*`/`UNKNOWN` under `f2-plugin-compat-v2` and cover PLUGIN-INSTALL COVERAGE ONLY: they do not say a task builds, that its declared nodes exist, that its environment runs, or that it is scientifically analyzable. A local clone is not proof of installation and a declared dependency is not proof of installation - only an install-plan mechanism is. Because 97% of the population has no local tree, population-wide plugin risk beyond the 50 evidence-backed tasks remains `NOT ESTABLISHED`.

**Boundaries observed.** No pilot launched; no guest, service, worker or vCPU provisioned; ledger read-only and byte-identical; no network contact, no package installed, no project tree executed; the terminal django-guardian tasks untouched; nothing committed or pushed; PR #33 untouched.

---

### 16.10 (2026-10-15) — the 185 SOURCE_PRIORITY-resolved conflicts are INCLUDED; the population is recomputed

**Owner decision (2026-10-15).** The 185 in-registration cross-source conflicts are
records whose operative source has **already** been selected by the existing
`SOURCE_PRIORITY` policy (`kept_source=swe-bench-live`, the other recorded as
`conflicting_cross_source_priority_selected`; see §16.8). The classifier now
**includes** them in the population instead of subtracting them — subtracting them
had removed 185 *resolved* registered rows. Ledger exclusions are preserved, the
overlap is reported so nothing is subtracted twice, and the resolved ids are
retained as provenance (`resolved_by_source_priority`). The operative row is the
highest-priority source's row, so the classifier reads the same row the union kept.

> **OPEN OWNER DECISION — 185 conflict rows (2026-10-24).** Both options remain open:
> **INCLUDE-185** keeps the 185 SOURCE_PRIORITY-resolved rows in the population
> (**3,821**); **EXCLUDE-185** removes them (**3,636**). **2026-10-24 owner
> decision: `EXCLUDE-185`.** The scratch draft pool excludes the 185; no
> production policy changed. This box does not rewrite the 2026-10-15 history.

**This is candidate selection only.** Inclusion is **NOT** admission, buildability,
or scientific analyzability — it changes only which registered ids the offline
plugin classifier considers.

**Recomputed** (rule `f2-plugin-compat-v2`, generated `2026-10-16T01:06:34Z`,
elapsed 72.0 s, `python -m qwen_train.f2_s8_plugin_compat`):

| quantity | count |
|---|---|
| registered ids | 3,828 |
| cross-source conflicts (both sources) | 195 |
| …within a single source (all copies excluded) | 1 |
| …in the operative registration | 185 |
| …resolved by `SOURCE_PRIORITY` and **INCLUDED** | 185 |
| ledger rows / distinct task ids | 9 / 7 |
| …overlap with conflicts (excluded once) | 0 |
| **selectable** | **3,821** (= 3,828 − 7) |
| classified | 3,821 |

| class | count | % |
|---|---|---|
| OBSERVED_COMPATIBLE | 29 | 0.759 |
| OBSERVED_INCOMPATIBLE | 30 | 0.785 |
| UNKNOWN | 3,762 | 98.456 |
| NOT_APPLICABLE | 0 | 0.00 |

By source: `swe-bench-live` 29 compatible / 30 incompatible / 1,697 unknown
(1,756 selectable); `swe-rebench-v2` 2,065 unknown (no local tree). Every decisive
result remains `swe-bench-live`.

**Delta vs §16.9 (pre-inclusion):** selectable 3,636 → **3,821** (+185); classified
3,636 → 3,821; COMPATIBLE 25 → **29**; INCOMPATIBLE 25 → **30**; UNKNOWN 3,586 →
**3,762**. Of the 185 newly-included ids, 9 became decisive (4 compatible,
5 incompatible) and 176 stayed `UNKNOWN` (no local tree).

**Provenance / integrity.** The ledger SHA-256 was captured **before and after** the
run and is identical (`5fb0aa8e…`) — **zero mutations observed between the two
captures**. Registration SHA-256 `35b704b7…`; corpus digests unchanged
(`swe-bench-live 9f22aa07…`, `swe-rebench-v2 577b56ff…`). Report
`plugin_compat_report.json` sha256 `8adcda20…`; companion `.md` sha256 `aec7f9e8…`.

**Boundaries (unchanged from §16.9).** Read-only offline classification: no
admission, no pilot, no Q10/Q12/Q13, no network, no package installed, no project
tree executed; the terminal django-guardian tasks untouched.

### 16.11 (2026-10-24) — 185-conflict policy conflict remains OPEN

A later standing-authorization instruction (2026-10-24) says the 185 conflict rows
must stay **EXCLUDED by default**. That conflicts with the 2026-10-15 owner decision
recorded in §16.10, which **INCLUDED** the 185 SOURCE_PRIORITY-resolved rows and
recomputed the population from 3,636 to 3,821.

No data, pool, classifier, or policy was changed by this review. The conflict is an
owner decision; until it is resolved, code and tests do not encode a new 185 policy.
This note is a dated correction record, not a rewrite of §16.10.

> **OWNER DECISION — 185 conflict rows (2026-10-24).** **`EXCLUDE-185`**:
> 3,636 excluded. The scratch draft pool excludes the 185; production policy and
> classifier remain unchanged pending a committed authority document.
