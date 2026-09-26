# Experiment J F1 — Observation 2 Launch Recipe (Corrected)

**Status:** RECORDED — NOT EXECUTED
**Date:** 2026-09-25
**Harness version:** run_repair_task.py (F1-modified with evaluator separation + web-tool restriction)
**Correction:** SWARM_F1_NO_WEB_TOOLS=1 strips web_search/web_fetch from the coder agent's tool surface for this local repair task.

---

## Exact Launch Command (PowerShell)

```powershell
$env:SWARM_WORKSPACE_ROOT = "C:\Users\rober\Projects\swe_probe_work\f1_pilot\run_1_fresh\repo"
$env:SWARM_MEMORY_INJECT = "0"
$env:SWARM_AUTONOMY = "0"
$env:SWARM_NO_TOASTS = "1"
$env:SWARM_SEMANTIC_CACHE = "0"
$env:SWARM_GENETIC_MUTATION = "0"
$env:SWARM_EVOLUTION = "0"
cd C:\Users\rober\Projects\v-horseshoe-v2
python qwen_train/run_repair_task.py --instance-id "f1_pilot/run_1_fresh" --base-commit "45d9f6192dd7b1c81c63f46d277798f19adb97ec" --problem-statement "The sandbox_bounds() function in swarm_os/lib/paths.py has a bug: when a write root is configured, the function incorrectly reports write_covers_workspace as True when the write root is a subdirectory of the workspace root, and False when the write root covers the workspace root. The function should report True when the write root covers the workspace root, and False when the write root is a subdirectory of it." --test-cmd "python -m pytest C:\Users\rober\Projects\swe_probe_work\f1_pilot\evaluator\test_sandbox_bounds.py -v" --f2p test_write_root_subdir_of_workspace --f2p test_workspace_inside_write_root --f2p test_no_write_root --evaluator-dir "C:\Users\rober\Projects\swe_probe_work\f1_pilot\evaluator" --behavioral-prompt --timeout 1200 --out "qwen_train/results/f1_observation_2.jsonl"
```

Note: The harness automatically sets `SWARM_F1_NO_WEB_TOOLS=1` before invoking the CLI. The backend reads this variable and strips web_search/web_fetch from the coder agent's tool surface.

## Parameters

| Parameter | Value |
|-----------|-------|
| instance-id | `f1_pilot/run_1_fresh` |
| base-commit | `45d9f6192dd7b1c81c63f46d277798f19adb97ec` |
| target workspace | `C:\Users\rober\Projects\swe_probe_work\f1_pilot\run_1_fresh\repo` |
| evaluator | `C:\Users\rober\Projects\swe_probe_work\f1_pilot\evaluator\test_sandbox_bounds.py` |
| test-cmd | `python -m pytest C:\Users\rober\Projects\swe_probe_work\f1_pilot\evaluator\test_sandbox_bounds.py -v` |
| fail-to-pass | `test_write_root_subdir_of_workspace`, `test_workspace_inside_write_root`, `test_no_write_root` |
| timeout | 1200 seconds |
| model | `robs4b` (local qwen3.5-4b) |
| provider | llama.cpp (local) |
| result path | `qwen_train/results/f1_observation_2.jsonl` |
| behavioral-prompt | true |

## Tool Surface

### AVAILABLE to the F1 coder agent:

| Tool | Purpose |
|------|---------|
| filesystem | Read/patch/write files in the target workspace |
| git | Git status/log/diff (read-only) |
| semantic_search | Code search |
| github_research | GitHub API queries |
| sandbox_repl | Shell execution (bash/powershell) |
| lsp | Language server diagnostics |
| mcp | MCP server tools |
| email | Email operations |
| playwright | Browser automation |
| todo | Task tracking |
| remember | Memory operations |
| deprecate_memory | Memory deprecation |
| final | Declare task complete |

### UNAVAILABLE to the F1 coder agent:

| Tool | Status | Reason |
|------|--------|--------|
| web_search | **BLOCKED** | F1 correction: prevents drift into web research |
| web_fetch | **BLOCKED** | F1 correction: prevents drift into web research |

## Environment Controls

| Variable | Value | Purpose |
|----------|-------|---------|
| SWARM_WORKSPACE_ROOT | `C:\Users\rober\Projects\swe_probe_work\f1_pilot\run_1_fresh\repo` | Agent workspace boundary |
| SWARM_MEMORY_INJECT | `0` | Disable memory injection |
| SWARM_AUTONOMY | `0` | Disable autonomous actions |
| SWARM_NO_TOASTS | `1` | Disable desktop notifications |
| SWARM_SEMANTIC_CACHE | `0` | Disable semantic cache |
| SWARM_GENETIC_MUTATION | `0` | Disable genetic mutation |
| SWARM_EVOLUTION | `0` | Disable evolution |
| SWARM_F1_NO_WEB_TOOLS | `1` | **F1 correction**: strip web tools from coder agent |

## Architecture Invariants

1. **Evaluator is OUTSIDE target workspace** — agent cannot modify evaluator
2. **Prompt describes BEHAVIORAL defect** — no exact implementation fix disclosed
3. **Target workspace is at authorized base commit** — clean, verified before observation
4. **Qdrant is unchanged** — no memory/lesson mutation during observation
5. **Frozen experiment files unchanged** — EXPERIMENT_J.md remains at 20a1989b
6. **web_search/web_fetch unavailable** — F1 correction prevents web research drift

## Lesson/ActiveLessons Injection State

- SWARM_MEMORY_INJECT=0 — no memory injection at runtime
- No ActiveLessons delivered (F1 is a no-lesson baseline)
- Qdrant memory store is not queried for lessons during F1

## Valid F1 Observations Before This Run

- Observation 1: EXCLUDED (infrastructure-invalid: timed out, web_search/web_fetch drift)
- Valid observations: **0** (as of 2026-09-25 when this document was created)
- **Reconciled count (2026-09-26):** 10 valid, 20 protocol observations
  completed out of 20. 0 remaining. See LEARNING_EXPERIMENT_STATE.md §9.16
  and §9.28 for authoritative current counts derived from result files.
  The 21st supplementary execution is excluded from the official 20-observation dataset.

## Pre-Flight Checks (run automatically before observation)

The harness runs these checks before invoking the model:
1. Target workspace exists and is at the authorized base commit
2. Target working tree is clean
3. Evaluator exists outside target
4. F1 control environment is correct
5. Prompt does not disclose exact implementation fix
6. Evaluator correctly detects bugs in target code (sanity check)
7. No contamination files in target workspace

## Confirmation

- [ ] This command has NOT been executed
- [ ] No F1 observation has been created
- [ ] No F1 trajectory has been created
- [ ] No F1 result has been created
- [ ] Human review required before execution

---

**NOT EXECUTED — AWAITING HUMAN APPROVAL**
