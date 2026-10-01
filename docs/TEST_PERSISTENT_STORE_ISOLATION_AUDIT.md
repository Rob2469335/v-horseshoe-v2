# Test / Persistent-Store Isolation Audit

**Audit-only.** No production code, test, fixture, dependency, CI, or Experiment J
authority document was modified. The only repository change is this report.

- **Commit audited:** `32ed3d5a3126389013a01174a2fa1d06ac2501ce`
  (`FIX: isolate PromptRepairer persistent paths`)
- **Date:** 2026-10-01
- **Verdict:** **broad pytest is NOT currently safe.** Two RED findings, both
  proven by empirical probes that mutated production-like state during this audit.

Evidence labels used throughout: **PROVEN** (source/output), **OBSERVED**,
**INFERRED**, **UNKNOWN**.

---

## 1. Scope and exact commit audited

| Item | Value |
|---|---|
| HEAD before audit | `32ed3d5a3126389013a01174a2fa1d06ac2501ce` (matched expected) |
| Branch | `master` |
| Remote | `origin` → `https://github.com/Rob2469335/v-horseshoe-v2.git` |
| Remote branch | `origin/master` |
| Working tree at start | 24 untracked investigation files, 0 tracked modifications |

## 2. Authority documents checked

| Document | SHA-256 |
|---|---|
| `docs/EXPERIMENT_J.md` | `4EAFD2FAF2BA79078F8C1CAD7415DC1CEB2D24E753122A06FC2FA6AF76908337` |
| `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` | `FF4A6722F424AE84EFC7EBB191119C57B46E0E0D11590D1AD980D65B04038304` |
| `docs/LEARNING_EXPERIMENT_STATE.md` | `262AC0B8A1692F978C5D611E350BA2875B4EA684FA4E384BC659390494192197` |
| `docs/EXPERIMENT_J_F2_TEST_ISOLATION_AUDIT.md` | read (prior audit; scope: one test) |

**PROVEN:** all three authority documents unchanged by this audit (verified
byte-identical after all probes).

## 3. Baseline Git state and lint

```
git log -1 --oneline : 32ed3d5a FIX: isolate PromptRepairer persistent paths
git status --short   : 24 entries (24 untracked, 0 tracked modifications)
ruff check . --select E9,F : Found 47 errors (39 fixable)
```

**PROVEN:** all 47 ruff findings are pre-existing at this commit and none are in
files this audit touched. `tests/test_package.py` contributes 4 `invalid-syntax`
entries (a vendored twine test file).

## 4. Persistent-store inventory

### 4.1 `data/` top level (PROVEN — filesystem enumeration)

| Path | Bytes | Class | Reachable from pytest? |
|---|---|---|---|
| `data/prompt_repairer_journal.jsonl` | 80,773 | **Experiment J / production governance input** | **PROVEN yes** (see §8) |
| `data/prompt_repairer_candidates.json` | 2 | **Experiment J learning state** | **PROVEN yes** (see §8) |
| `data/prompt_repairer_audit.jsonl` | 20,481,619 | **Experiment J audit trail** | **PROVEN yes** (see §8) |
| `data/prompt_repairer_receipt.key` | 32 | signing key material, gitignored | UNKNOWN — no test references it |
| `data/prompt_repairer_rollouts.jsonl` | 1,709 | runtime evidence | UNKNOWN |
| `data/evolution/fitness.jsonl` | 4,525,791 | **production evolution store** | **PROVEN protected for `tests/`; GRAY for `swarm_os/tests/`** |
| `data/events/events.jsonl` | 576,234 | production event log | **PROVEN conditional write** (§10) |
| `data/analytics.duckdb` | 12,288 | analytics SQLite | UNKNOWN — no test reference found |
| `data/trajectories/` | 557 files | trajectory records | UNKNOWN |
| `data/checkpoints/` | 29 files | checkpoint state | UNKNOWN |
| `data/run_snapshots/` | 478 files / 421 MB | run snapshots | UNKNOWN |
| `data/f1_evidence/` | 28 files | **F1 runtime evidence** | UNKNOWN |
| `data/repair_states/`, `data/snapshots/`, `data/intel/`, `data/usage/`, `data/legal/`, `data/chess/`, `data/swe_probe/`, `data/kv_cache/`, `data/curriculum_fix/`, `data/books/`, `data/browser_profile/`, `data/pids/` | various | mixed | UNKNOWN |

### 4.2 External store: Qdrant (`http://127.0.0.1:6333`)

**OBSERVED:** Qdrant is **not currently listening** (`/health` unreachable), so
local-Qdrant bypasses are presently inert. See §7 for the structural exposure.

## 5. Test-to-store matrix (PROVEN)

| Test / family | Store | Mechanism | Class |
|---|---|---|---|
| `tests/test_prompt_repairer.py` (all, via `repairer_fixture`) | candidates / snapshots / audit / journal | `isolated_data_dir` patches `_DATA_DIR` + 3 paths; `_journal_file()` derives from `_DATA_DIR` at call time | **GREEN** |
| `tests/test_outcome_fitness.py`, `tests/test_rollout_run_provenance.py` | fitness | `monkeypatch.setattr(of, "FITNESS_PATH", tmp_path/...)` | **GREEN** |
| any `tests/` test calling `_feed_outcome` | fitness | `tests/conftest.py:169-181 isolate_outcome_fitness` sets `SWARM_EVOLUTION=0` | **GREEN** (probe §11) |
| `swarm_os/tests/**` | fitness | **no fitness guard in scope** | **GRAY** (no `_feed_outcome` reference found; probe clean) |
| `tests/test_admin_status.py:46-62` | `data/events/events.jsonl` | conditional `if not exists or size == 0` | **YELLOW** (§10) |
| any test requesting `client` (30 functions) | **candidates + audit** | `TestClient(app)` lifespan → `recover_interrupted_promotions()` | **RED** (§8) |
| `runtime_v2/services/_semantic_decision_cache.py` and 5 other modules | Qdrant | function-local `from qdrant_client import` bypasses `global_qdrant_mock` | **YELLOW** (§7) |

## 6. Fixture / isolation matrix

| Fixture | Scope | Protects | Hides / gap |
|---|---|---|---|
| `global_qdrant_mock` (`tests/conftest.py:67`) | `tests/` autouse | 4 module-level client bindings | 6+ function-local import sites unbound; `swarm_os/tests/conftest.py:16` patches only 3 (omits `lesson_manager`) |
| `global_mcp_manager_mock` (`:95`) | `tests/` autouse | real npx MCP subprocesses | hides real MCP lifecycle |
| `global_system_probe_mock` (`:127`) | `tests/` autouse | psutil probes | hides real probe results |
| `global_chess_engine_mock` (`:138`) | `tests/` autouse | real Stockfish spawn | documents a real full-suite hang (anyio#1014) it also masks |
| `global_subprocess_mock` (`:158`) | `tests/` autouse | background server spawns | **masks `subprocess.Popen` entirely** — will interfere with any fresh-process/seam test |
| `isolate_outcome_fitness` (`:169`) | `tests/` autouse | production fitness store | **not scoped to `swarm_os/tests/`** |
| `harden_testclient_shutdown` (`:34`) | session autouse | TestClient teardown hang | bounds teardown at 20s; can abandon shutdown mid-lifespan |
| `client` (`:28`) | on-request | — | **triggers the RED path in §8** |

## 7. Qdrant isolation assessment — **YELLOW**

**PROVEN:** `global_qdrant_mock` (`tests/conftest.py:67-92`) patches exactly four
module-level bindings: `vector_store`, `reflection_loop`, `tool_registry`,
`lesson_manager`.

**PROVEN:** six production modules bind the client via **function-local import**,
which patching a module attribute cannot intercept:

| Module | Line | Binding |
|---|---|---|
| `runtime_v2/services/_semantic_decision_cache.py` | 38-40 | `from qdrant_client import AsyncQdrantClient` then `AsyncQdrantClient(url="http://127.0.0.1:6333")` |
| `swarm_os/lib/vector/qdrant_store.py` | 106-110 | local import, `url=QDRANT_URL` |
| `swarm_os/services/chess_book_memory.py` | 112-118, 205-207 | local import |
| `swarm_os/services/gm_games.py` | 283-303 | local import |
| `swarm_os/services/legal/legal_search.py` | 155, 271, 319 | local import ×3 |
| `swarm_os/api/api_features.py` | 578-587 | local import |

**PROVEN:** these modules are referenced by tracked tests
(`test_semantic_cache*.py` ×5, `test_qdrant_store_client_loop.py`,
`test_features_search.py`, `test_gm_games.py`, `test_legal_*.py` ×4,
`test_agents_smoke.py` and 11 more).

**PROVEN:** `swarm_os/tests/conftest.py:16-35` patches **3** sites and omits
`lesson_manager`.

**PROVEN:** Qdrant is not currently listening, so the bypass cannot mutate a live
store right now. **INFERRED:** on a machine with Qdrant running (the documented
dev setup starts it on :6333), these paths would open real clients and could
create/upsert/delete real collections. Classified **YELLOW**, not RED, because no
live endpoint existed to prove mutation.

## 8. PromptRepairer assessment — one GREEN family, one **RED**

### 8.1 GREEN — direct unit tests

**PROVEN:** the fix is present at HEAD. `isolated_data_dir`
(`tests/test_prompt_repairer.py:402-421`) patches `_DATA_DIR`,
`_CANDIDATES_FILE`, `_SNAPSHOTS_FILE`, `_AUDIT_LOG_FILE`; `repairer_fixture`
(`:423-424`) depends on it. Because `_journal_file()`
(`swarm_os/services/prompt_repairer.py:66-69`) resolves from `_DATA_DIR` **at call
time**, the journal is redirected without being mocked — real on-disk append/read
and real `recover_interrupted_promotions()` are still exercised.

**PROVEN (path classification):** candidates (`:877`, `:1023`), snapshots
(`:878`, `:1027`), audit (`:1093`) and journal (`:905`, `:909`) are all
**dynamically resolved module globals**, not import-time constants.

### 8.2 RED-1 — `TestClient(app)` lifespan rewrites production learning state

**PROVEN chain:**

1. `tests/conftest.py:28-31` — `client` fixture builds `TestClient(app)`, which
   runs the app lifespan.
2. `swarm_os/app/main.py:126-129` — lifespan calls
   `get_prompt_repairer()` then `await repairer.recover_interrupted_promotions()`.
   **No fixture patches either call, and neither is autouse-guarded.**
3. `prompt_repairer.py:908-957` — recovery replays the whole production journal.
   Replay of the current journal yields **142 pending entries** (`cand_fake` ×141,
   `forged` ×1), so `pending` is truthy.
4. `prompt_repairer.py:952-955` — emits one `RECOVERED_INTERRUPTED_PROMOTION`
   audit record per pending entry (142 records ≈ 21 KB).
5. `prompt_repairer.py:967-968` — `if pending: self._save_candidates()` rewrites
   `data/prompt_repairer_candidates.json` via `atomic_write_text` (`:1023`).

**PROVEN empirical evidence (probe 3 and probe 7):**

| Event | `audit.jsonl` bytes | `candidates.json` mtime |
|---|---|---|
| Session baseline | 20,394,866 | `2026-10-01T02:47:00.905` |
| After `pytest tests/test_admin_status.py` (7 passed) | 20,459,927 (**+65,061**) | `2026-10-01T12:47:16.595` |
| After bare `TestClient(app)` + `GET /health` (no test at all) | 20,481,619 (**+21,692**) | `2026-10-01T12:58:54.879` |

The bare-`TestClient` probe proves the mutation needs **no test** — only the
lifespan. 426 `RECOVERED_INTERRUPTED_PROMOTION` records were written at 12:47
(~153 bytes each). Content of `candidates.json` stayed `{}` (sha256 unchanged
`44136FA3…`) because the 142 pending ids are fixture sentinels absent from the
store — so the rewrite is currently **idempotent in content but not in effect**,
and it appends ~142 audit records to a 20 MB governance trail on every startup.

**PROVEN blast radius:** 30 test functions request `client` across 8 files
(`test_readyz_response_shape.py` ×12, `test_ndjson_streaming_contract.py` ×6,
`test_agents_smoke.py` ×5, `test_admin_status.py` ×3, plus 4 singletons), and
90 test files reference `client`.

**PROVEN Experiment J relevance:** the production journal is an Experiment J
governance input (`prompt_repairer.py:908-931` decides lesson rollback from it),
and the audit log is the Experiment J audit trail. Both were mutated by running
the suite.

## 9. Evolution / fitness assessment — GREEN for `tests/`, GRAY for `swarm_os/tests/`

**PROVEN:** `_feed_outcome` (`runtime_v2/api/agent_service_v2.py:767-785`) is
gated by `_fitness_env_enabled()`, which is true only when
`SWARM_EVOLUTION == "1"` (`swarm_os/services/outcome_fitness.py:296-297`).
`tests/conftest.py:169-181` sets it to `0` for all `tests/` tests.

**PROVEN:** `FITNESS_PATH` is a **CWD-relative** constant
(`outcome_fitness.py:30`, `Path("data/evolution/fitness.jsonl")`), resolved at
call time inside the `with open(FITNESS_PATH, "a")` at `:159-160`.

**PROVEN probe 1:** `pytest tests/test_rollout_run_provenance.py` → 9 passed;
production `fitness.jsonl` unchanged at 10,415 rows / `1E9094639F9404F0`.

**PROVEN gap:** `isolate_outcome_fitness` lives in `tests/conftest.py`, which
applies only to `tests/`. Root `conftest.py` and `swarm_os/tests/conftest.py` have
no fitness guard. **PROVEN:** 0 files under `swarm_os/tests/` reference
`_feed_outcome`, `step_agent_stream`, or `agent_service`. **PROVEN probe 6:**
`swarm_os/tests/unit/test_genetics.py` → 12 passed, fitness unchanged. Classified
**GRAY** for the suite as a whole: absence of current references is not proof
against a future test.

**PROVEN:** tests that patch `of.FITNESS_PATH` (16 call sites in
`test_outcome_fitness.py`, 2 in `test_rollout_run_provenance.py`) are GREEN
regardless of the fixture.

## 10. Filesystem-write audit — one YELLOW

**PROVEN:** 234 candidate write sites in `tests/` after excluding `tmp_path` /
`tempfile` / `monkeypatch`-guarded lines. Tracing the highest-risk ones by
destination:

| Test | Destination | Class |
|---|---|---|
| `tests/test_admin_status.py:53-62` | **`data/events/events.jsonl`** (production) via `Path("data/events/events.jsonl")` + `.mkdir()` + `.open("w")` | **YELLOW** |
| `tests/test_sandbox_bounds.py:46` | sets `SWARM_WRITE_ROOT=data/curriculum_fix` (production-relative) | **YELLOW** — requires tracing the sandbox writer to prove containment |
| `tests/test_admin_resume_latest.py:9` | `Path("swarm_os/data/snapshots/snapshot_0001.json")` | GRAY — read-only reference, write not established |
| `tests/test_arm_workspace.py`, `test_autonomy_e2e.py`, `test_autonomous_loop_bugs.py`, `test_agents_md_atomic.py`, `test_approval_gate.py` | temp/git-repo fixtures created inside the test | GREEN |

**PROVEN probe 2:** `pytest tests/test_admin_status.py` → 7 passed;
`data/events/events.jsonl` unchanged (576,234 bytes, `15A3D9B010D7E46C`). The
write at `:57` is guarded by `if not events_path.exists() or size == 0`, which is
currently false. **YELLOW** rather than GREEN because the guard is *state-
dependent*: on a clean checkout where the file is absent, the test **would
create a production event log entry**.

## 11. Empirical probes and exact evidence

| # | Command | Before | After | Result |
|---|---|---|---|---|
| 1 | `pytest -q tests/test_rollout_run_provenance.py` | fitness 10,415 rows / `1E909463` | identical | 9 passed, store unchanged |
| 2 | `pytest -q tests/test_admin_status.py` | events 576,234 B / `15A3D9B0` | identical | 7 passed, store unchanged |
| 3 | `pytest -q tests/test_admin_status.py` (journal family) | audit 20,394,866 B | **20,459,927 B** | **RED-1 mutation** |
| 4 | `pytest -q swarm_os/tests/unit/test_genetics.py` | fitness `1E909463` | identical | 12 passed, store unchanged |
| 5 | bare `TestClient(app)` + `GET /health` | audit 20,459,927 B | **20,481,619 B** | **RED-1 confirmed with no test** |
| 6 | `pytest -q tests/test_prompt_repairer.py::test_forged_journal_cannot_delete_unowned_lesson` | journal 714 / `41BB8D28` | identical | 1 passed, GREEN |
| 7 | 5 `repairer_fixture` tests | journal 714 / `41BB8D28` | identical | 5 passed, GREEN |

**PROVEN:** the production journal was **never** modified by any probe. It remains
714 rows / `41BB8D2822B62D27BB4FCDB58C02A8F585C8D01DC8AEAFA3F8E164187E142E97`.
Known contaminated evidence was **not** cleaned, rewritten, or deleted.

**OBSERVED, disclosed:** `audit.jsonl` and `candidates.json` **were** mutated by
probes 3 and 5. Per instruction, this branch was stopped and the evidence
preserved rather than cleaned.

## 12. Memory / reflection / diary assessment — GRAY

**PROVEN:** no file-write memory/diary store was found reachable from tests. The
memory subsystem persists in Qdrant (`reflection_loop.py:13,277`;
`lesson_manager.py:22,261-269`), both module-level and therefore patched to
`:memory:` by `global_qdrant_mock`. **UNKNOWN:** whether any test reaches a
memory write path through an unbound module. Classified **GRAY** — not GREEN —
because absence of a file store is not proof of isolation for the Qdrant path.

## 13. Runtime evidence / Experiment J assessment

**PROVEN:** `data/f1_evidence/` (28 files) holds F1 runtime evidence. No test
imports `qwen_train/run_repair_task.py` (verified: 0 tracked references).
**GRAY:** `data/trajectories/`, `data/checkpoints/`, `data/run_snapshots/` writers
were not exhaustively traced; `test_agent_trajectory.py` and 10 other trajectory-
referencing tests exist, and the trajectory path resolution was not established.

**PROVEN:** no test creates an ACTIVE lesson or reaches `promote()` against the
production store — `promote` requires `SWARM_RECEIPT_KEY`, and
`tests/conftest.py:11-15` sets a test-only key via `monkeypatch.setenv`. Qdrant
`lesson_manager` is `:memory:` under the fixture.

**PROVEN:** no Experiment J authority document, threshold, `k`, `N`, treatment, or
promotion rule was modified by this audit.

## 14. TestClient / application-startup assessment — see §8.2 (RED-1)

Additional startup side effects, **PROVEN** by probe 7 output: startup emits
`SQLite MCP Server running on stdio` with
`Database path: <repo>/test.db`, and `Error while closing MCP connections`. So
startup also creates/opens a repo-root `test.db` and spawns an MCP subprocess
when the MCP mock is absent (as in the bare probe).

**PROVEN:** `global_subprocess_mock` patches `subprocess.Popen` but **cannot**
stop MCP, because the MCP SDK uses `anyio.create_subprocess_exec` — documented in
`tests/conftest.py:97-105`.

## 15. Environment-variable audit

| Variable | Read at | Test interaction | Class |
|---|---|---|---|
| `SWARM_EVOLUTION` | runtime, `outcome_fitness.py:296` | forced `0` in `tests/`; not guarded in `swarm_os/tests/` | GREEN / GRAY |
| `SWARM_RECEIPT_KEY` | runtime, `prompt_repairer.py:162` | set to `unit-test-receipt-key` by autouse `tests/conftest.py:11-15`; also set per-module in 3 test files | GREEN |
| `SWARM_WORKSPACE_ROOT` | runtime + import, `swarm_os/lib/paths.py:57` | `.env` does not define it; `tests/test_read_before_write_guard.py:45` skips if unset | GREEN |
| `SWARM_WRITE_ROOT` | runtime | `test_sandbox_bounds.py:46` sets it to a production-relative dir | **YELLOW** |
| `QDRANT_URL` | runtime | CI sets `http://127.0.0.1:6333`; unbound local imports would honor it | **YELLOW** |

**PROVEN:** `.env` defines `SWARM_EVOLUTION` (name only; value not printed).
`load_dotenv(override=True)` runs at `swarm_os/app/main.py:20`, so importing the
app inside a test process loads production `.env` into `os.environ`.

## 16. Global-autouse-fixture assessment

**PROVEN:** `global_subprocess_mock` (`tests/conftest.py:158-166`) replaces
`subprocess.Popen` with a MagicMock returning `(b"", b"")` and pid `99999` for
**every** test. **INFERRED:** any test whose purpose is to verify real process
spawning, PID identity, or fresh-process isolation will silently see the mock.
`tests/test_f1_evidence.py` (committed in `76b96dae`) tests F1 backend ownership
and imports `subprocess` directly, so it must be reasoning about Popen
symbolically rather than spawning — consistent with this risk.

**PROVEN:** `global_qdrant_mock` is broad but its gaps are structural, not
philosophical (§7).

No fixture was modified or refactored by this audit.

## 17. Clean-checkout / untracked-file assessment

**PROVEN:** 24 untracked files. Searched every tracked `*.py` for `import`/`from`
of each untracked module name: **0 imports found**. The two apparent hits
(`test_child`, `test_import`) are **substring coincidences** in test-function
names (e.g. `swarm_os/tests/unit/test_genetics.py:53 test_child_has_all_tool_genes`).

**Classification:** the tracked repository does **not** depend on any untracked
file — **PROVEN**. A clean checkout of `32ed3d5a` reproduces the tracked test
behavior. No untracked file was added, deleted, modified, or staged.

## 18. Classification summary

| Class | Count | Items |
|---|---|---|
| **GREEN** | 3 | PromptRepairer unit fixtures; fitness store for `tests/`; `.env`-independent env fixtures |
| **YELLOW** | 3 | `TestClient(app)` audit append (RED-1 companion, content-idempotent); Qdrant local-import bypass; `test_admin_status.py` conditional event-log write; `SWARM_WRITE_ROOT` in `test_sandbox_bounds.py` |
| **RED** | 2 | **RED-1** `TestClient(app)` lifespan → production `candidates.json` rewrite + `audit.jsonl` growth (proven, no test needed); **RED-2** 90 test files / 30 functions can trigger RED-1 |
| **GRAY** | 6 | `swarm_os/tests/` fitness; memory/diary Qdrant path; trajectories; checkpoints; run_snapshots; receipt-key file reachability |

YELLOW and RED rows total 5 distinct items; RED-1 and RED-2 are the same mechanism
at different blast radii, counted separately as specified.

## 19. RED / YELLOW detail

### RED-1 — lifespan writes production learning state
1. **File:** `swarm_os/app/main.py:126-129` (+ `swarm_os/services/prompt_repairer.py:908-968`)
2. **Trigger:** any `TestClient(app)` construction, including bare lifespan
3. **Write path:** `data/prompt_repairer_candidates.json` (`atomic_write_text`,
   `:1023`) and `data/prompt_repairer_audit.jsonl` (`open(...,"a")`, `:1093`)
4. **Why isolation fails:** no fixture patches `recover_interrupted_promotions`
   or `_save_candidates`; `global_qdrant_mock` covers Qdrant only, and this is
   filesystem persistence
5. **Evidence:** probe 7 — bare `TestClient` grew audit by 21,692 B and rewrote
   candidates mtime
6. **Minimal remediation:** autouse fixture patching
   `swarm_os.services.prompt_repairer._DATA_DIR` + the three sibling path globals
   to a temp dir, placed in root `conftest.py` so it covers `tests/` **and**
   `swarm_os/tests/`
7. **Authorization required:** **YES** — new autouse guard, explicitly out of
   scope for this audit

### RED-2 — suite-wide exposure
Same mechanism. 30 test functions across 8 files request `client`; 90 files
reference it. Any full-suite run executes the lifespan repeatedly.

### YELLOW-1 — Qdrant local-import bypass
Files in §7 table. Remediation: extend `global_qdrant_mock` to cover the six
local-import modules, or route all clients through one factory. **Authorization
required: YES** (fixture refactor).

### YELLOW-2 — conditional production event-log write
`tests/test_admin_status.py:53-62`. Remediation: monkeypatch the events path to
`tmp_path`. **Authorization required: YES** (test modification).

### YELLOW-3 — `SWARM_WRITE_ROOT` points into production
`tests/test_sandbox_bounds.py:46` sets `SWARM_WRITE_ROOT=data/curriculum_fix`,
resolved relative to CWD. Writer containment was not traced. **GRAY-leaning
YELLOW.** Remediation: point at `tmp_path`. **Authorization required: YES.**

## 20. Remediation plan, ordered by risk

1. **RED-1/RED-2** — add a root-`conftest.py` autouse fixture redirecting
   `prompt_repairer._DATA_DIR` / `_CANDIDATES_FILE` / `_SNAPSHOTS_FILE` /
   `_AUDIT_LOG_FILE` to a temp dir. Highest value: stops the audit log growing
   ~21 KB per startup and stops any production candidates rewrite.
2. **YELLOW-2** — redirect `test_admin_status.py`'s events path to `tmp_path`.
3. **YELLOW-3** — redirect `test_sandbox_bounds.py`'s `SWARM_WRITE_ROOT`.
4. **YELLOW-1** — extend Qdrant coverage to the six local-import modules, in both
   conftests, adding `lesson_manager` to `swarm_os/tests/conftest.py`.
5. **GRAY closure** — trace trajectory / checkpoint / run-snapshot path
   resolution under test, then classify.
6. **Pre-existing 142 pending journal rows** — leave in place. They are known
   fixture evidence; deleting them is a destructive change to a governance input
   and is not authorized.

## 21. Changes NOT authorized by this task (none performed)

PromptRepairer production code; any test or fixture; `docs/EXPERIMENT_J.md`;
`docs/EXPERIMENT_J_F1_AUTHORIZATION.md`; `docs/LEARNING_EXPERIMENT_STATE.md`;
dependencies; CI; `.env`; cleaning the audit/candidates/journal stores; deleting
the 141 stale journal rows; the 24 untracked investigation files; Experiment J F2
execution; creating or promoting an ACTIVE lesson; `N`, thresholds, `k`, treatment,
promotion rules, or authorization.

## 22. Final recommendation

**Broad pytest is NOT safe yet.** Two RED findings are proven: running the suite
appends governed Experiment J audit records and rewrites the production candidate
store, purely as a side effect of application lifespan startup. This requires no
test to be "broken" — a bare `TestClient(app)` reproduces it.

The test suite is therefore **not** currently a read-only operation on
production-like state, and **no test suite may be run inside the Experiment J
§10.3 item-10 evidence window** until remediation step 1 above is authorized and
applied.

Targeted suites remain safe and were verified GREEN: PromptRepairer unit tests,
fitness tests, and `swarm_os/tests` genetics.

## 23. Evidence index

| Claim | Location |
|---|---|
| Lifespan recovery call | `swarm_os/app/main.py:126-129` |
| Recovery replay + `_save_candidates` | `swarm_os/services/prompt_repairer.py:908-968` |
| Candidates write | `swarm_os/services/prompt_repairer.py:1021-1023` |
| Audit append | `swarm_os/services/prompt_repairer.py:1093` |
| Journal dynamic resolution | `swarm_os/services/prompt_repairer.py:66-69` |
| `client` fixture | `tests/conftest.py:28-31` |
| Qdrant mock coverage | `tests/conftest.py:67-92`; `swarm_os/tests/conftest.py:16-35` |
| Fitness gate + path | `swarm_os/services/outcome_fitness.py:30,159-160,296-297` |
| Fitness guard | `tests/conftest.py:169-181` |
| Conditional event write | `tests/test_admin_status.py:53-62` |
| `SWARM_WRITE_ROOT` | `tests/test_sandbox_bounds.py:46` |
| Test-only receipt key | `tests/conftest.py:11-15` |
| dotenv override | `swarm_os/app/main.py:20` |
| Prior audit scope | `docs/EXPERIMENT_J_F2_TEST_ISOLATION_AUDIT.md` |
