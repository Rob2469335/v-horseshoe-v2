# Experiment J N=2 Readiness Audit

**Date:** 2026-09-28  
**HEAD:** `c75c35bf833667979471f7ae52ec9a1e14685551`  
**Branch:** `master`  
**Author:** Read-only automated audit  

---

## 1. Repository State

| Item | Value |
|------|-------|
| Branch | `master` |
| HEAD | `c75c35bf833667979471f7ae52ec9a1e14685551` |
| Working tree | Dirty: `start-dev.ps1` modified; 60+ untracked |
| Candidate file | Untracked (not in git) |
| Backup | `data/prompt_repairer_candidates.json.bak-20260928-213507` exists |

---

## 2. Experiment Authority

| Phase | Status | Evidence |
|-------|--------|----------|
| F0 | FROZEN | `docs/EXPERIMENT_J.md` (commit `20a1989b`) |
| F1 | CLOSED (20/20) | `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` |
| F2 | PENDING | `docs/LEARNING_EXPERIMENT_STATE.md:25` |
| N=2 | NOT AUTHORIZED | No document authorizes N=2 |

F2 blockers from `docs/LEARNING_EXPERIMENT_STATE.md:27`: "F2 Engineering Work: NOT IMPLEMENTED. Promotion fixture, exclude_ids, delivery instrumentation, fresh-process harness, replay code."

---

## 3. Candidate State

| Field | Value |
|-------|-------|
| ID | `a8f69f262bae` |
| Status | `CANDIDATE` |
| Evidence runs | 4 |
| Evidence tasks | `["pypa__twine-1066", "f1_pilot/run_1_fresh"]` |
| Governance version | 2 |

Surviving rollout_ids: `21b63a3f`, `a690605b`, `9a306e32`, `98a07653` (all `run_id: "unknown"`, no verifiable execution records).

---

## 4. Promotion Gate

| Gate | Threshold | Status |
|------|-----------|--------|
| Evidence runs | `MIN_EVIDENCE_RUNS = 3` | PASS (4 >= 3) |
| Evidence tasks | `MIN_EVIDENCE_TASKS = 2` | PASS (2 >= 2) |
| `SWARM_RECEIPT_KEY` | Required | **NOT SET** -- promotion blocked |

Source: `prompt_repairer.py:47,50,1150-1156`.

---

## 5. Web-Tool Safety

`SWARM_F1_NO_WEB_TOOLS=1` is set by `run_repair_task.py:441` directly in `os.environ` before backend import. `_strip_web_tools_for_local_analysis()` at `_agent_helpers.py:84-88` strips `web_search`/`web_fetch` when this env var equals `"1"`. F1/F2 runs use `run_repair_task.py`, NOT `start-dev.ps1`.

---

## 6. Infrastructure

| Service | Status |
|---------|--------|
| Backend :8000 | UP |
| Model :8080 | UP |
| Embedding :8081 | UP |
| Reranker :8082 | UP |
| Vision :8083 | UP |
| Qdrant :6333 | UP |

---

## 7. Scientific Validity

INVALID observations receive zero capability credit. VALID observations receive endpoint measurement. Rediscovery rule excludes pre-delivery edits.

---

## 8. Blockers

1. **SWARM_RECEIPT_KEY not provisioned** -- promotion hard-blocked (fail-closed)
2. **F2 engineering not implemented** -- no promotion fixture, exclude_ids, delivery instrumentation, replay code
3. **No verified ACTIVE lesson L exists** -- F2 prerequisite unmet
4. **No N=2 authorization** -- no document authorizes N=2

---

## 9. Conclusion

**BLOCKED -- DO NOT RUN N=2**

Four independent blockers prevent N=2. The candidate cleanup is complete but insufficient to enable any experiment execution.
