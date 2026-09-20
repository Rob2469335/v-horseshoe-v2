# Known Test Failures (baseline for the evaluation gate)

This is the authoritative known-failure set. The full-suite gate passes **only**
when the failure set equals this exactly. Any addition, removal, count change,
or a *different seventh* failure means the gate is **failed** — do not treat a
changed set as "close enough."

Last verified against a full `pytest` run **without `-x`** at commit `0a6f4816`
(the recovery-fix fix), backend down, autonomy off.

## The exact 7-entry set

| # | Test | Classification |
|---|------|----------------|
| 1 | `test_verification_failure_records_reflexion` | **P5 baseline** — fails identically at `59f1e632` and HEAD (`store_reflexion` awaited 0 times). Documented, not fixed. |
| 2 | `test_analysis_budget_scales_with_goal_depth` | Fails at or before `e43d9448` (pre-session). |
| 3 | `test_edit_goals_get_a_real_turn_budget` | Fails at or before `e43d9448` (pre-session). |
| 4 | `test_cloud_model_env_override` | Fails at or before `e43d9448` (pre-session). |
| 5 | `test_generate_falls_back_to_local_when_cloud_fails` | Fails at or before `e43d9448` (pre-session). |
| 6 | `test_memory_timestamp_except_handlers_are_parenthesized` | Fails at or before `e43d9448` (pre-session); comma-form excepts in `routes.py`/`sandbox_repl.py`. |
| 7 | `test_healing_watchman_stores_system_lesson_issue_resolution` | **Mechanism changed** between `e43d9448` and HEAD (`clear_eval_context` import missing → `'str' can't be awaited` at `healing_watchman.py:182`). Two different causes; own entry. |

## Must NOT be present after the recovery fix (commit `0a6f4816`)

- `test_collection_crash_is_env_error` — **was** failing (crash scored as `f2p: 0/1 passed`); fixed by the pre-summary crash-marker guard.
- `test_dist_info_stub_removal` — **was** failing (`TypeError` two-arg call); fixed to the one-argument signature.

If either of these reappears, the gate is failed and the fix regressed.

## Gate procedure

```text
run full suite WITHOUT -x, backend down
extract the actual FAILED test ids
compare (sorted, de-duplicated) against the exact 7 above
gate passes <=> sets are IDENTICAL (no additions, no removals, same 7)
```

The xfail (`test_autonomy_e2e.py` acceptance-proof) and the 2 skips are
expected and not part of the failure gate.

## Known-failure source

`full_suite_output.txt` (repo root, scratch) — the pre-fix run that showed
`9 failed` (7 known + 2 recovery-caused). After `0a6f4816` the two
recovery-caused failures are gone, leaving exactly the 7 above.