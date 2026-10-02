# Remediation Record — PromptRepairer Startup Recovery

Repair of the startup-recovery defect that produced **134,384**
`RECOVERED_INTERRUPTED_PROMOTION` audit records (~99 % of the 20.8 MB audit log) by
re-processing the same 142 journal transactions on every backend boot.

Authority: human repair authorization, 2026-10-02. Scoped to the PromptRepairer
recovery path only — no Experiment-J execution, no F1/F2 execution, no model
training, no promotion.

---

## 1. Root cause

`recover_interrupted_promotions()` (`swarm_os/services/prompt_repairer.py`) resolved
pending transactions but **never wrote a terminal journal row**, while its pending
filter cancelled work on `committed` only. A resolved transaction therefore stayed
pending forever and was re-processed — and re-audited — on every startup.

The journal already had the terminal phase this needed: `rolled_back`, written by the
promotion failure path. Recovery neither emitted nor filtered on it. The repair uses
the state machine that was already there rather than adding a parallel one.

Three distinct defects, all in that one method:

| # | Defect | Effect |
|---|---|---|
| R1 | No terminal row on resolve | Unbounded re-audit — one record per pending row per boot |
| R2 | `except Exception: pass` around the provenance check | An unreachable lesson store was silently read as "nothing to remove", then `RECOVERED_INTERRUPTED_PROMOTION` was emitted anyway |
| R3 | `_audit(...)` fired unconditionally per pending row | `RECOVERED_INTERRUPTED_PROMOTION` asserted a recovery that may never have happened — including when the candidate was absent from the candidate store |

R1 also had a **second, non-test instance**: a promotion that failed after
`qdrant_applied` writes `rolled_back`; recovery ignored that phase, so such a
transaction stayed pending forever too. No such row exists in the current journal, so
this path had not yet fired in production — the fix closes it regardless.

## 2. Code changes

`swarm_os/services/prompt_repairer.py` only.

- `_TERMINAL_JOURNAL_PHASES = frozenset({"committed", "rolled_back"})` — the
  terminal-phase vocabulary, now used by the pending filter.
- `recover_interrupted_promotions()` — cancels pending on either terminal phase, and
  delegates each row to a new per-row resolver. `_save_candidates()` is now gated on
  rows actually resolved, not merely on a non-empty pending list.
- `_recover_pending_promotion()` (new) — verifies provenance **before** any mutation
  and returns whether the transaction reached a verified terminal state.

Outcomes:

| Condition | Audit event | Journal | Candidate mutated? |
|---|---|---|---|
| Lesson found, provenance matches → removed | `RECOVERED_INTERRUPTED_PROMOTION` (`verified: "removed"`) | `rolled_back` | yes, ACTIVE → PROMOTABLE |
| Store answered, lesson absent | `RECOVERED_INTERRUPTED_PROMOTION` (`verified: "absent"`) | `rolled_back` | yes |
| No `lesson_id` recorded | `RECOVERED_INTERRUPTED_PROMOTION` (`verified: "absent"`) | `rolled_back` | yes |
| Lesson found, provenance does **not** match | `ROLLBACK_PROVENANCE_MISMATCH` | `rolled_back` | **no** |
| **Lesson store unreachable** | `RECOVERY_UNVERIFIED` | *stays pending* | **no** |

Startup recovery is **not** disabled, gated, or made read-only. A genuinely
interrupted promotion is still recovered.

### New audit vocabulary

`RECOVERY_UNVERIFIED` is the one new item. No existing item meant *"verification could
not be performed"*: `ROLLBACK_PROVENANCE_MISMATCH` means verification **succeeded and
refused**, and `JOURNAL_RECOVERY_FAILED` means the whole loop aborted — neither
describes a per-row attempt that was recorded truthfully while the loop continued.
Reusing either would have misrepresented a partial, successful run of the other rows.

`GOVERNANCE_VERSION` was **not** bumped: that constant versions the evidence and
promotion-threshold policy, which this repair does not touch.

## 3. Idempotency — the exact invariant

> Re-running `recover_interrupted_promotions()` over the same journal cannot append an
> unbounded sequence of successful recovery records, because a transaction that reaches
> a verified terminal state is marked `rolled_back`, and a terminal phase permanently
> cancels its `qdrant_applied` row.

Verified directly against production data (§5) and by test
(`test_repeated_recovery_is_idempotent`: 5 replays → exactly 1 record).

**One deliberate residual.** An unverified transaction stays pending and is retried, so
a backend started while the lesson store is down records one truthful
`RECOVERY_UNVERIFIED` per pending row per boot. That is a real unresolved condition
being reported, not a success claim, and it is bounded per startup. It is the direct
cost of leaving the transaction retryable; the alternative (closing it unverified) would
permanently skip a recovery that must still happen.

## 4. Data repair

Authorized scope: exactly the then-pending contaminated transactions. The pending set
was recomputed with the **repaired** production `_TERMINAL_JOURNAL_PHASES`, and the
script refused to write unless it matched the authorized set exactly.

| | Value |
|---|---|
| Original journal | `41BB8D2822B62D27BB4FCDB58C02A8F585C8D01DC8AEAFA3F8E164187E142E97` · 80,773 B · 714 lines |
| Repaired journal | `417cb23fce3e137564868b37835033ca49d0d8b43c7c95272f753a441c5e492a` · 572 lines |
| Removed | **142** = 141 × `cand_fake`/`lesson_123` + 1 × `forged`/`legit_lesson_123` |
| Preserved | 143 `begin`, 143 `snapshot_saved`, 143 `committed`, 143 `qdrant_applied` (140 production hex ids + `cand_2` + `cand_4`) |

Preservation artifacts, under `data/_quarantine/` (gitignored, and **not** a
PromptRepairer input — recovery only ever reads `data/prompt_repairer_journal.jsonl`):

- `prompt_repairer_journal.original.20261002T225914Z.bak` — byte-identical original
- `pending_transactions_quarantine.20261002T225914Z.jsonl` — the 142 removed rows, verbatim

The journal was **not** truncated and **not** re-serialized: the 572 retained lines are
a byte-exact subsequence of the original 714.

## 5. Verification

Focused tests (no broad pytest — §3.6 still bars it):

| Suite | Result |
|---|---|
| `tests/test_rollback_crash_recovery.py` | 15 passed (8 new) |
| `tests/test_prompt_repairer_lifespan_isolation.py` | 5 passed (2 new) |
| `tests/test_prompt_repairer.py` | 60 passed |
| 6 further PromptRepairer/promotion suites | 153 passed, 2 skipped |
| `tests/test_f2_real_p2_integration.py` (real uvicorn child) | 4 passed |

Live proof against the **production** paths, nothing patched: three consecutive
`recover_interrupted_promotions()` calls produced **zero** changes to any production
store; the audit log stayed at 135,740 lines and no lesson removal was attempted.

Test isolation: after 237 test executions, including a real spawned backend,
`journal`, `audit`, `candidates`, `rollouts` and `evolution/fitness.jsonl` were all
**byte-identical** to their Phase-1 baselines.

## 6. Scientific firewall

Unchanged, verified against the Phase-1 baseline:

| Artifact | SHA-256 |
|---|---|
| `docs/EXPERIMENT_J.md` (F0) | `4EAFD2FAF2BA79078F8C1CAD7415DC1CEB2D24E753122A06FC2FA6AF76908337` |
| `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` | `FF4A6722F424AE84EFC7EBB191119C57B46E0E0D11590D1AD980D65B04038304` |
| `docs/LEARNING_EXPERIMENT_STATE.md` | `3EE88C894C7F80F98B33E294138B8B8EB24A5781373EB0B3CA91B079D6E3284C` |
| `data/evolution/fitness.jsonl` | `1E9094639F9404F041F00C35B4F69C93B3D7037E6A8847650AE42575566BAAFE` |
| `qwen_train/results/` (151 files) | combined `F882D5CF8C36B9C2DDE19098B82D9DE60FE7ED9A78726238BF2552A3FDC3138A` |

No Experiment-J artifact was read as input to this repair. `docs/EXPERIMENT_J.md` was
hash-checked only.

## 7. Historical audit log

The 134,384 historical `RECOVERED_INTERRUPTED_PROMOTION` records were **not** rewritten,
filtered, or deleted. They remain as evidence of the defect this record documents. The
audit log is byte-identical to its Phase-1 baseline
(`465C2EBDCEFB08855468D0D73E3A52699E93649FC5A994E0973C3DD547A5F75E`).

## 8. Remaining

- **`RECOVERY_UNVERIFIED` can repeat per boot while the lesson store is down.** Intended
  and truthful (§3); it stops when the store is reachable or the row is resolved.
- **`ROLLBACK_PROVENANCE_MISMATCH` leaves the transaction terminal without rolling the
  candidate back.** The verification proved the lesson is *not* this candidate's, so no
  state is changed. Whether a mismatched candidate should return to `PROMOTABLE` is a
  policy question this repair does not decide.
- **Journal rows carry no provenance marker** (only `phase`, `candidate_id`,
  `lesson_id`, `timestamp`). Identifying contaminated rows still required matching
  candidate ids. Fixed going forward by the tests being isolated; no marker was added,
  as no schema change was authorized.
- **`REQUIRES AUTHORIZATION`:** the pre-existing §12 items in
  `docs/PROMPTREPAIRER_LIFESPAN_ISOLATION_REMEDIATION.md` (YELLOW-1/2/3, GRAY) are
  untouched, so **broad pytest remains unauthorized**.