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
