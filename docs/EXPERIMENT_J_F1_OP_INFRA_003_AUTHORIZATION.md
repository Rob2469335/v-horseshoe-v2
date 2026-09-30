# F1-OP-INFRA-003 Authorization: Monitor/Retry Race Condition Fix

## Purpose
Authorize a narrow infrastructure correction to fix a harness monitor/retry race condition that caused duplicate `run_id` entries under a single `rollout_id` during Experiment J N=1.

## Root Cause
The `_monitor` thread in `qwen_train/run_curriculum.py` watched **all** trajectory files in `data/trajectories/`. When it detected any trajectory with `status=completed`, it called `proc.kill()` on the CLI subprocess. This happened **before** the CLI finished consuming the NDJSON response stream, causing `stream_prompt_with_retry()` to receive an incomplete result and retry — spawning a second agent-loop invocation with a **new `run_id`** but the **same `rollout_id`** (inherited from `os.environ["SWARM_ROLLOUT_ID"]` set once at `eval_twine.py:119`).

## Authorized Correction
**Remove the `_monitor` thread and its `proc.kill()` call entirely.** The timeout is already enforced by `proc.communicate(timeout=...)`, making the monitor's kill redundant and harmful.

### Exact Change in `qwen_train/run_curriculum.py`
- Remove the `_monitor` thread implementation (lines 305–331 in original)
- Remove the `_threading` import
- Remove `_trajectory_dir`, `_start_ns`, `_killed`, `backend_at_timeout`, `model_at_timeout` module-level variables used only by the monitor
- Initialize `backend_at_timeout = True` and `model_at_timeout = True` before the `try` block instead
- Remove `monitor.start()` call
- Remove `monitor` variable
- Keep the existing `proc.communicate(timeout=timeout)` timeout enforcement
- Keep the `TimeoutExpired` exception handling with health checks

## Scope Limitations
This authorization **ONLY** covers:
1. Removal of the `_monitor` thread and its `proc.kill()` call in `qwen_train/run_curriculum.py`
2. Addition of regression tests in `tests/test_monitor_fix.py`

This authorization **DOES NOT** authorize:
- Changes to `docs/EXPERIMENT_J.md` (F0 remains frozen)
- Changes to `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` (F1-OP-INFRA-001, -002, etc. remain unchanged)
- Changes to `_workspace_is_external()` in `runtime_v2/api/_agent_routing.py` (separate issue)
- Changes to `docs/EXPERIMENT_J.md` (F0 frozen)
- Changes to `AGENTS.md`
- Changes to classifier, PromptRepairer, LessonManager, scoring, model config, timeout, horizon, task definition
- Running Experiment J N=1 or any new observation
- Restarting services
- Committing changes

## Testing Requirements
Before any observation resumes:
1. All 6 new regression tests in `tests/test_monitor_fix.py` must pass
2. All existing F1 infrastructure/evidence tests must pass (63 tests)
2. `ruff check . --select E9,F` must pass on changed files
3. `docs/EXPERIMENT_J.md` SHA-256 must remain `4EAFD2FAF2BA79078F8C1CAD7415DC1CEB2D24E753122A06FC2FA6AF76908337`
4. No changes to `swarm_os/lib/mcp/filesystem.py`, `runtime_v2/api/evaluation_bridge.py`, `swarm_os/services/prompt_repairer.py`, `swarm_os/services/lesson_manager.py`

## Authorization
**AUTHORIZED** for implementation and regression testing only.

**NOT AUTHORIZED** for:
- Running Experiment J N=1 or any new observation
- Committing changes without explicit human approval
- Any changes beyond the scope defined above

---

*Authorized: 2026-09-27*
*Scope: F1-OP-INFRA-003 — Monitor/Retry Race Condition Fix Only*
*Related: F1-OP-INFRA-001 (SWARM_F1_NO_WEB_TOOLS), F1-OP-INFRA-002 (Workspace Routing)*