# Experiment J / F2 — Test Isolation Audit (Production Journal Write)

**Status:** documentation record. No remediation is authorized or performed.
**Date:** 2026-10-01
**Scope:** whether the tracked test suite writes Experiment J production learning
state under `data/`, and whether that matters to governed startup recovery.

This document is a provenance/auditability record. It changes no scientific
parameter, no F0/F1 result, and no authorization.

---

## 1. Current Experiment J / F2 state

| Item | State | Evidence |
|---|---|---|
| F0 scientific design | **FROZEN** | `docs/EXPERIMENT_J.md` (commit `20a1989b`) |
| F1 pilot | **CLOSED** | `docs/EXPERIMENT_J_F1_AUTHORIZATION.md`; 20/20 protocol observations |
| §10.3 item 9 (signing authority) | **PROVISIONED** | `docs/LEARNING_EXPERIMENT_STATE.md:1062` |
| §10.3 item 10 (genuine ACTIVE lesson L) | **PENDING** | `docs/LEARNING_EXPERIMENT_STATE.md:1041` |
| ACTIVE lesson L | **NONE** | Qdrant `ActiveLessons` absent (C0) |
| N=2 | **NOT AUTHORIZED** | `docs/LEARNING_EXPERIMENT_STATE.md:1065` |
| F1 backend-ownership fix | **COMMITTED** | `76b96dae` "FIX: fail closed on unowned F1 backend" |

## 2. Authorized vs unauthorized

| Action | Authorization |
|---|---|
| Implement §10.3 items 1–9 | Granted (COMPLETE per `LEARNING_EXPERIMENT_STATE.md:1032-1040`) |
| Execute §10.3 item 10 | The next authorized scientific step (`:1041`) |
| Freeze F2 artifacts (item 11), certify (12), authorize (13) | **NOT YET** |
| N=2 (item 14) | **NOT AUTHORIZED** (`:1045`) |
| Modify `docs/EXPERIMENT_J.md` (F0) | **PROHIBITED** — frozen scientific design |
| Change PromptRepairer thresholds / promotion / classification | **NOT AUTHORIZED** |
| **Remediate the test-isolation gap described below** | **NOT AUTHORIZED** — see §7 |

## 3. The test-isolation gap that actually exists

### 3.1 PROVEN — one tracked test writes the production journal

`tests/test_prompt_repairer.py::test_forged_journal_cannot_delete_unowned_lesson`
(line 553) uses the `repairer_fixture` (line 402) and at line 568 performs:

```python
with open(_journal_file(), "a", encoding="utf-8") as f:
    f.write(json.dumps({"phase": "qdrant_applied",
                        "candidate_id": "cand_fake",
                        "lesson_id": lid,
                        "timestamp": time.time()}) + "\n")
```

`repairer_fixture` (lines 402-413) patches **nothing** on the module — it only
replaces instance methods (`_save_candidates`, `_save_snapshots`, `_audit`) with
mocks *after* construction. Therefore `_journal_file()` still resolves to the
production path.

Verified by probe (read-only; no test executed):

```
outside any fixture : journal_file() = <repo>\data\prompt_repairer_journal.jsonl
inside repairer_fixture scope:
                      journal_file() = <repo>\data\prompt_repairer_journal.jsonl   <-- production
```

### 3.2 PROVEN — why that write is not inert

`swarm_os/services/prompt_repairer.py`:

- `_journal_file()` (line 66-69) derives the path from `_DATA_DIR` at call time.
- `recover_interrupted_promotions()` (line 908) is invoked **unconditionally** at
  backend startup: `swarm_os/app/main.py:129`.
- Recovery treats a `qdrant_applied` row as evidence of a crashed real promotion
  (lines 924-927) and may delete a lesson (lines 936-942) or roll a candidate back
  to `PROMOTABLE` (lines 945-951).

So a row written by a test becomes **input to production startup recovery**, not
merely test output. The write is also append-only and un-compacted: recovery
replays the whole file (line 914) on every start.

### 3.3 PROVEN — the repository already has a precedent, but no rule

`tests/conftest.py:169-181` contains an autouse fixture whose docstring states the
expectation directly:

> "Never let a test feed the REAL outcome-fitness store. ... appended sentinel-task
> rows to the repo's real data/evolution/fitness.jsonl, polluting the very store
> the live evolution daemon scores on."

`conftest.py:36-49` adds a second autouse guard (`global_lesson_manager_mock`,
"Default-hermetic governed lesson seam").

**PROVEN:** no autouse fixture in `conftest.py`, `tests/conftest.py`, or
`swarm_os/tests/conftest.py` references `_DATA_DIR`, `_CANDIDATES_FILE`, or
`_journal_file` (0 matches in all three). The gap is a missing instance of an
established practice.

### 3.4 GOVERNANCE GAP — no explicit general rule exists

Searched every Experiment J/F1/F2 authority document plus `AGENTS.md`:

| Pattern | Result |
|---|---|
| `test isolation` | NO MATCH |
| `tests must not` | NO MATCH |
| `must not mutate` | NO MATCH |
| `isolation boundary` | NO MATCH |
| `fixture.*production` | NO MATCH |
| `production state` | NO MATCH |
| `test suite` | NO MATCH |

The nearest provisions do not cover this:

- `AGENTS.md:62` — "NEVER commit secrets, API keys, `.env`, or `data/` runtime
  state" — a **commit** rule, not a runtime-mutation rule. It has been satisfied:
  `data/` is ignored (`.gitignore:43-44`).
- `docs/EXPERIMENT_J.md:147-164` (§8 Contamination Framework) — all 17 factors
  govern **arm execution**, not the test suite.
- `docs/EXPERIMENT_J.md:263` — Qdrant state control is "an operational requirement
  for C0/learning-event isolation", which concerns `ActiveLessons`, not the
  filesystem journal.

**Therefore:** the gap violates established *implied* engineering practice
(§3.3) but no *written* governance rule. Adding a general rule is **REQUIRES
AUTHORIZATION**.

## 4. Claims previously made that are CORRECTED

Recorded here so an independent auditor does not inherit them.

### 4.1 REJECTED — "candidate-store contamination is proven"

An earlier local audit attributed `data/prompt_repairer_candidates.json` creation
to `tests/test_prompt_repairer.py::test_crash_between_persistence_phases` and its
line 352 `repairer2 = PromptRepairer(...)`.

**This is incorrect.** That test (line 334) requests the `repairer` fixture
(lines 37-46), which is a generator fixture holding four nested `patch(...)`
contexts open across the `yield`. Line 352 therefore executes **inside** the patch
scope. Verified by probe reproducing the fixture's exact patch set:

```
inside the `repairer` fixture scope:
   journal_file()   = %TEMP%\tmpjt_...\prompt_repairer_journal.jsonl
   _CANDIDATES_FILE = %TEMP%\tmpjt_...\candidates.json
```

`_journal_file()` derives from `_DATA_DIR` at call time (line 66-69), so
redirecting `_DATA_DIR` isolates the journal too. Tests using `repairer_fixture`
additionally mock `_save_candidates()` (line 410).

**PROVEN: candidate-store contamination is NOT reproducible from the tracked test
suite.**

### 4.2 REJECTED — "the journal write near line 326 is a production contamination issue"

`test_malformed_transaction_journal` (line 322) also uses the `repairer` fixture
and also calls `open(_journal_file(), "a")` (line 326). Same fixture scope, same
isolation. **Not** a production write.

**PROVEN: the journal write at line 326 is isolated.**

### 4.3 NOT PROVEN from GitHub — local runtime observations

The following were observed on one machine and are **runtime evidence, not
Git-tracked repository state**. An auditor working from GitHub alone cannot verify
them and should not be told they are established:

- `data/prompt_repairer_journal.jsonl` local SHA-256 `14B10B98…`, 709 rows
- `data/prompt_repairer_candidates.json` local SHA-256 `44136FA3…`, content `{}`
- `data/prompt_repairer_audit.jsonl` local 20,394,866 bytes / 133,034 lines
- the local count of ~141 pending journal entries from fixture identities
- local hashes `948C792A…` / `14B10B98…` quoted in earlier chat

All `data/` paths are gitignored (`.gitignore:43-44`) and untracked.

### 4.4 NOT PROVEN — no causal claim from timestamps

No claim is made here that any specific file write was caused by any specific
test run except where the mechanism is proven in §3.1.

## 5. Do R1/R2/R3 exist as repository concepts?

**PROVEN — no.** A repository-wide search for bare `R1`/`R2`/`R3` across tracked
documentation returns a single hit, in the immutable `AGENTS_LEGACY.md:3561`, and
it is unrelated prose ("Round 1").

> **R1/R2/R3 ARE WORKING LABELS, NOT AN AUTHORITORY REPOSITORY DEFINITION.**

The repository's own vocabulary is: §10.3 **item** numbers, F1 **observation**
numbers, and `rollout_id` / `task_id` for evidence identity.

## 6. Threshold semantics (as actually defined)

`docs/RUNPOD_RUNBOOK.md:74-83`; constants at
`swarm_os/services/prompt_repairer.py:51,54`:

```
MIN_EVIDENCE_RUNS  = 3   distinct rollout_id (fallback run_id)
MIN_EVIDENCE_TASKS = 2   distinct task_id
```

One rollout contributes at most one evidence run. Multiple failures sharing a
`rollout_id` collapse to a single run (`docs/RUNPOD_RUNBOOK.md:81-82`).

## 7. Does this block Item 10?

**Assessment: no, on current evidence; but remediation is separately unauthorized.**

- The §3.1 write targets the **journal** only, not the candidate store (§4.1).
- No test writes an ACTIVE lesson, and Qdrant `ActiveLessons` is absent (C0).
- Recovery's deletion path is provenance-gated (lines 936-942): a lesson is removed
  only when `candidate_id in lesson.source_candidates`, otherwise
  `ROLLBACK_PROVENANCE_MISMATCH` is audited.
- `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md:67` puts "all other
  tests" out of scope for that authorization, so no existing grant covers fixing
  `tests/test_prompt_repairer.py`.

**REQUIRES AUTHORIZATION** before any of:
1. patching `_DATA_DIR` in `repairer_fixture`, or
2. adding an autouse conftest guard for `_DATA_DIR` / `_journal_file` /
   `_CANDIDATES_FILE`, or
3. an operational rule barring test-suite runs inside the Item-10 evidence window.

**OPEN RISK for later F2 steps (inferred, not established):** once a genuine lesson
L exists, its promotion appends a 4-row journal quartet. Every subsequent backend
startup replays the journal (line 914) against an ever-growing fixture backlog.
Fixture growth is unbounded (no compaction path; line 909 only reads).

## 8. Next authorized step

**§10.3 item 10** (`docs/LEARNING_EXPERIMENT_STATE.md:1041`) remains the next
authorized scientific step.

Two preconditions are *procedural*, not code changes:

1. Port 8000 must be clear before a rollout starts, so the F1-owned backend owns
   the port (enforced fail-closed by `76b96dae`).
2. Run no test suite between rollout start and promotion, so the production
   journal is not appended to during the evidence window.

Neither requires a repository change.

## 9. Evidence index

| Claim | Location |
|---|---|
| Production journal write | `tests/test_prompt_repairer.py:553`, `:568` |
| `repairer_fixture` patches nothing | `tests/test_prompt_repairer.py:402-413` |
| `_save_candidates` mocked there | `tests/test_prompt_repairer.py:410` |
| Isolated journal write (rejected claim) | `tests/test_prompt_repairer.py:322`, `:326` |
| Isolated candidate store (rejected claim) | `tests/test_prompt_repairer.py:334`, `:352`, fixture `:37-46` |
| Journal path derivation | `swarm_os/services/prompt_repairer.py:66-69` |
| Recovery reads whole journal | `swarm_os/services/prompt_repairer.py:908-914` |
| `qdrant_applied` ⇒ pending | `swarm_os/services/prompt_repairer.py:924-927` |
| Lesson deletion (provenance-gated) | `swarm_os/services/prompt_repairer.py:936-942` |
| Candidate rollback | `swarm_os/services/prompt_repairer.py:945-951` |
| Startup invokes recovery | `swarm_os/app/main.py:129` |
| Existing store-isolation precedent | `tests/conftest.py:169-181` |
| Second autouse lesson guard | `conftest.py:36-49` |
| Thresholds | `swarm_os/services/prompt_repairer.py:51,54` |
| Item-10 order | `docs/LEARNING_EXPERIMENT_STATE.md:1030-1045` |
| Governance rule | `docs/LEARNING_EXPERIMENT_STATE.md:1047-1051` |
| Tests out of scope for prior grant | `docs/EXPERIMENT_J_F2_EXECUTION_CONTRACT_AUTHORIZATION.md:67` |
| `data/` ignored | `.gitignore:26,43,44` |