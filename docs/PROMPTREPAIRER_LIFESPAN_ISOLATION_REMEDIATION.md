# Remediation Record — RED-1 / RED-2 PromptRepairer Lifespan Isolation

Remediation of the two RED findings in
`docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md`. That document's historical
findings are preserved unchanged; this record documents the fix only.

- **Starting SHA:** `ff0a07020101edc7d320a6254c2147a47403413e`
  (`ARCH: audit persistent test-store isolation`)
- **Scope:** the PromptRepairer persistent-store defect only.
- **Broad pytest was NOT run and is NOT authorized by this change.**

## 1. Files changed

| File | Change |
|---|---|
| `conftest.py` | added root-level autouse fixture `isolate_prompt_repairer_store` |
| `tests/test_prompt_repairer_lifespan_isolation.py` | **new** — 3 regression tests |

Not modified: `swarm_os/services/prompt_repairer.py`, `swarm_os/app/main.py`,
`tests/conftest.py`, `swarm_os/tests/conftest.py`, `tests/test_prompt_repairer.py`,
`tests/test_admin_status.py`, `tests/test_sandbox_bounds.py`, dependencies, CI,
any `data/` file, any Experiment J document.

## 2. Why the previous fixture was insufficient

`32ed3d5a` added `isolated_data_dir` to `tests/test_prompt_repairer.py` and made
`repairer_fixture` depend on it. That correctly protected **direct** PromptRepairer
unit tests. It did **not** protect the application-lifespan path, because:

1. `swarm_os/app/main.py:126-129` runs `recover_interrupted_promotions()` inside
   the lifespan.
2. The lifespan obtains its repairer via `get_prompt_repairer()`
   (`prompt_repairer.py:1614-1618`), which constructs `PromptRepairer()` with **no
   fixture arguments** — so `repairer_fixture` and `isolated_data_dir` are never
   involved.
3. `tests/conftest.py:28-31` `client` is **on-request, not autouse**, so no
   isolation existed for the 30 test functions that request it.
4. `tests/conftest.py` is scoped to `tests/` only, so `swarm_os/tests/` had no
   PromptRepairer isolation at all.

Result: any `TestClient(app)` construction replayed the production journal
(142 pending rows) and wrote to production.

## 3. The fixture mechanism

`conftest.py::isolate_prompt_repairer_store` — `autouse`, uses pytest `tmp_path`,
patches four module globals:

```python
swarm_os.services.prompt_repairer._DATA_DIR        -> tmp_path/"prompt_repairer_store"
swarm_os.services.prompt_repairer._CANDIDATES_FILE -> <store>/candidates.json
swarm_os.services.prompt_repairer._SNAPSHOTS_FILE  -> <store>/snapshots.json
swarm_os.services.prompt_repairer._AUDIT_LOG_FILE  -> <store>/audit.jsonl
```

**The journal is isolated by derivation, not by mocking.** `_journal_file()`
(`prompt_repairer.py:66-69`) returns `_DATA_DIR / "prompt_repairer_journal.jsonl"`
and its own docstring states it is *"Derived from `_DATA_DIR` at call time so
tests that redirect `_DATA_DIR` also isolate the journal."* Every write site
(`:1027-1028` `_save_candidates`, `:1096-1103` `_audit`, `:898-906`
`_journal_append`) resolves its destination from module globals at **call** time,
so the redirect also holds for an already-constructed singleton.

**Nothing is disabled.** `recover_interrupted_promotions()` is not patched,
`_save_candidates()` is not mocked, the lifespan is not skipped, and
`_journal_file()` is not mocked. Real JSON/JSONL I/O, real recovery logic, real
`_save_candidates()`, real `_audit()` all execute against temporary state. This
mirrors the existing `isolate_outcome_fitness` pattern in `tests/conftest.py:169`.

## 4. Why it covers both test trees

Placed at the **repository root** `conftest.py`, which pytest collects for every
test tree beneath it. `tests/conftest.py` applies only to `tests/`, and
`swarm_os/tests/conftest.py` only to `swarm_os/tests/`; neither would have covered
the other tree.

**Proven empirically** with a throwaway probe in each tree (removed after use):

```
tests/test_zz_probe_tree.py              -> TESTS_TREE_OK  ...\pytest-461\test_probe_tests_tree_0\prompt_repairer_store\prompt_repairer_journal.jsonl
swarm_os/tests/unit/test_zz_probe_tree.py -> SWARMOS_TREE_OK ...\pytest-461\test_probe_swarmos_tree_0\prompt_repairer_store\prompt_repairer_journal.jsonl
2 passed
```

**Proven decisively** by re-running the audit's exact RED-1 reproducer under
pytest: a bare `TestClient(app)` + `GET /health` (the probe that previously grew
the audit by 21,692 bytes) now leaves production byte-identical.

## 5. Regression tests

`tests/test_prompt_repairer_lifespan_isolation.py`:

1. `test_lifespan_startup_does_not_mutate_production_prompt_repairer_state` —
   fingerprints size+SHA-256 of all three production stores, runs a real lifespan
   startup, asserts byte-identical.
2. `test_lifespan_startup_writes_only_into_the_isolated_store` — asserts all four
   destinations resolve inside `tmp_path` after a real startup.
3. `test_isolated_store_receives_real_recovery_activity` — seeds a **pending**
   promotion (`qdrant_applied` with no later `committed`) into the temp journal,
   runs lifespan, then asserts the temp audit log contains
   `RECOVERED_INTERRUPTED_PROMOTION` **and** the temp candidates file shows the
   real rollback `ACTIVE -> PROMOTABLE` with `active_lesson_id` dropped. This
   proves recovery ran for real rather than being bypassed.

Tests 1–3 reset the `get_prompt_repairer()` module singleton
(`_repairer_instance = None`) because it is cached process-wide
(`prompt_repairer.py:1612-1618`); without that reset the seeded candidates file is
never re-read. This was found empirically — an earlier draft of the test failed
for exactly this reason.

## 6. Targeted tests run (no broad pytest)

| Command | Result |
|---|---|
| `pytest -q tests/test_prompt_repairer_lifespan_isolation.py` | **3 passed** |
| `pytest -q tests/test_prompt_repairer.py::test_forged_journal_cannot_delete_unowned_lesson` | **1 passed** |
| `pytest -q tests/test_prompt_repairer.py -k "mutation_after_evaluation or forged_evaluation_receipt or stale_evaluation_receipt or unsafe_mutation_after_evaluation or forged_journal"` | **5 passed**, 55 deselected |
| `pytest -q tests/test_prompt_repairer.py` | **60 passed** |
| both-tree probe (temporary, removed) | 2 passed |
| bare-`TestClient` reproducer probe (temporary, removed) | 1 passed |

## 7. Production-store before / after

| Store | Bytes before | Bytes after | SHA-256 before = after |
|---|---|---|---|
| `data/prompt_repairer_journal.jsonl` | 80,773 | 80,773 | `41BB8D2822B62D27BB4FCDB58C02A8F585C8D01DC8AEAFA3F8E164187E142E97` |
| `data/prompt_repairer_audit.jsonl` | 20,503,309 | 20,503,309 | `8838C901FDDDF9FBDAA3881D578664299194A5212352E7B7834C8385A978AAF3` |
| `data/prompt_repairer_candidates.json` | 2 | 2 | `44136FA355B3678A1146AD16F7E8649E94FB4FC21FE77E8310C060F61CAAFF8A` |

**All three byte-identical and mtime-unchanged across every targeted run.**

## 8. Temporary-store evidence

During the tests the redirected destinations were:

```
<pytest tmp>/test_isolated_store_receives_r0/prompt_repairer_store/
    prompt_repairer_journal.jsonl   (read by real recovery)
    candidates.json                 (written by real _save_candidates)
    audit.jsonl                     (written by real _audit)
```

Observed content: temp `audit.jsonl` contained `RECOVERED_INTERRUPTED_PROMOTION`;
temp `candidates.json` held `cand_regression` with `status == "PROMOTABLE"` and no
`active_lesson_id`. No PromptRepairer file was created under the repository's
`data/`.

## 9. Unexpected mutation during this remediation — DISCLOSED

Between capturing the initial baseline and the first targeted run, the production
audit log grew by **21,690 bytes** (20,481,619 → 20,503,309, mtime `13:15:08`),
with `candidates.json` mtime advancing identically.

Investigated:

- The write **preceded** the `conftest.py` edit (`13:15:31`).
- `pytest_cache/v/cache/nodeids` mtime was `12:53:39` — no pytest run at `13:15`.
- `py_compile` does not execute module bodies and cannot start a lifespan.
- A 50-second idle sampling window showed **no periodic writer**.
- No process held the audit file open at inspection time; the writer had exited.
- The delta (21,690 B) matches one lifespan startup's ~142 recovery records,
  i.e. the same RED-1 signature.

**Not identified.** I did not run a lifespan outside pytest in this window, and
no command I issued could have produced it. Per instruction the evidence was
**preserved, not cleaned**; the affected values are the recorded baseline in §7.
This remains an open question about what triggered that write — it is not claimed
as understood.

## 10. Experiment J integrity

| Document | SHA-256 before = after |
|---|---|
| `docs/EXPERIMENT_J.md` | `4EAFD2FAF2BA79078F8C1CAD7415DC1CEB2D24E753122A06FC2FA6AF76908337` |
| `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` | `FF4A6722F424AE84EFC7EBB191119C57B46E0E0D11590D1AD980D65B04038304` |
| `docs/LEARNING_EXPERIMENT_STATE.md` | `262AC0B8A1692F978C5D611E350BA2875B4EA684FA4E384BC659390494192197` |

Journal verified at 714 rows / 142 pending after all runs. No F2 execution, no
ACTIVE lesson created or promoted, no change to `N`, thresholds, `k`, treatment,
promotion rules, or authorization. The 142 stale pending rows were not deleted,
rewritten, or normalized.

## 11. Checks

`git diff --check` clean · `py_compile` exit 0 · `ruff check --select E9,F` clean
on both touched files · repository ruff total unchanged at 47 (baseline) ·
`git diff --stat -- swarm_os/` empty · `git status --short -- data/` empty · 24
pre-existing untracked investigation files untouched.

## 12. Remains unresolved

This remediation covers RED-1 and RED-2 only. Still open from
`docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md`:

- **YELLOW-1** — Qdrant `AsyncQdrantClient` bound via function-local import in six
  modules, unpatchable by `global_qdrant_mock`; `swarm_os/tests/conftest.py` also
  omits `lesson_manager`.
- **YELLOW-2** — `tests/test_admin_status.py:53-62` conditionally writes production
  `data/events/events.jsonl` when that file is absent or empty.
- **YELLOW-3** — `tests/test_sandbox_bounds.py:46` sets `SWARM_WRITE_ROOT` to the
  production-relative `data/curriculum_fix`.
- **GRAY** — `swarm_os/tests/` outcome-fitness scope; memory/reflection/diary
  Qdrant path; trajectory, checkpoint and run-snapshot path resolution;
  `data/prompt_repairer_receipt.key` reachability.
- **Unattributed audit-log growth** described in §9.

Each requires separate authorization. None was touched.

## 13. Broad pytest status

**Do NOT run broad pytest yet.** This remediation alone does not authorize it.
YELLOW-1 through YELLOW-3 and the GRAY items are unresolved, and broad pytest was
not run to confirm the suite is otherwise unaffected.
