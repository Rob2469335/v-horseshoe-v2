# WORK_LOG.md — Historical Project Memory

This file is the human-readable historical project record. Historical material was migrated out of AGENTS.md during the 2026-09-28 documentation restructuring.

**Migration artifact:** `AGENTS_LEGACY.md` (immutable byte-for-byte copy of the original AGENTS.md before restructuring)

**AGENTS_LEGACY.md SHA-256:** `F0DDF84CC876EDD8574AB63568F92045F2798F2AA3F40FF306939E42A1E1731E`

**Migration date:** 2026-09-28

**AGENTS_LEGACY.md should only be consulted for migration/recovery verification.** Agents should NOT load AGENTS_LEGACY.md routinely. Historical information should be added to WORK_LOG.md rather than AGENTS.md.

---

## Original AGENTS.md Structure (Pre-Migration)

The original AGENTS.md (6,522 lines) contained these major historical sections that have been moved here:

1. **MASTER PROJECT ROADMAP** — Architecture phases 1-6 + post-phase-6 repair sequence (Phases 7-16)
2. **Validation Program** — The validation sequence for proving the system works
3. **Current Validation Status** (historical snapshots)
4. **Experiment J — Learning-Loop Validation Definition** (conceptual, pre-execution)
5. **Frozen Historical Twine N1** — Frozen evaluator + historical counts
6. **Twine Smoke-Test Incident** — Invalid/aborted attempts
7. **Architecture Status** — Audit records
8. **Do Not Drift Rules** — Future agent constraints
9. **Experiment Protocol Governance** — F0/F1/F2 authority rules
10. **Current Next Step** (historical snapshots)
11. **Machine Specs** — Hardware documentation
12. **PROTECTED PATHS / DIRECTORIES** — Cleanup safeguards
13. **Standing Building Rules** — HARD PROHIBITIONS, REQUIRED PROCESS, EVIDENCE-FIRST, SEAM-LEVEL, VERIFICATION CADENCE, 0/N SCORE, VERIFICATION STANDARDS, CONVENTIONS, ENVIRONMENT NOTES
14. **Lint / CI** — Ruff configuration
14. **Long jobs** — Detached + concurrent rule
15. **Durable writes** — Atomic write rule
16. **Verify-before-assume** — Project standing habit
17. **Module Map** — Detailed module inventory with line counts
18. **Key Patterns** — Agent loop, tool decision, JSON extraction, delegation, healing, memory, async, control plane, repository pattern
19. **Qwen3.5 Local Model** — Model details, alias, thinking mode, server, fallback, analysis+edit cloud routing
20. **LIVE WORK LOG** — Real-time agent status updates
21. **ROBS_4B — PERSONAL MONEY-GOAL ADVISOR MODEL** — Research-backed plan
22. **RUNPOD OPERATIONS** — Lessons learned, step-by-step workflow, CUDA config, training flow
23. **V6 DATASET PLAN** — Audited + corrected
24. **NIGHTLY SELF-IMPROVEMENT DISTILLER** — Audited + corrected, blocker identified
25. **V4 Adapter Evaluation** — Base vs adapter loop-rate investigation (settled)
26. **V4 Loop Investigation** — Serving-config fix via `--reasoning-budget`
27. **Recent Changes** — Chronological fix records (2026-09-27 through 2026-08-03)
28. **Bug Fixes (Codebase Analysis)** — Detailed bug fix records with file locations
29. **Evidence Model** — Three-layer identity model (task_id, rollout_id, run_id)
30. **Learning / Explanation Rule** — Pedagogical framework
31. **Self-Healing & Self-Learning Fixes** — Auto-repair log entries
32. **Historical Auto-Repair Entries** — Thousands of [AUTO-REPAIR], [ROLLBACK-COMPLETED], [CANARY-FLAGGED] entries
33. **Rule Entries** — Agent rule violation logs

---

## Major Historical Sections (Summarized)

### Architecture Phases 1-6 + Post-Phase-6 (Phases 7-16)

**Phase 1 — Core Event & Record System:** EventRecord, EventStore, EventBus, OutcomeRecord, PolicyRecord. Status: COMPLETE / VERIFIED (hostile audit: 22 PASS, 2 WARNING).

**Phase 2 — Decision / Healing Engine:** Evaluator, ExperimentRunner, PromotionEngine, RollbackEngine, HealingEngine, PolicyResolver, DecisionGate. Status: COMPLETE / VERIFIED (L1-L6 signed off, governance suites committed, promotion path exercised in isolation at `a1b8f471`, 162/162).

**Phase 3 — Organism Runtime:** Orchestrator, ToolRegistry, AgentRuntime, TaskSession, OrganismSnapshot. Status: COMPLETE / VERIFIED (agent-loop E2E, god-module split 1589 tests, durable checkpointing 11 tests, live delegation records).

**Phase 4 — API Services:** `swarm_os/api/` + `runtime_v2/api/`. Status: COMPLETE (API audit rounds with revert-proof tests; governance/security gate complete).

**Phase 5 — Frontend:** `organism_console/` (CLI live) + `start-console/` (web/SSR experiment). Status: COMPLETE per documented scope.

**Phase 6 — Infrastructure & Runtime:** llama/model services, local robs4b routing, Qdrant, runtime/process supervision. Status: COMPLETE / CLOSED.

**Post-Phase-6 Repair & Verification (Phases 7-16):**
- Phase 7: Crash-safe transaction recovery (owned journal with begin/snapshot_saved/qdrant_applied/committed states)
- Phase 8: Single-token governance / trusted receipts (HMAC-SHA256 over complete canonical state, env-only SWARM_RECEIPT_KEY, fail-closed)
- Phase 9: Reflection/diary restoration (production diary write path preserved, distiller rewired to real agent failures)
- Phase 10: Remove fragile import hacks
- Phase 11: Fix adversarial tests (swallowed tool failures, wrong-shape mocks)
- Phase 12: Test the tests (seam-isolation acceptance proof, revert-proof rule)
- Phase 13: Whole-tree prompt-injection audit (16 defects fixed, raw historical memory removed from worker prompt)
- Phase 14: E2E governance test (test_autonomy_e2e.py — 10 sequential checkpoints)
- Phase 15: Repository hygiene (dead-code sweep ~130 files, disk prune ~6.5 GB, zero-lint sweep)
- Phase 16: Final verification (hostile audit FAILED on 4 findings; all repaired: raw memory reach, forgeable HMAC key, partial evidence independence, partial rollback; 2026-09-18 V3 verification: 108 tests passed)

### Validation Program (Historical Sequence)

```
Governance/Security
→ Hostile Audit
→ Observability fixes
→ Evaluation/Learning bridge
→ Twine N1 analysis
→ Twine N5
→ Experiment J
→ Click
→ Pyfakefs
→ Sandbox Bounds
→ 25-rollout measurement
```

### Experiment J — Learning-Loop Validation (Conceptual, Not Run)

**Purpose:** Validate whether PromptRepairer actually teaches the worker something that changes subsequent behavior. NOT simply another SWE-bench task.

**Conceptual sequence:**
1. Pre-learning behavior — worker encounters task pattern, record behavior
2. Failure — genuine, attributable failure on relevant task
3. Learning — failure enters PromptRepairer; no manual injection/promotion
4. Evidence/validation/promotion — follow existing evidence requirements
5. Post-learning task — comparable/relevant task after lesson learned
6. Behavior comparison — did learned lesson change behavior in expected direction?
7. Causal attribution — preserve raw evidence to distinguish learning from lucky success

**Status:** Defined conceptually; detailed execution protocol not recovered/approved; NOT RUN.

**Frozen N1 (separate from Experiment J):**
- Task: `pypa__twine-1066`
- Base commit: `4a1fc064a7899872ee845df6a8810bb51a6845ac`
- Frozen evaluator: `qwen_train/run_twine_eval.py`
- Frozen SHA-256: `C8388FF9C317944AA53C163C5149455B177F95F59A75DE7C1E13AD88AD041AC2`
- Historical trajectory count: 216
- Historical event count: 724
- **WARNING:** Never rerun or modified; if current files differ, report discrepancy

### Twine Smoke-Test Incident

Two controlled smoke attempts were INVALID/ABORTED:
1. `eval_twine.py` without `--test-patch` — baseline reported 3 passed, evaluator correctly rejected
2. `eval_twine.py` with test patch — interrupted, backend unreachable at timeout health check (`skipped:INFRASTRUCTURE`)

Neither counts as N1, N5, or planned measurement; neither counts toward 25-rollout; neither constitutes learning evidence.

### F1 Pilot — CLOSED (20/20 Observations)

Per `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` and `docs/LEARNING_EXPERIMENT_STATE.md`:
- 20/20 protocol observations completed
- 10 valid (reaching qualifying first-edit endpoint @ ATIF Step 4)
- 10 infrastructure-invalid
- F1 scientific protocol frozen; only operational runtime parameters modified
- Post-F1 governance baseline established 2026-09-27

### Recent Changes Chronological (Selected Key Entries)

**2026-10-10:** CI `test` job recovered from 120 pre-existing failures to a green test job (GitHub Actions run `38015219709` @ `c5ec7d5f`: `test` 4734 passed, 102 skipped, 1 xfailed, 0 failed; `sota-enforcement` and both `frontend` jobs passed, but the overall workflow remains RED because `security-audit` fails — see the next entry) across seven commits (`5e993f5d`, `1ebbb77e`, `f25ab662`, `c2980719`, `5c5a14a4`, `d36a4b78`, `c5ec7d5f`). Root causes fixed: hardcoded `C:\Users\rober\...` roots in F2 doc/provenance tests -> repo-relative + host-artifact skip guards; routine read budget restored to the documented 12 turns (decoupled from global `MAX_TURNS=24`); analysis-cloud default tests updated to the documented 2026-09-22 Nemotron NIM lead (`WORK_LOG` 2026-09-22); `_agent_helpers` `str.lstrip("./")` path-normalizer defect (stripped leading `/` from POSIX absolute paths); `settings.ssl_verify` default corrected to True (was False — TLS verification off); `event_log_repo._atomic_write_text` given a `FileLock` + the canonical unique-temp write; `filesystem.resolve_in_sandbox` now refuses absolute paths outside the sandbox instead of rebasing them under root on POSIX; `f2_evidence._resolve_artifact_path` rejects Windows drive paths host-independently; `project_map.build_project_map` re-sourced to the current AGENTS.md Repository Map (the 2026-09 restoration had orphaned it); `start-dev.ps1` startup reporting made evidence-based (readiness flags + FAILED branches + import-probe tee + rotation) and PID-scoped lifecycle wired in (`lifecycle.ps1`/`unified-stop.ps1`); mutation-loop related-test cap raised 8->20 (tool_executor's canonical test was being dropped); reflection tests updated to the post-`717661a2` `PromptRepairer.process_failure` contract; `test_github_research`'s end-to-end call made hermetic (the live `gh` CLI refuses to run in Actions without `GH_TOKEN`).

**2026-10-10 (security-audit blocker — residual risk, unresolved):** `pip-audit -r requirements.txt` reports `nltk 3.10.3` PYSEC-2026-3740 / CVE-2026-81726 / GHSA-8mgp-746c-j5xp (path traversal in nltk model-artifact APIs when `pathsec` is enabled). Authoritative evidence (fetched 2026-10-10): the GitHub advisory GHSA-8mgp-746c-j5xp states `vulnerable_version_range: <= 3.10.3` with **no patched version**, and `api.osv.dev` returns 3.10.3 as affected; the PyPA record's `fixed: 3.10.3` field is inconsistent with the GHSA and does **not** clear 3.10.3. PyPI's latest nltk release is 3.10.3 (released 2026-08-13; no newer release exists), so **no published patched release is available** and no upgrade closes the finding. Dependency path: `crawl4ai (>=0.9.0, resolved 0.9.4) -> nltk>=3.9.1`; crawl4ai is imported by `swarm_os/lib/mcp/web_search.py` (and referenced by `swarm_os/services/rv_finder/parsers.py`, `swarm_os/api/api_features.py`). The repository imports `nltk` nowhere directly (git grep: no matches). **Reachability of the vulnerable model-artifact APIs is NOT ESTABLISHED**: crawl4ai's internal use of nltk was not inspected and no application path was shown to pass attacker-controlled paths to those APIs — an absence of evidence, not evidence of unreachability. The finding is **not suppressed**: the CI workflow explicitly forbids suppression/exclusion, so adding `--ignore-vuln` would be a **security-policy change**, not a routine fix. Candidate remediations, all **REQUIRES AUTHORIZATION** and none performed here: (a) a policy decision to accept/ignore this specific advisory with documented justification; (b) an evaluated `crawl4ai`/`nltk` dependency change (pin, replacement, or removal) with compatibility testing; (c) an upstream nltk patch, if/when one is released. The only *proven* limitation today is the absence of a published patched release; a safe repository-level remediation has **not been established** (and is not proven impossible).

**2026-09-27:** F1 governance baseline finalized; N1/eval_twine migration audit recorded; eval_twine grant cleanup aligned with granted scopes; eval_twine now requires test patch; Experiment J observation N=1 recorded (valid behavioral observation, rollout_id `64e948c4-6039-4412-b67f-4ba329e784ff`)

**2026-09-24:** Rollout provenance, run_id attribution, tool-decision cap (512 tokens); N5 provenance audit PASS/CLOSED; 512-token cap validated (model enters tool loop, 4 tool calls <240s)

**2026-09-23:** Evaluation bridge implemented and production-path validated in isolation (162/162 tests); evidence identity fix (`_evidence_key()` prefers rollout_id); learner artifact representation-boundary defect fixed (deterministic `_derive_learner_artifact`, governance version v2)

**2026-09-22:** Nemotron 3 Ultra migration verified live; synchronous `PromptRepairer.process_failure()` await bug fixed

**2026-09-17:** Local 4B repair task #1 — cache exonerated, workspace isolation confirmed, genuine n=1 edit-grounding weakness captured

**2026-09-15:** First trustworthy SWE baseline; 0/14 was serving artifact; 7 silent harness defects found+fixed; synthetic pool at CEILING; Docker-free real-bug pool validated (3/3 runnable Python instances)

**2026-09-15:** `robs4b` does NOT hold personal facts — it FABRICATES a biography (measured); anti-fabrication rule mandatory for retraining

**2026-09-15:** 14-task SWE-rebench pool built; sandbox bound identified (agent tools hard-bound to project root); SWARM_WORKSPACE_ROOT opt-in fix implemented

**2026-09-14-13:** Long jobs DETACHED + CONCURRENT rule codified; synthetic pool at ceiling; phantom failures identified; contamination filter PASS (append-only hash denylist); real-bug harvester built; atomic durable writes (AGENTS.md 0-byte incident); memory daemon traceback noise fixed

**2026-09-14:** Self-learning CLI — ATIF trajectory capture, scoped Serena MCP grant, gold miner + north-star metrics, self-learning score + paired significance, pathway evidence, lesson-admissibility gate; EXPERIMENT A (T1→T2) = `no_signal`; EXPERIMENT B (memory ON/OFF) = `no_signal`; Fix curriculum (31 deterministic bug kinds, ~100% pass = benchmark too easy); 3-pool candidate hardening (130 sound / 16 unsound caught); free-first fallback cascade; DeepSeek model-name refresh; memory-ablation gate; strata discipline

### Key Technical Fixes (Selected)

- **Qdrant bound to loopback** (was 0.0.0.0, unauthenticated, LAN-exposed) — fixed via `QDRANT__SERVICE__HOST=127.0.0.1`
- **Security audit round** — 4-agent audit, 16 verified defects fixed (AST security gate bypass, .env copied to DangerRoom, access-control gaps, XSS, PowerShell denylist bypass, Crawl4AI SSRF, non-constant-time token compare)
- **Self-healing loop closed end-to-end** — tool failures now reach every consumer (events.jsonl, diary, turn-budget)
- **Coordinator short-circuits fixed** — hard code-level guard prevents `action=final` on stale episodic memory
- **Analysis agent web_search mandatory** — prompt + deterministic guard; internet goals reject every `final` until web_search+web_fetch succeed
- **Goal verification suite** — now snapshots tree before each attempt, only verifies changed_this_attempt
- **Fallback chain credential leak fixed** — per-fallback dicts with own api_base/api_key
- **OPENAI_API_BASE quotes in .env** — stripped on load, was breaking entire cloud chain
- **get_live_fallbacks missing** — added, was causing ImportError on every tool decision
- **CPU P-core single-slot optimization** — `-np 1 -t 2 -tb 4 -ngl 0` for full tok/s
- **Orchestrator/LLM timeout bumps** — reranking bursts saturating DDR5
- **Tool-decision timeouts retry** — single-slot queueing not dead model
- **250-token cap raised to 4096** — was truncating tool-decision JSON
- **asyncio.wait_for → asyncio.timeout()** — 21 calls migrated
- **9 per-request httpx clients → pooled singletons** — connection pooling
- **start-dev.ps1 $specArgs bug** — array built but never passed to Start-Job
- **--cache-reuse 1024 on MTP GGUF** — unsupported, now conditional
- **AsyncQdrantClient timeout** — added 10s explicit timeout
- **_ensure_collection retry logic** — 3-attempt exponential backoff
- **ngram-mod n-match settled at 16** — 24 too large, 8 too small
- **Memory bridge print() → logging** — structured backend logs
- **Reflection_loop 402 retry** — now logs at debug level
- **GraphRAG/consolidation slot-busy timeouts** — now catch ReadError/ReadTimeout first, log at debug
- **MemoryDaemon/EmbeddingService startup races** — 15s sleep + 3-attempt backoff
- **Crawl4AI web fetch empty on JS-heavy** — added page_timeout=15000, word_count_threshold=10

### Self-Healing / Self-Learning Architecture (Completed L1-L6)

**L1 — Structural verifier on agent `final`:** Rejects placeholder finals; rejects finals citing unread .py paths
**L2 — Diagnose-before-patch fix_class gate:** FIX_PS_TERMS first, MV only when no structural signal
**L3 — Real test-pass signal:** DangerRoom.run_tests() returns real exit code; no-test fallback = 0.5 discounted
**L5 — Trust-gated reflexion consolidation:** Same-fact = REINFORCE; CONFLICT = OVERWRITE; genuine retrieve failures logged+flagged
**L6 — Process-separated fail-closed security gate:** AST scan in separate `python -I` subprocess; exit 0 = allow, anything else = DENY

### Autonomous Layer Build Order (All 5 Complete)

1. Policy (autonomy_policy.json) ✅
2. Server-side watch-loop (SWARM_AUTONOMY=1 default) ✅
3. Durable checkpointing (data/checkpoints/<id>/checkpoint.json) ✅
4. Signal-gated rollback Phase A+B (canary registry + off-tick auto-trigger) ✅
5. Reranker/distillation learning upgrades (rerank=primary rank, distiller cloud chain aligned) ✅

### Model Evolution History

- **2026-08:** Qwen3.5-4B-MTP became default (~21 t/s ngram-mod, ~12 t/s MTP 2x)
- **2026-08-30:** V4 adapter completed (115 steps, loss 1.134, adapter 6.3 MB)
- **2026-09-01:** V5 pivot to native trace generation (67 commits → 64 records, loss 1.016)
- **2026-09-02:** V5 exam results — 9/10 grounded content with `--reasoning-budget-message`
- **2026-09-05:** OpenVINO build blocked by Windows Defender (unsigned binaries)
- **2026-09-02:** V6 dataset plan audited/corrected (LLM-retrieval 6-10 files, trajectory-vs-diff as A/B pilot)
- **2026-09-02:** Nightly self-improvement distiller — historical trajectory reconstruction impossible (run_id missing from events), corrected architecture defined
- **2026-08-31:** V4 loop investigation settled — `--reasoning-budget 600` eliminates loop at serving time (base 10/10, adapter 9/10 finish=stop)
- **2026-08-30:** V4 adapter evaluation — loop is inherent Qwen3.5 property (base 8/10 vs adapter 7/10 looped)

---

## Experiment J / F1 History

### F1 Authorization (docs/EXPERIMENT_J_F1_AUTHORIZATION.md)
- F1-OP-001: 20 independent no-lesson pilot runs
- F1-OP-002: Pilot task = repair_task1 / sandbox_bounds @ base commit 45d9f619
- F1-OP-003: relevant_file_set = {swarm_os/lib/paths.py}
- F1-OP-004a: Pilot observation horizon = 12 ATIF decision steps, right-censor at step 12
- F1-OP-004b: Censored observations contribute value 12 to P95 set; all 20 runs contribute
- F1-OP-004: P95 = nearest-rank empirical P95, n=20, rank=19; k = min(12, max(8, P95))
- F1-OP-INFRA-001: SWARM_F1_NO_WEB_TOOLS=1 strips web tools on non-internet goals
- F1-OP-INFRA-002: Fresh workspace per attempt; workspace_root enforced via headers
- F1-OP-INFRA-003: Backend liveness monitoring with PID identity; health gate with /readyz + /health

### F1 Observations (20/20 Complete)
- 10 valid observations (all reaching qualifying first-edit endpoint @ ATIF Step 4)
- 10 infrastructure-invalid observations
- Observation 1: infrastructure-invalid (web-tool drift)
- Observation 2: qualifying first-edit endpoint @ ATIF Step 4 (filesystem.patch on swarm_os/lib/paths.py), patch rejected by read-before-write guard
- Observations 7-11, 13-16: VALID, endpoint @ ATIF Step 4
- Observations 18-20: infrastructure-invalid (timeout, rejection-loop stall)

### Post-F1 Governance Baseline (2026-09-27)
- SWARM_AUTONOMY=0, SWARM_GENETIC_MUTATION=0, SWARM_EVAL_TICK unset/disabled
- SWARM_EXPERIMENT_J_ARM unset/disabled, SWARM_F1_NO_WEB_TOOLS unset/disabled
- SWARM_EVOLUTION=0
- Verified in restarted backend PID 20332

---

## ROBS_4B Personal Model History

**Vision:** 4B Qwen3.5 adapter specialized to Rob — codebase + income/money goals
**Core decision:** HYBRID (RAG + fine-tune) — fine-tune = behavior/knowledge of user's world; RAG = live facts; NEVER bake secrets
**3-layer stack:** Layer 1 (codebase specialist, DONE V6), Layer 2 (money-goal personalization, NEW), Layer 3 (live personal/money data RAG, already built)
**Anti-fabrication:** Facts live in memory store (RAG), never weights; target policy = retrieval-first; three ways to reach RAG (auto-inject, model-initiated, deterministic pre-fetch); DPO pairs with fabricated biography as rejected exemplar
**Tool-use training:** Wait for measured failures from real SWE-rebench pool (synthetic pool at ceiling)
**Routing caveat:** Tool DECISION for analysis/edit agents routes to CLOUD (deepseek-flash); robs4b only decides for coordinator/planner/tool-runner/tool-maker

---

## RunPod Operations History

**Primary objective:** Cheapest Community Cloud GPU for 4B GGUF + llama.cpp + Twine (max $0.22/hr)
**Fixed first choice:** RTX 3070 ($0.13/hr, 8 GB VRAM)
**10 Gates:** Pod access → Direct SSH → Verify actual GPU (nvidia-smi) → CUDA → llama.cpp → Model download/verify → Start server → Health → Real GPU offload (MANDATORY) → Generation → Twine
**Key traps:** SSH proxy denies key (use direct), SCP truncates at ~94MB (use -O flag), multi-command SSH hangs (one command per call), PowerShell mangles Python (write .py scripts), sed fails on paths (use python), exam writes to C:\ path on Linux (fix BOTH paths), nohup doesn't write output (use nohup cmd > log 2>&1 &)

---

## V4/V5/V6 Dataset Architecture

**V4 — Oracle Context Pivot:** Fixed fatal structural flaw (V2/V3 only had summaries, no raw code). Pseudo-Random Wide-Window Slice (400-line window, bug position randomly shifted). Zero-shot + budget forcing (600 token cap, force transition cue). Training OOMs: context ceiling ~2528 tokens on Meteor Lake iGPU; global context budgeting by real tokenizer output (Python ~3.0-3.3 chars/token). V4 training completed 115 steps, 7099s, loss 1.134.

**V5 — Native Trace Generation:** 67 commits traced → 66 valid + 1 excluded. Human grounding audit: 64 accepted / 2 rejected. 64 records, token range 967-1630. Training completed: 320 steps, loss 1.016. Exam results: diag_c 5/10 (vs V4 3/10), diag_c+r 9/10 (vs V4 4/10). Breakthrough: `--reasoning-budget-message "Your_final_answer_must_begin_with_DIAGNOSIS_and_name_the_file"` → 9/10 content-only grounding.

**V6 — Audited + Corrected:** LLM-retrieval 6-10 relevant files (not 3-file cap), trajectory-vs-diff as A/B pilot, ~300 examples as pilot. Phase 1 first, Phase 2 behind retrieval change.

---

## Critical Lessons Learned (Preserved from Historical Record)

1. **Never accept a score without naming the mechanism** — 0/N from harness defects looks like incapability
2. **Infrastructure failures must remain separate from capability failures** — classify, stop, repair, rerun cleanly
3. **Verify before assume** — independent verification caught real problems (evolution staging mismatch, Build 3/4 history)
4. **One fix, one commit, one verification** — batching hides which change is wrong
5. **Context-window meter is not provider cache evidence** — claim no cache hit/miss without provider usage metadata
6. **Detached + concurrent for long jobs** — serial runs waste session time
7. **Atomic durable writes** — AGENTS.md found at 0 bytes mid-session; use atomic_write_text (tmp + os.replace)
8. **Phantom failures** — backend down = cli_ok=False masquerading as failures (filter cli_ok==True)
9. **Contamination filter** — append-only hash denylist is authoritative, not date/author heuristics
10. **Sandbox bound** — agent tools hard-bound to project root; SWARM_WORKSPACE_ROOT opt-in for external workspaces
11. **Semantic decision cache exonerated** — contamination was workspace visibility, not cache
12. **Edit-grounding weakness** — model reads file but generates edit substituting variable from different function
13. **Length-blind judge required** — judge preferred 5009ch confident-but-fabricated over 1400ch correct dense
14. **Path-string lost between reasoning and final answer** — generation-architecture / attention-recency issue
15. **Windows Defender destroys unsigned AI binaries** — cannot run experimental executables without exclusion
16. **Two `uvicorn` processes are NORMAL** — parent + child; killing parent kills backend
17. **Full stack must start with start-dev.ps1** — backend alone floods "All connection attempts failed"
18. **Unattended CLI runs must not prompt** — sandbox_repl is ALWAYS_CONFIRM; non-TTY stdin now auto-DENIES
19. **Runner timeout must stay WELL UNDER backend's (300s)** — 120-180s per item
20. **Diversity > quantity for learning signal** — keep flaky families out, boundary-check numeric verification

---

## F2 Replay Boundary Engineering — Implementation + Forensic Audit (2026-09-29)

Authoritative context: `docs/EXPERIMENT_J.md` (frozen F0), `docs/EXPERIMENT_J_F1_AUTHORIZATION.md`,
`docs/LEARNING_EXPERIMENT_STATE.md` §10 (F2 engineering checkpoint). Do not modify those three docs.

### 1. What exists on disk (reconciled from working tree, not from conversation memory)

| Item | Status | Evidence |
|------|--------|----------|
| Freeze primitives | On disk | `runtime_v2/services/f2_freeze.py` — **UNTRACKED in git** |
| Replay isolation | On disk | `runtime_v2/services/f2_replay.py` — **UNTRACKED in git** |
| Back-compat wrappers | Tracked + modified | `swarm_os/services/f2_freeze.py`, `swarm_os/services/f2_replay.py` (re-export runtime_v2) |
| FastAPI integration | Tracked | `swarm_os/app/main.py` lifespan calls `install_verified_replay_from_env()` (~line 522-541), fail-closed on error |
| Delivery gate sites | Tracked (in HEAD commit 9576f8a7) | `runtime_v2/api/agent_service_v2.py:2667-2668`, `runtime_v2/services/stream_runner.py:658-669,770-780` |
| F2 tests | Tracked | `tests/test_f2_freeze.py` (58), `tests/test_f2_replay.py` (25) |
| Promotion fixture / exclude_ids / X-derivation / fresh-process harness / delivery instrumentation | **NOT IMPLEMENTED** | No production/harness caller of `freeze_artifact()`/`persist_manifest()` exists; no `exclude_ids` anywhere |

**Git-state defect (confirmed):** `runtime_v2/services/f2_freeze.py` and `runtime_v2/services/f2_replay.py`
have never been tracked (not in any commit). Tracked code depends on them:
`swarm_os/app/main.py@HEAD` does `from runtime_v2.services.f2_freeze import FreezeVerificationError` at module top,
and the tracked wrappers re-export from `runtime_v2.services.f2_*`. A **clean checkout of HEAD cannot import
the backend or collect the F2 tests** (`ModuleNotFoundError: No module named 'runtime_v2'`).

### 2. Exact test baseline (run 2026-09-29)

```
python -m pytest tests/test_f2_freeze.py tests/test_f2_replay.py -v
```
Collected: 83 · Passed: 80 · Failed: 3 (deterministic, every run) · Errors: 0 · Skipped: 0

Failing tests:
1. `TestFreshProcessReplay::test_child_process_replays_frozen_artifact`
2. `TestParentChildDistinction::test_parent_contextvar_not_inherited_by_child`
3. `TestParentChildDistinction::test_verified_replay_via_env`

All three fail identically inside the child subprocess:
`ModuleNotFoundError: No module named 'runtime_v2'` at the wrapper's `from runtime_v2.services.f2_freeze import ...`.

**Root cause (proven):** `tests/test_f2_replay.py::_run_child()` spawns
`sys.executable -u <temp_dir>/_child_script.py` with `cwd=repo_root` but NO `PYTHONPATH`.
For a script executed from a temp dir, `sys.path[0]` is the script dir, not `cwd`. The editable install
(`pip install -e .`) exposes top-level `organism_console` and `swarm_os` only — `runtime_v2` is absent from
every editable finder MAPPING. So the child can import tracked `swarm_os.services.f2_freeze` but its re-export
target `runtime_v2.services.f2_freeze` cannot resolve.

**PYTHONPATH experiment (proven):** setting `PYTHONPATH=<repo_root>` for those same 3 tests → **3 passed**.
The failures are test-harness environment, but this is NOT a production-cannot-fail claim:
the future F2 fresh-process harness (unimplemented) would face the identical import requirement unless it sets
`PYTHONPATH`/`--app-dir`. Production `start-dev.ps1` sets `PYTHONPATH=$root` + `-m uvicorn --app-dir`, so the
current backend startup path is safe; a future child-process harness is unproven.

### 3. Forensic audit classification (independent, 2026-09-29)

**Classification: PARTIALLY VERIFIED.**

Confirmed (code + passing tests): manifest self-hash exclusion; manifest provenance fields;
atomic persistence (temp+`os.replace`+fsync); no LIVE fallback on verification failure;
frozen-data mutation isolation; explicit LIVE-vs-FROZEN env signaling; fresh-child does not inherit
parent ContextVar (logic correct; execution blocked only by import path).

NOT PROVEN / gaps:
- **T → X ordering:** no production pipeline. `render_active_lessons()` → freeze → persist → derive X is not
  implemented; X exists only as literal strings in `TestTXHashDistinction`.
- **Sole delivery authority:** `get_delivery_artifact()` is gated at 3 sites, but `swarm_os/brain.py:270,280`
  and `organism_console/core/repair_engine.py:656` call `render_active_lessons()` directly (bypass replay gate).
- **Multi-task isolation:** no concurrency test exists; `_f2_state` is process-global by construction.
- **Verifier identity check:** accepts a self-consistent manifest whose git_sha/task/arm differ from any
  authorized canonical identity (verifies internal integrity, not binding to an external identity).
- **Startup fail-closed end-to-end:** `lifespan` raises on corrupt manifest (main.py:536-541), but no test
  runs the lifespan with a corrupt manifest.

### 4. Contradictions surfaced (not resolved)

1. `docs/LEARNING_EXPERIMENT_STATE.md` §10 header says "DOCUMENTATION CHECKPOINT ONLY — no implementation
   performed", yet the F2 freeze/replay implementation, tests, and integration exist in the working tree and in
   commits `c8e7c20e`, `e27d65b`, `9576f8a7`. Authority doc is stale relative to the working tree. Higher
   authority = the repo implementation + tests for *what exists*; the doc remains authoritative for
   *what is not authorized yet* (promotion fixture, exclude_ids, harness, N=2).
2. `AGENTS.md` §43 "Experiment J F2 — PENDING" remains true for the scientific step (no ACTIVE lesson L) but
   does not record the on-disk replay-boundary engineering state above.
3. Report claim "62 freeze + 21 replay tests" vs actual **58 + 25** (recorded in §2).

### 5. Untracked probe files created during this session's investigation

`test_child.py`, `test_import.py`, `test_import2.py` (repo root, untracked, scratch). They call
`freeze_artifact`/`persist_manifest` and must NOT be counted as production callers. Recommend deletion or
exclusion before any commit.

### 6. Status (machine-readable)

| Item | Status | Evidence |
|------|--------|----------|
| F2 freeze/replay primitives | IMPLEMENTED (untracked on disk) | `runtime_v2/services/f2_freeze.py`, `f2_replay.py` |
| F2 tests | 80/83 pass; 3 env-blocked (PYTHONPATH) | §2 above |
| T → X pipeline | NOT IMPLEMENTED | no production caller / no exclude_ids |
| Delivery authority | PARTIAL (2 bypass sites) | brain.py, repair_engine.py |
| Multi-task isolation | NOT EMPIRICALLY TESTED | §3 |
| Phase 3 | NOT AUTHORIZED | prerequisites unmet: `SWARM_RECEIPT_KEY` missing, no ACTIVE lesson L |
| N=2 | MUST REMAIN STOPPED | no authorizing document |

---

## AGENTS.md Runtime Writer Architecture Audit (2026-10-02)

**Trigger:** Source inspection of `swarm_os/lib/agents_md.py`, `swarm_os/services/watch_loop.py`, `swarm_os/services/reflection_loop.py`, `swarm_os/healing/recovery_engine.py`, `runtime_v2/services/tool_executor.py`, `swarm_os/services/telegram_center.py`, `runtime_v2/services/project_map.py`, `runtime_v2/prompts/system_prompts.py`.

**What was discovered:**

1. **Five runtime writers target AGENTS.md**, not four as previously assumed:
   - `watch_loop._audit_write` → `update_agents_md` (locked + atomic)
   - `reflection_loop._record_rule_to_agents_md` → `update_agents_md` (locked + atomic)
   - `recovery_engine._record_to_agents_md` → `update_agents_md` (locked + atomic)
   - `tool_executor.skill_manage` → `update_agents_md` (locked + atomic)
   - `telegram_center._handle_learn_cmd` → **bare `Path.write_text` — no lock, no atomic staging**

2. **Historical 0-byte truncation incident:** AGENTS.md was found truncated to 0 bytes mid-session after `telegram_center._handle_learn_cmd` clobbered it (recovered from git; documented in `swarm_os/lib/agents_md.py` docstring).

3. **Telegram → AGENTS.md → project_map → system_prompt chain established:**
   - `telegram_center._handle_learn_cmd` appends verbatim user input from `/learn` command to AGENTS.md
   - `project_map.build_project_map()` reads AGENTS.md and extracts Architecture Overview + Module Map sections
   - `system_prompts._project_map_context()` injects the project map into agent system prompts for code_analyzer, researcher, coder, debugger, reviewer
   - Chain capability is `PROVEN IN CURRENT REVISION`; actual exercise in this deployment is `NOT ESTABLISHED`.

4. **`reflection_loop` marker:** Inserts under `## Self-Healing & Self-Learning Fixes` marker, which is currently absent in AGENTS.md (a first write would create it).

**Why it mattered:** The non-atomic writer in `telegram_center.py` created a data-corruption hazard. The chain means untrusted Telegram input can reach agent system prompts via AGENTS.md.

**Authoritative evidence locations:**
- Implementation: `swarm_os/lib/agents_md.py` (docstring + `update_agents_md`), `swarm_os/services/telegram_center.py` (`_handle_learn_cmd`), `runtime_v2/services/project_map.py`, `runtime_v2/prompts/system_prompts.py`
- Audit: This entry; `docs/TEST_PERSISTENT_STORE_ISOLATION_AUDIT.md` §6 (Qdrant isolation remediation notes)
- Remediation status: `REQUIRES AUTHORIZATION` per AGENTS.md §6.11 — making `_handle_learn_cmd` atomic, sanitising its input, or splitting AGENTS.md is not a cleanup and not an agent decision.

**Agent guidance preserved in AGENTS.md:** The four "Consequences for you" bullets remain in AGENTS.md §6 (maintenance section) as standing safety rules.

---

## Frozen N1 Artifacts (Do Not Modify)

- `qwen_train/run_twine_eval.py` — Frozen evaluator (SHA-256: `C8388FF9C317944AA53C163C5149455B177F95F59A75DE7C1E13AD88AD041AC2`)
- `qwen_train/N1_HARNESS_PROVENANCE.md` — Provenance record with 2 historical backend run_ids

---

## Protected Experiment J Documents (Unchanged)

- `docs/EXPERIMENT_J.md` — Frozen F0 scientific design (commit `20a1989b`)
- `docs/EXPERIMENT_J_F1_AUTHORIZATION.md` — F1 operational authorization
- `docs/LEARNING_EXPERIMENT_STATE.md` — Current learning experiment state

---

## Migration Summary

| Metric | Value |
|--------|-------|
| Original AGENTS.md | 6,522 lines |
| New AGENTS.md | 228 lines |
| AGENTS_LEGACY.md | 6,522 lines (immutable) |
| WORK_LOG.md | ~1,800 lines (this file) |
| Historical content preserved | YES (all in WORK_LOG.md + AGENTS_LEGACY.md) |
| Experiment J docs unchanged | YES (verified SHA-256) |
| Source code changed | NO |
| Subsystem AGENTS.md created | NO (none needed — no subsystem-specific rules found) |

## Machine Specs (verified 2026-08-30)

Moved here from  + "AGENTS.md" + @ during the 2026-10-01 instruction-architecture
remediation. This is a point-in-time hardware record, not a standing rule. The one
standing trap it corrects is retained in  + "AGENTS.md" + @ (Protected Paths section):
**this host has no discrete GPU**.

- **CPU**: Intel Core Ultra 5 135U (Meteor Lake) — 2 P-cores + 8 E-cores + 2 LP E-cores,
  12 cores / 14 threads, 1.60 GHz base / 4.4 GHz turbo
- **iGPU**: Intel Arc integrated (4 Xe-cores / 64 EU), shared system RAM (UMA)
- **NPU**: Intel AI Boost (OpenVINO rejected for Qwen3.5)
- **RAM**: 32 GB DDR5-5600
- **Driver**: 32.0.101.8991, Vulkan 1.4.356, D3D12, SM 6.7

**Correction (supersedes an earlier claim):** there is **NO Arc A770** on this
machine. The training GPU is the Meteor Lake integrated Arc iGPU using shared DDR5.

## Experiment J F2 — Engineering Closure and the Sample-Size Contract Gap (2026-10-05)

**Engineering freeze at this entry:** `cea3c1d1` (`origin/master`), F0 unchanged
(SHA-256 `4EAFD2FAF2BA79078F8C1CAD7415DC1CEB2D24E753122A06FC2FA6AF76908337`).

### Completed and published engineering (11 commits, `9fe968d1` … `cea3c1d1`)

- **Step 2b/2c — delivery-timestamp contract.** `parse_delivery_timestamp`: Unix
  epoch -> UTC -> **floored** whole second, strict `<`; numeric conversion guarded
  so no input raises; a 2025-01-01 plausibility floor. The generic canonical parser
  was deliberately NOT broadened.
- **Step 3 — F0 section 6 rediscovery.** `classify_run_rediscovery` is now on the
  verification path; a pre-delivery edit is named as rediscovery rather than
  reported as an empty post-delivery window. `_reconstruct_endpoint` and the
  `F2Result` schema were NOT changed.
- **Step 3 — test-isolation fix.** Root `conftest.py` autouse fixtures no longer
  share the test's own `tmp_path`; this repaired **11** pre-existing failures
  across 9 files.
- **Step 4 — distiller provenance persistence.** `SynthesisAttestation.to_dict()`
  omitted `distiller_reproducibility` and `from_dict()` never restored it; both now
  carry it. A populated round-trip regression test replaced a test that only
  asserted the empty default.
- **Step 4 — F2 clean-room leak closed.** `_store_decision_reflexion` was not gated
  by `is_replay_active()`, so ANY arm (T, X or the no-lesson calibration) could mint
  a PromptRepairer candidate from an ordinary decision failure.
- **Step 5/6 — workspace-mutation integrity evidence.** The arm receipt now carries
  the post-run dirty-path set plus `head_after`, `head_unchanged`, `refs_after` and
  an ignored-path sample, closing the commit / ref / ignored-artifact bypasses; and
  `_classify_workspace_mutation` maps the observation to
  allowed/expected/suspicious/prohibited/unknown with an integrity assessment.
  **Engineering assessment only — not an exclusion rule.**
- **Step 7 — `F2Bundle` persistence round-trip.** Identity fields are now pinned
  through `to_dict()`/`from_dict()`; a truncated bundle FAILS CLOSED. The
  authoritative assembly point is the **evaluation/admission** stage, not the arm
  worker (which cannot supply `task_outcome_report`, `EvaluatorAuthorization`,
  `implementation_bytes` or `horizon_k`).
- **Step 9/10 — calibration execution layer + adapter.** `run_calibration` is gated
  fail-closed on a `CalibrationAuthorization` verifying six fields (arm, task set,
  replicates, horizon, censoring convention, endpoint hash); reruns are limited to
  the closed infrastructure-cause set with every attempt retained.
  `make_calibration_runner` connects the runner to the F2 membrane and delegates
  the endpoint to the frozen `f2_endpoint.qualifying_first_edit`.
- **Retroactive ratification.** `F2-IMPL-AUTH-003` records that `bee672dc` and
  `68208c4f` were made before repository authorization existed and were later
  ratified; the provenance is preserved, not rewritten.

**Tests at this entry:** F2 suite **899 passed, 3 skipped, 0 failed**.

### Unresolved scientific issue — the paired-binary sample-size contract

**Status: UNRESOLVED. REQUIRES SCIENTIFIC AUTHORIZATION. Do not treat as solved.**

`docs/EXPERIMENT_J_F2_READINESS_AND_STATISTICAL_PLAN.md` states that the no-lesson
X/C0 calibration produces *"the empirical discordance `π_d` that sets required N"*.
That is **mathematically impossible**: for a task-PAIRED design,

> `π_d = P(T xor X) = p_T + p_X - 2*P(T=1, X=1)`

which requires **both** arms and the joint association. `p_X` alone leaves
`π_d` anywhere in `[0, 1]`. A no-lesson calibration has no T arm
(`CALIBRATION_ARMS = ("X", "C0")`), so it cannot supply `π_d`.

This also conflicts with the frozen sequence: **F0 section 10** has the no-lesson
pilot inform `k`/`n`, then **F1 freezes `k`, `n`, practical-effect** — and the
learning event comes AFTER F1, so no T observation can exist when `n` is frozen.

An earlier report proposed a `p_X`-derived bound on `π_d`; **that bound is invalid**
(it assumed T/X independence) and is withdrawn.

**What the pilot CAN legitimately estimate:** `p_X` (the headroom gate),
steps-to-first-edit (which informs `k` per F0 section 5, capped [8, 12]), task
heterogeneity, censoring / no-edit / invalid / infrastructure rates, and run-to-run
instability. **It cannot estimate `π_d`, `p_T`, or the association.**

**Also recorded:** `horizon_steps = 12` in the implementation is the observation
horizon and the F0 **cap**; it is NOT the frozen primary-endpoint `k`, which F1
selects from the pilot by the 95th-percentile rule.

### Task-population status

`qwen_train/curriculum/f2_endpoints.json`: **12 derived, 2 refused** (correct
fail-closed refusals — an ambiguous or non-matching reference fix). `reference_digest`
proves **artifact identity only**; reference *execution*, reference *acceptance*, and
empty/base *failure* are **NOT ESTABLISHED**.

### External prerequisites still outstanding

`SWARM_RECEIPT_KEY` (Q11) — NOT PROVISIONED; `SWARM_DISTILLER_MODEL` and
`SWARM_DISTILLER_WEIGHTS_DIGEST` (Q7) — unset; network/sandbox enforcement (Q9) —
host control, not established; contamination-resistant post-cutoff population (Q1)
— not acquired; Q10 calibration — not authorized, not executed.

---

---

## 2026-10-05 — F2 engineering completion, then documentation reconciliation

Two logical passes, both repository-side. Neither executed an experiment, authorized
anything, or fabricated evidence.

### Pass 1 — `e6562622` … `3218ffbc` (F2-IMPL-AUTH-021/022/023)

Repository-side F2 machinery completed and pushed. Recorded here because this file
previously had **no** entry for any of it.

| Commit | What |
|---|---|
| `26a450ef` | **AUTH-021** — the authoritative independent test-outcome evaluator (`qwen_train/f2_evaluator.py`, registered `f2_evaluator_report_v1`) |
| `e6562622` | **AUTH-022** — Q9 observation/verification/attestation layers; the model conversion-chain record |
| `3218ffbc` | **AUTH-023** — execution preflight, anti-reward-hacking integrity gate, report self-integrity, evidence-based readiness |

Two genuine defects were found and fixed rather than documented around:

* **Producer/verifier drift.** The evaluator emits `missing` for a declared test
  absent from the evidence, but three pre-existing consumers
  (`_derive_json_test_report_v1`, `_derive_task_outcome`, `_task_success_from_report`)
  each rejected `missing` as an unrecognised status. A legitimate report could be
  refused downstream as a protocol error instead of scoring as the failure it is. All
  three now delegate to one authority, `verdict_from_report_payload`.
* **Report forgery.** `parse_report` re-derived the verdict from the report's own
  `fail_to_pass` map, so rewriting `declared_result` on an all-passed map produced an
  internally consistent *forged* report. Reports now carry a SHA-256
  `report_digest`.

An existing worker test used a hand-written report fixture. It was replaced with a
genuinely evaluator-produced report rather than weakening the new check.

Suite: `pytest tests/ -k f2` → **1274 passed, 4 skipped** (baseline before: 1176 + 4).
Ruff `E9,F` clean.

### Pass 2 — documentation reconciliation and execution readiness

A contradiction audit against the real code found documentation that had drifted in
both directions: claims that implemented things do not exist, and one internal
contradiction. All findings were verified against source before being fixed.

**Contradiction corrected.** The readiness plan's §4 stated "R8 is unmet for every
task" while its own rows and §4b said `relevant_file_set` is derived for **12 of 14**.
R8 is delivered; **S8 evidence provenance is the sole remaining eligibility gate**.

**Stale "not implemented" claims corrected** (each verified against source first):
the arm runner, the F2 execution adapter, the delivery-abstraction remediation,
`exclude_ids`, the bundle-emission wiring, the "result-derivation protocol is a
governance gap" claim, and Q9's "not a code change I can make unilaterally".

**New:** `docs/EXPERIMENT_J_F2_OPERATOR_HANDOFF.md` — the single authoritative
readiness checklist and the exact 16-step execution order A–P, each step carrying
STATUS / OWNER / INPUTS / OUTPUT / FAIL-CLOSED / NEXT STEP.

**Conversion provenance made machine-checkable.** Previously a record naming only a
merged artifact plus free-text `detail` closed the link, so prose asserting a
converter could stand in for a recorded operation. The record now requires 13 named
fields and its recorded digests are cross-checked against the links the chain already
proves — a record naming a *different* adapter is `MISMATCH`, not a pass. Timestamp
ordering remains refused as conversion evidence. **No conversion script exists in the
repository and none was invented; the link stays `UNRECORDED`.**

**State and history reconciled.** `docs/LEARNING_EXPERIMENT_STATE.md` gained entries
D-17…D-20 (it had recorded none of the 2026-10-05 work); `AGENTS_LEGACY.md` was
**not** edited despite containing a timestamp-ordering inference about the conversion
— `docs/INFERENCE_TOPOLOGY.md` §2 now labels that inference `INFERRED` and states the
gap.

### The state this leaves behind

Preflight: **BLOCKED, 18 blocking findings** — 12 `OPERATOR ACTION REQUIRED`,
4 `EXTERNAL EVIDENCE REQUIRED`, 1 `PRIVILEGED HOST ACTION REQUIRED`,
1 `AUTHORIZATION REQUIRED`; plus 2 `NOT EXECUTED` and 16 `IMPLEMENTED`.

**A green software suite is not experimental readiness.** The repository can *verify*
isolation but not impose it, and can *validate* evidence but not produce it. S8
evidence, egress enforcement, an ACTIVE lesson, the receipt key, population
admission, curator authority and the conversion record all remain outstanding, and no
document or code path pretends otherwise.

Not executed: S8, Q10, Q12, Q13.
## 2026-10-07 - F2 VM isolation boundary hardening (F2-IMPL-AUTH-027)

**Repository-side F2 isolation implemented and tested.** A governed F2 P2
(`SWARM_F2_ISOLATION=1`) now actually **stops** the background subsystems rather
than relying on environment flags: MemoryBridge daemons, the codebase-index
self-heal daemon, external MCP init, the task scheduler, reflection, genetic
mutation, evolution, the autonomy watch-loop, intel, eval-tick, Telegram, chess
resume and the system-probe warmup are not started
(`swarm_os/app/main.py`). `f2_p2_environment()` marks the production P2 and sets
`SWARM_CODEBASE_INDEX=0`. A startup assertion fails closed unless Qdrant
(`127.0.0.1:6333`) and embedding (`127.0.0.1:8081`) are unreachable
(`runtime_v2/services/f2_runtime_guard.py`); Qdrant-backed tools are removed from
the arm surface. A narrow, non-proxy model gateway
(`qwen_train/f2_model_gateway.py`) exposes only
`POST /v1/chat/completions`, `POST /v1/completions`, `GET /v1/models` to the fixed
local upstream. 27 focused tests pass; the full F2 suite + agents smoke
(1438 passed / 5 skipped) is clean (recorded in F2-IMPL-AUTH-027).

**Provisioner hardened.** `qwen_train/f2_vm_provision.ps1` was audited and fixed:
it previously created **two** NICs (`New-VM -SwitchName` already adds one, then
`Add-VMNetworkAdapter` added a second). It now attaches **exactly one** NIC
(fail-closed if not), uses a **stateful** allow via `-IdleSessionTimeout`, adds a
catch-all deny in both directions, configures Secure Boot + vTPM, sets the
integration-service final state, and supports official install ISO or golden
VHDX. It remains non-executing by default and never downloads media. The script
is syntax-validated; the switch/VM were **not** created.

**Stopped at the correct boundary.** `Get-VM` returns no VMs, only the OS
`Default Switch` (Internal) exists, and **no official Windows install ISO is
present**. No VM/ACL/guest-egress property can be proven without the guest, so
the boundary is `BLOCKED - EXTERNAL PREREQUISITE`. Design and human prerequisites
are recorded in `docs/EXPERIMENT_J_F2_VM_ISOLATION.md`. No experiment was run; no
secret, `.env`, Windows Firewall, Avast, ACL, account, or host policy was touched.

## 2026-10-07 (follow-up) - F2 VM ACL verification gaps closed

**ACL construction corrected against Microsoft primary documentation.** The
provisioner's allow rule previously carried a stateful *intent* via
`-IdleSessionTimeout (New-TimeSpan -Minutes 5)` **without** `-Stateful $true` and
with the wrong parameter type. Microsoft Learn
(`Add-VMNetworkAdapterExtendedAcl`) confirms: `-Stateful <Boolean>` exists,
`-IdleSessionTimeout` is an **Int32 number of seconds** (not a TimeSpan), and
extended ACLs are stateless unless `-Stateful $true` is passed. The allow rule is
now `-Stateful $true -IdleSessionTimeout 300`; the catch-all deny in both
directions now uses the documented wildcard `-LocalIPAddress ANY
-RemoteIPAddress ANY` (`ANY` = all IPv4 **and** IPv6) instead of relying on
omitted address parameters. The provisioner now prints every stored ACL field
and **fails closed** unless exactly one outbound Allow exists, it is
`Stateful=$true`, it has a positive idle timeout, inbound+outbound catch-all
denies exist, and the allow weight outranks the deny weight.

**Static regression test added.** `tests/test_f2_vm_provision_acl.py` (8 cases,
`CODE PROOF` — no VM) detects each of the defects above plus the double-NIC
defect (`New-VM -SwitchName` already attaches one adapter). 35 focused tests
pass; ruff E9,F clean.

**Dry-run proven non-mutating.** `qwen_train/f2_vm_provision.ps1` invoked without
`-Execute` printed the plan and exited 0; `Get-VM` was 0/0 and `Get-VMSwitch` was
1/1 (only the OS `Default Switch`) before and after; no F2 adapter or NIC.

**Timestamp/clock analysis recorded** in
`docs/EXPERIMENT_J_F2_VM_ISOLATION.md` §11a: F2 timestamps are host wall-clock
(epoch/UTC) via `time.time()`/`time.gmtime()`; no strict cross-clock pass/fail
gate was found in the F2 path (`PROVEN`, source); guest clock-drift impact is
`NOT ESTABLISHED`; keeping Hyper-V Time Synchronization disabled is recorded as
a PROPOSAL to revisit only if strict cross-clock ordering is ever required.

No VM, switch, adapter, firewall, Avast, ACL, account, `.env`, or secret was
touched. Boundary remains `BLOCKED — EXTERNAL PREREQUISITE` (no official guest
install media).

## 2026-10-07 (preflight) - F2 isolation: timeout, clock, input and probe contracts

**Timeout contract defined and corrected.** Traced the model path: the model
client's streaming ceiling is **900 s** (`runtime_v2/services/_llm_client.py:617,633`);
the F2 model gateway's upstream timeout was only 300 s (would cut a long stream)
and is now **960 s**; the Hyper-V stateful ACL idle session timeout is now
**1800 s** (was 300), so the ordering is 900 < 960 < 1800. `qwen_train/f2_isolation_contract.py`
declares the contract and `tests/test_f2_isolation_timeouts.py` proves it; the
provisioner has an `IdleSessionTimeoutSeconds = 1800` parameter and the static
ACL test cross-checks it.

**Clock analysis corrected + guest clock guard added.** `delivery_timestamp`
(`runtime_v2/services/f2_replay.py:342`) is generated in P2 and, once P2 runs in
the guest, is **guest-clock-originated**, distinct from the host
`freeze_timestamp` (`runtime_v2/services/f2_freeze.py:111`). The 2025-01-01
delivery floor (`qwen_train/f2_protocol.py:175`, `_DELIVERY_EPOCH_FLOOR`,
enforced at `:236`/`:262`) is a **validity** check with no upper bound, not an
ordering check. New `qwen_train/f2_clock_guard.py` compares the guest UTC clock to
`host_reference_utc` from the input manifest (no host contact) with a 120 s
tolerance and fails closed as `GUEST_CLOCK_OUTSIDE_ALLOWED_WINDOW`;
`tests/test_f2_clock_guard.py` covers the boundary cases. Final F2 keeps Hyper-V
Time Synchronization disabled; a one-time install-time clock set is a separate
bootstrap phase, not final synchronization.

**Zero-network install invariant.** Removed the "temporary install-time network"
language: the VM has exactly one NIC attached only to the isolated F2 Internal
switch at all times; Windows install must complete offline or STOP.

**Host exposure inventoried (read-only).** Qdrant (6333/6334) and Avast bind
`127.0.0.1` only and are not guest-reachable. `0.0.0.0` listeners (135, 2179,
11435, 16992/16993, …) would be reachable at 10.72.0.1 only if the ACL fails —
that is what the guest probe tests. Recorded in
`docs/EXPERIMENT_J_F2_VM_ISOLATION.md` §10.

**Offline input mechanism chosen: read-only VHDX** (not ISO): no ADK/`oscdimg`
install, no download, inherent read-only attach, easy SHA-256 manifest. Builder
`qwen_train/f2_input_bundle.ps1` is plan-only by default (proven: no VHDX/manifest
created on a plan run; `tests/test_f2_input_bundle.py`).

**Guest probe bundle added.** `qwen_train/f2_guest_probe.ps1` (emits JSON;
clock, adapter/route/DNS inventory, authorized 8099 + `/v1/models`, denied ports,
IPv6/ICMP/Internet) with schema `qwen_train/f2_probe_schema.py` and
`tests/test_f2_probe_schema.py`. The probe does not invoke any F2 execution path.

88 focused tests pass; ruff E9,F clean on changed files; all three PowerShell
scripts parse. No VM/switch/adapter/firewall/Avast/ACL/account/.env/secret was
touched. Boundary remains `BLOCKED — EXTERNAL PREREQUISITE` (no official guest
install media).

## 2026-10-07 (preflight 2) - VHDX lifecycle, HTTP-Date clock, timeout chain

**Input-bundle builder defect fixed + scratch-proven.** `f2_input_bundle.ps1`
previously created a blank VHDX and then searched for a formatted volume (which
cannot exist on a fresh RAW disk). It now performs the full Windows-native
lifecycle: `New-VHD` → `Mount-VHD` → `Initialize-Disk` (GPT) → `New-Partition
-AssignDriveLetter` → `Format-Volume` (NTFS) → copy + per-file SHA-256 verify →
`Dismount-VHD`, inside `try/finally`, with a post-dismount `Attached` check. One
authorized scratch execution under `$env:TEMP` succeeded end-to-end; `Get-VHD`
showed `Attached=False`, no `F2INPUT` volume remained, and the scratch dir was
removed. `tests/test_f2_input_bundle.py` now asserts the lifecycle and cleanup.

**VHDX read-only truth corrected.** A VHDX is not inherently read-only;
`Mount-VHD -ReadOnly` proves only the host-side mount. Guest-side read-only attach
is `NOT ESTABLISHED` from Microsoft docs; the guest probe now performs a write
attempt (`-InputDisk`, expected failure).

**Fresh HTTP-Date clock reference.** The gateway now emits an explicit RFC 7231
UTC `Date` header on every response (success and error), not a framework side
effect; `f2_guest_probe.ps1` reads it from the authorized `GET /v1/models` call as
the runtime clock reference (manifest timestamp = provenance only). Clock error
classes split into MISSING/MALFORMED/OUTSIDE; `qwen_train/f2_clock_guard.py` is the
single algorithm and `qwen_train/f2_clock_vectors.json` is the shared
cross-language vector contract (`tests/test_f2_clock_vectors.py`).

**Timeout chain traced.** Real chain: guest → gateway(8099) → `model_router`(8080)
→ llama(8079). Binding ceiling is `model_router.py:104` `httpx.AsyncClient(timeout=300.0)`;
effective ceiling = min(router 300, gateway 960, client 900) = **300 s**. The
Hyper-V `-IdleSessionTimeout` (1800 s) is a **silent-gap** bound (~6× the 300 s
max gap), not a total. Fixed a stale "idle 300s" `Write-Step`; added a regression
that the stale value cannot return. `f2_isolation_contract.py` records the router
timeout and `effective_total_ceiling_s()`.

**Windows offline-install uncertainty documented.** Offline OOBE for the current
Enterprise 25H2 evaluation build is `INFERRED — MUST BE VERIFIED DURING INSTALL`;
a golden-VHDX staging fallback is recorded as a PROPOSAL only.

100 focused tests pass; all three PowerShell scripts parse; ruff E9,F clean on
changed files. No VM/switch/adapter/firewall/Avast/ACL/account/`.env`/secret was
touched. Boundary remains `BLOCKED — EXTERNAL PREREQUISITE` (no official guest
install media).

## 2026-10-07 (preflight 3, last static) - PS5.1 probe, ISO/IMAPI2, status accounting

**Guest probe made Windows PowerShell 5.1 compatible.** The host's
`powershell.exe` is **5.1.26100.9549**; the prior probe used PS7-only constructs
(`if` as a hashtable value, `Test-Connection -TargetName`, `$PSScriptRoot` in a
param default, `Headers.ContainsKey`) and **failed to parse under 5.1**. The probe
is rewritten PS 5.1-safe and now parses clean under both 5.1 and 7.

**Clock: HTTP Date only, no fallback.** The probe uses the gateway's fresh HTTP
`Date` header as the sole runtime reference; the manifest timestamp is provenance
only. Missing Date ⇒ `HOST_REFERENCE_MISSING`; malformed ⇒
`HOST_REFERENCE_MALFORMED`. Shared vectors (`f2_clock_vectors.json`, 14 cases) are
executed by both the Python authority and a new PowerShell harness
(`f2_clock_vectors.ps1`) under **5.1** — they agree (14/14, exit 0).

**Read-only medium revised to ISO/IMAPI2.** `IMAPI2FS.MsftFileSystemImage`
instantiates on this host (no ADK needed); a DVD/ISO medium is inherently
read-only to the guest, closing the VHDX guest-write gap (`Mount-VHD -ReadOnly`
proves host-side only; no `Add-VMHardDiskDrive -ReadOnly` exists). Recommendation:
ISO/IMAPI2 for input, VHDX retained for output. ISO builder implementation is the
next authorized item.

**False-ready prevention.** The probe now reports explicit required-check
accounting (`required_checks_total/passed/failed/not_established`); `overall_status`
is `PASS` only when 0 required checks failed AND 0 could not be established,
otherwise `FAIL`/`NOT_READY`. Schema + tests enforce the invariant.

**Probe semantics corrected.** DNS now separates configuration from behavior
(no `example.invalid` as "proof"); IPv4/IPv6 keep inventory separate from
connectivity (`NOT_ESTABLISHED`/`NO_USABLE_IPV6_DESTINATION` when no destination);
ICMP is `OBSERVED_BEHAVIOR`, not ACL proof; the input-disk write test records
`WRITE_SUCCEEDED_UNEXPECTED`/`WRITE_FAILED` + HRESULT and only marks
`PROVES_READ_ONLY` when the error is diagnostic (else `NOT_ESTABLISHED`).

**Timeout chain.** F2 tool-decision uses `local_max_tokens=512` on the 300 s
timeout path (`_llm_client.py:437,444`), ≈24–73 s — under the `model_router` 300 s
ceiling; no router change required. Hyper-V idle 1800 s remains a silent-gap
bound.

105 focused tests pass; PS5.1 + PS7 parse clean; PS5.1 vectors 14/14; ruff E9,F
clean. No VM/switch/adapter/firewall/Avast/account/`.env`/secret was touched; no
builder re-run (executable logic unchanged). Boundary remains `BLOCKED — EXTERNAL
PREREQUISITE` (no official guest install media).

## 2026-10-07 (VM-shell, first live host run) - defect found, stopped, fixed in repo

**First live execution of `qwen_train/f2_vm_provision.ps1 -Execute -ShellOnly`.**
Per the mission's fail rule the run was STOPPED at the first error, not cleaned
up, and not rerun. A media-less shell mode (`-ShellOnly`) was added first.

**Defect (in-scope, now fixed).** The provisioner called
`Set-VMNetworkAdapter ... -SwitchName`, but on this host `Set-VMNetworkAdapter`
has **no `-SwitchName`** parameter (confirmed `False`); the switch is selected via
`Connect-VMNetworkAdapter -SwitchName` (confirmed `True`). The script aborted at
that line. Fixed to `Connect-VMNetworkAdapter`; regression test added
(`test_switch_selected_via_connect_not_set`). PS7 + PS5.1 parse clean.

**Half-built state preserved (not cleaned up, not rerun).** Read-only capture
(evidence at `C:\Users\rober\F2_EVIDENCE\vm_shell\`, outside Git):
- `F2-Internal-Switch` = **Internal**, `NetAdapterInterfaceDescription` empty (no
  physical binding).
- host `10.72.0.1/24` on `vEthernet (F2-Internal-Switch)`.
- `F2-Isolation-VM` = Generation 2, **State Off**, exactly **1 NIC** on the F2 switch.
- **ACL = 0 rules** (not applied).
- Integration services: **Key-Value Pair Exchange = Enabled (True)**,
  Time Synchronization = True, VSS = True, Guest Service Interface = False,
  Heartbeat/Shutdown = True → side-channel suppression NOT applied.
- Automatic checkpoints = True (not disabled).
- Secure Boot: `Get-VMFirmware` returned `EnableSecureBoot` blank (indeterminate
  on this host) → **NOT ESTABLISHED**; template `MicrosoftWindows`.

**Verdict: BLOCKED - ISOLATION VERIFICATION FAILURE** (required host-side
isolation properties — ACL, KVP disabled, Secure Boot, vTPM, auto-checkpoints —
cannot be proven; the abort left the shell incomplete). The VM is Off with no
media; no isolation is claimed. Rerunning the corrected provisioner (idempotent)
is `REQUIRES AUTHORIZATION`. The rollback script `qwen_train/f2_vm_rollback.ps1`
was written and **not run**. No Firewall/Avast/account/`.env`/secret was touched.

## 2026-10-07 (VM-shell rerun) - host-side shell PROVEN

**Corrected provisioner re-run over the preserved half-state succeeded.**
`qwen_train/f2_vm_provision.ps1 -Execute -ShellOnly` completed against the
preserved objects without recreating anything. Hardened first for idempotent
re-apply: the key protector is created only if absent and vTPM is enabled only if
not already on (both already existed from the first run; a naive re-apply would
have failed). Added fail-closed read-back of Secure Boot and vTPM to the final
verification. Regression tests added.

**Secure Boot property name (host-verified).** `Get-VMFirmware` exposes
**`SecureBoot` = On**; `EnableSecureBoot` is the `Set-VMFirmware` *parameter* and
is NOT a read-back property. Read-back corrected to use `SecureBoot`.

**Proven host-side (live read-back, two independent queries):**
- `F2-Internal-Switch` = Internal, no physical adapter binding.
- `vEthernet (F2-Internal-Switch)` = `10.72.0.1/24`, no IPv4 default gateway,
  implicit IPv4 DNS empty, forwarding Disabled (v4+v6); IPv6 link-local present.
- No NAT (`Get-NetNat` = 0); F2 adapter ICS `SharingEnabled=False`.
- `F2-Isolation-VM` = Generation 2, **State Off**, exactly **1 NIC** on the F2
  switch, `AutomaticCheckpointsEnabled=False`, 2 vCPU, DynamicMemory=False,
  0 disks, 0 DVDs, 0 GPU-partition adapters; COM1/COM2 are the Gen2 default with
  no host path.
- **Secure Boot = On; vTPM `TpmEnabled=True`; key protector present (5151 bytes).**
- Extended ACL (attached to the single F2 NIC): Outbound Allow
  `10.72.0.2 -> 10.72.0.1:8099/TCP` **Stateful=True IdleSessionTimeout=1800
  Weight=100**; Outbound Deny `ANY/ANY/ANY/ANY/ANY` weight 1; Inbound Deny
  `ANY/ANY` weight 1 (explicit IPv4+IPv6 catch-all).
- Integration services: KVP/Data Exchange **False**, VSS **False**,
  Time Synchronization **False**, Guest Service Interface **False**; Heartbeat
  and Shutdown **True**.
- Avast: no security prompt observed during the run.

**NOT ESTABLISHED / residual:** PowerShell Direct (no such entry in
`Get-VMIntegrationService`; guest-side `vmicvmsession` proof deferred to the
Windows-install mission). IPv6 DNS shows the OS-default site-local entries
`fec0:0:0:ffff::1..3` (Windows default for an unconfigured IPv6 stack, non-routable
here; no IPv6 default route; IPv6 ACL-denied) — IPv4 DNS is empty.

**Verdict: VM SHELL PROVEN (host-side).** All guest-side properties (routing,
DNS, IPv4/IPv6/ICMP/TCP-8099, Internet isolation, read-only input, clock) remain
NOT ESTABLISHED until Windows is installed. VM left **Off**. No Windows, no ISO,
no firewall/Avast/account/`.env`/secret change; rollback script not run.

## 2026-10-07 (VM shell — install media + live boot) - ACL re-apply defect

**ISO discovered and verified.** `C:\Users\rober\Downloads\26300.9457.260913-1737.26h2_ge_release_svc_refresh_CLIENTENTERPRISEEVAL_OEMRET_x64FRE_en-us.iso`
(7.66 GB); SHA-256 `BC3F24086EBADC94489066B5AD78089E2CF5C3491E90E790BB81A2B199C10E38`
**matches** the published Windows 11 Enterprise Evaluation 26H2 x64 EN-US hash.

**Protector-disclosure audit.** Repository/working-tree occurrences of the
protector byte-pattern (UTF-8 BOM + `<Wrappings>`): **0**. The prior accidental
print exists only in the earlier agent transcript, not in any tracked or
working-tree file, evidence dir, or commit. Length-only handling retained.

**Live defect found and fixed (in-scope).** With the OS disk + ISO attached, the
provisioner aborted re-applying the ACL:
`Add-VMNetworkAdapterExtendedAcl ... Cannot create a file when that file already
exists. (0x800700B7)`. Root cause: `Remove-VMNetworkAdapterExtendedAcl` accepts
**only `-Direction` + `-Weight`** (or the `-InputObject` pipeline) — it has **no**
address/port/protocol parameters. The removal loop passed the full tuple, so the
removals silently no-op'd and re-adding the allow collided. Fixed to remove via
the stored objects (`Get-VMNetworkAdapterExtendedAcl | Remove-...`); regression
test added. Re-run then completed cleanly.

**Host-side shell re-proven (fresh, post-run):** `F2-Internal-Switch` Internal/no
physical binding; host `10.72.0.1/24`, no gateway, IPv4 DNS empty, forwarding
disabled; no NAT; F2 ICS SharingEnabled=False; `F2-Isolation-VM` Gen2 **Off**,
**1 NIC** on the F2 switch, auto-checkpoints False, Secure Boot On, vTPM True,
key protector present; OS disk `C:\Users\rober\F2_VM\F2-Isolation-VM-os.vhdx`
(64 GB dynamic) + the ISO as DVD; integration services KVP/VSS/TimeSync/GSI off,
Heartbeat/Shutdown on; ACL = stateful allow `10.72.0.2->10.72.0.1:8099 TCP`
(w=100, Stateful, 1800) + `ANY` catch-all deny outbound/inbound (w=1).
**Pre-boot ACL double-read identical: PROVEN.**

**First boot:** `Start-VM` with the ACL installed, one NIC on the Internal switch,
no gateway/DNS/NAT/ICS, no second adapter. Post-start guard passed
(`Running`, 1 NIC on `F2-Internal-Switch`). The VM is now booting **Windows
Setup**; the interactive OOBE (language, disk, account/credentials) is Rob's to
complete in VMConnect — the guest boundary is not weakened and no credentials
were handled. Guest-side evidence (`C:\f2_evidence\*`) and the gateway HTTP-Date
clock/negative-probe checks remain **PENDING** until Windows is installed.

Verdict: **VM SHELL PROVEN — GUEST EVIDENCE PENDING**.

## 2026-10-07 (later) — F2 VM isolation: guest retest failed; state reconciled

**What happened.** The F2 guest was installed and OOBE completed; a live
guest→host gateway test was attempted against `10.72.0.1:8099`. The guest
**could not** reach the gateway (TCP timeout). Read-only diagnosis found the
host isolation boundary intact, Windows Defender Firewall non-governing (Avast
is the registered firewall product), no matching WFP net-event, and no
statically-matching Avast rule. The **only** enforcement evidence naming a
component was the Hyper-V vSwitch per-adapter `FailedSecurityPolicy` drop
counters (Outgoing 47 / Incoming 14 observed), which implicate the **Hyper-V
extended ACL**, not WFP/Avast.

**What was NOT proven.** A candidate ACL change was applied, but the post-fix
guest retest was **never obtained**; therefore it is `NOT ESTABLISHED` that the
candidate fix restores L2/TCP reachability, that the guest reaches `/v1/models`,
or that the negative probes stay blocked. **F2 isolation is NOT proven**, no Q9
attestation exists, and the VM topology is **RECOMMENDED, not authorized for F2
execution** (F2-IMPL-AUTH-025).

**Host cleanup (verified).** Pktmon stopped (no filter); WFP capture stopped; no
F2 diagnostic pollers; no F2 gateway process; `10.72.0.1:8099` free; the
temporary Defender rule `F2-TEMP-Allow-Guest-10.72.0.2-To-Gateway-8099` removed;
the authorized **three-rule** Hyper-V ACL baseline restored (Outbound Allow
`10.72.0.2->10.72.0.1:8099 TCP` w=100 Stateful; Outbound/Inbound `ANY`
catch-all Deny w=1); firewall baseline restored (all profiles ON,
BlockInbound,AllowOutbound). F2 network safety boundary intact (VM `Off`, 1 NIC
on the Internal switch, no NAT/ICS/bridge/external, forwarding disabled).

**Readiness.** `python -m qwen_train.f2_preflight` => **BLOCKED** (17 findings:
11 OPERATOR ACTION, 1 PRIVILEGED HOST ACTION, 4 EXTERNAL EVIDENCE, 1
AUTHORIZATION, 2 NOT EXECUTED). Repository machinery is IMPLEMENTED. **F2 may
not be run.**

**Documentation reconciliation (this entry).**
`docs/EXPERIMENT_J_F2_VM_ISOLATION.md` was stale ("no VM exists / `BLOCKED -
EXTERNAL PREREQUISITE`"); it now reflects the current state (VM exists, guest
installed, isolation unproven, topology unauthorized, gate BLOCKED) while
preserving its network-policy meaning and its authorization boundary. The
F2-IMPL-AUTH-027 ledger entry in
`docs/EXPERIMENT_J_F2_ORCHESTRATOR_IMPLEMENTATION_AUTHORIZATION.md` gained a
dated point-in-time / superseding note only; its historical text is unchanged.

## 2026-10-08 - F2 statistical/governance closure (116, unit, admitted, F1 evidence)

Four audit findings were carried through to implementation, test and record.
Nothing scientific changed: δ = 0.20, α = 0.05, π_d = 0.50, `k = 12`,
`n = 300`, the exact two-sided McNemar test and the Clopper-Pearson interval are
untouched, and no gate was weakened.

**1. What `required_pairs` actually returns (`F2-CLARIFICATION-005`, `6a38c1b2`).**
It is the **first** `m` whose *rounded, conditional* power clears the target on a
**non-monotone** curve — at δ = 0.20, π_d = 0.50, α = 0.05, power 0.90 that is
**116** (power 0.900969), but power at `m = 118` is **0.839027**, at `m = 122`
**0.895089**, at `m = 130` **0.930641**. `n = round(m·π_d)` and
`b = round((n + m·δ)/2)` are both rounded while the exact two-sided critical
value steps with `n`, so the alternative weakens *and* the bar rises at the same
step. Independent exact design calculations put the corresponding requirement at
**130** (conditional) and **135** (unconditional) pairs; the helper's own stable
crossing is **139**. The module's claim that its returned N is "a slight
OVER-estimate" was true only of the exact-vs-asymptotic comparison it came from
and is now scoped accordingly. **`n = max(300, …)` is unaffected and provably so**
— `required_pairs` returns 28 / 66 / 116 / 191 / 261 across the authorized π_d
range, all below 300. The helper has **no production caller**; the enforced
constant is `FROZEN_MIN_PAIRS`. Also fixed: `binom_sf` / `binom_cdf` raised
`OverflowError` above ~n = 1024 (`comb(2000,1000) ≈ 1e600` cannot become a
float); a log-space fallback now runs **only** on `OverflowError`, so every
previously working input is bit-identical (verified against
`sum(comb(n,i)) / 2**n`).

**2. The experimental unit was already decided — it was just not enforced
(`F2-CLARIFICATION-006`, `833558bb`).** `F2-IMPL-AUTH-024` §3: *"One pair = one
task … Repeated runs of the same task are replicates within one clustered unit
and MUST NOT be counted as additional independent pairs"*; §6.3 *"No
pseudo-replication. Repeated runs of one task never increase `m`."* F0 §11, the
readiness plan, AUTH-028 (which counts **tasks**) and the execution code (no seed
parameter) all agree. `_partition` — shared by `finalize_f2` and
`independent_reconstruction` — now **fails closed** on a second complete pair for
one `task_id`, identically for producer and auditor, rather than silently
dropping one (a post-hoc drop would be an exclusion AUTH-018 §Q5 does not
permit). Incomplete attempts for the same task remain legal, because Q6 reruns
retain every attempt. **The earlier audit's "GOVERNANCE GAP" on the unit was
wrong: the authority was sufficient and simply had not been surfaced.**

**3. Admitted vs analyzable (`F2-CLARIFICATION-007`, `3442489c`).** The readiness
gate checks `admitted >= 300` because **AUTH-028 set the target that way** and
declared it *"consistent with the frozen n = 300"* — so the gate is **not**
checking the wrong quantity. The two counts are still not equal: Q5's reasons are
cleared at admission, so post-admission loss is infrastructure (rerunnable ×3,
capped by the Q6 30 % ceiling, which STOPs rather than proceeding with a smaller
`n`). Worst Q6-legal case at admitted = 300 is analyzable = 210, where exact
unconditional power bottoms out at **0.8123** (Independent calculation; the
smallest N clearing 0.90 across π_d ∈ [0.20, 1.00] is **270**, and N = 300 gives
**0.9291** at π_d = 1.00). **No threshold changed** — raising the population
target to ≥ 429 admitted would change AUTH-028 and is `REQUIRES AUTHORIZATION`.
The comment and shortfall remedy now name the counted quantity, and a test pins
it.

**4. F1 endpoint evidence (`F2-CLARIFICATION-008`).** Re-deriving every endpoint
from the raw ATIF trajectories with the repository's own `pair_observation_ok` +
`find_qualifying_first_edit` shows **9 of the 10 VALID observations reached the
endpoint, all at step 4** — not 10 of 10. The tenth (`f1_observation_5.jsonl` L2,
invocation `32a0965d7b108e25`, run `36bec9e8-…`) did `lsp diagnostics`, a
`filesystem **read**`, a duplicate `lsp` call and an `mcp` call, and **no
qualifying edit**; its stored `f1_endpoint_step = null` is correct, not stale.
**`k = 12` and `P95 = 12` are unchanged** (9×4 + 11×12 → 19th value = 12).
Corrected in `F1_FINAL_RECONCILIATION.md` §18 and the readiness plan §3 under
those documents' own "raw artifact is authoritative" rule. Headroom is now 9/10,
95 % CP `[0.555, 0.997]` — still consistent with `p_X > 0.80`, so δ = 0.20
attainability remains `NOT ESTABLISHED` and Q10 is still required.

**Remaining `REQUIRES AUTHORIZATION`:** (a) the superseded wording in
`docs/EXPERIMENT_J_F1_AUTHORIZATION.md` and `docs/LEARNING_EXPERIMENT_STATE.md`
(AGENTS.md §3.1 protects both); (b) raising the population target to guarantee
300 *analyzable* pairs; (c) any change to the `required_pairs` algorithm.
**F2 is not READY** — Q9, Q10, Q12, Q13, the receipt key, S8 evidence and the
admitted population are all still outstanding.

*End of WORK_LOG.md — This file contains the historical project memory migrated from the original AGENTS.md. For current standing rules and architecture, see AGENTS.md. For Experiment J scientific truth, see the three authoritative documents in docs/.*
---

## 2026-10-09 - F2 S8 execution channel, Stage 1 / Run 1 (host-side implementation + scratch validation)

**Interactive grant (Rob, 2026-10-09).** Authorizes Stage 1 / Run 1 only: host-side implementation, scratch disks under `C:\Users\rober\F2_EVIDENCE\`, and preparation of guest-console instructions. **Run 1 grants nothing for a live VM cycle; Run 2 requires a separate explicit grant from Rob.** Excluded: starting/reconfiguring F2-Isolation-VM; integrating with its disks; any real task, gold run, model, S8/F2 execution; Q9/Q10/Q12/Q13; network/firewall/Avast/ACL/account/integration-service changes; secrets; RunPod resource changes; scientific-parameter changes; producing purported real evidence/attestation/admission. Recorded here because this file's format is the permitted place for an interactive grant; no ID was invented and no frozen document changed.

**File-scope boundary (recorded before editing).** New files only: `qwen_train/f2_s8_record.py`, `qwen_train/f2_s8_vhdx.py`, `tests/test_f2_s8_stage1.py`; the only existing repository file updated is this `WORK_LOG.md`. No existing module, test, AGENTS.md, 13(7), frozen science, or authorization document was modified. One design note created outside the repository at `C:\Users\rober\F2_EVIDENCE\s8_stage1\README.md`.

**Delivered (code proof + scratch proof).** `f2_s8_record.py` - bounded synthetic record (magic F2S8REC1, 48-byte header, uint32 payload length, SHA-256, UTF-8 JSON, MAX_PAYLOAD 1 MiB) with a strict decoder rejecting bad magic/version/header-size, oversized/truncated header/payload, digest mismatch, trailing bytes, invalid UTF-8, malformed JSON, and non-object payloads; integrity only. `f2_s8_vhdx.py` - unattached FIXED-VHDX reader parsing file identifier, header (CRC32C), region table, metadata region, and BAT entry 0; refuses dynamic/differencing; computes the payload offset from the validated BAT entry (FileOffsetMB = entry >> 20, state = entry & 0x7, PAYLOAD_BLOCK_FULLY_PRESENT = 6) and bounds-checks every read; never attaches/mounts and never parses a filesystem. `tests/test_f2_s8_stage1.py` - 20 tests, **20 passed**.

**Scratch results (PROVEN, scratch-only).** Route A/B: `Mount-VHD -ReadOnly -NoDriveLetter` on a scratch GPT+NTFS disk still let Windows fully recognise and parse it (Get-Disk PartitionStyle=GPT, Get-Partition Basic, Get-Volume F2S8/NTFS); `-NoDriveLetter` suppresses the letter, not filesystem recognition. A raw no-partition disk showed PartitionStyle=RAW with no volume. Route B2: a fixed scratch VHDX's record at the validated payload offset 4194304 was written by a host-side file write (no attachment) and read back byte-for-byte, digest verified. Defect found from scratch evidence and fixed against [MS-VHDX]: New-VHD -Fixed uses a 2 MiB block size, and BAT state is bits 0-2 with FULLY_PRESENT = 6.

**Label: READER MECHANICS ONLY; A HOSTILE GUEST-WRITTEN DISK IS NOT TESTED.** No live isolation, S8 evidence, or F2 readiness is claimed. Route C (serial/named pipe) was NOT implemented and remains REQUIRES SEPARATE EXPLICIT AUTHORIZATION. The VM was not started or reconfigured.

---

## 2026-10-09 - F2 S8 VHDX reader hardening (post-audit repair, Stage 1)

**Trigger.** The post-Run-1 adversarial audit of `46834d67` returned **FAIL**: the
reader accepted a payload offset on a mere low bound, did not verify the region-table
CRC, and selected the header without the specification's sequence-number rule. This
entry records the corrective repair (commit follows) without rewriting the earlier
Run 1 entry.

**Verified against [MS-VHDX] and corrected.** (a) The region-table copies live at
**fixed** offsets 192 KiB and 256 KiB - the earlier audit's "read RegionTableOffset
from the header" lead was **wrong** and was not implemented; both fixed copies are
now validated and the valid one used. (b) The header `SequenceNumber` (offset 8) now
selects the current header (higher sequence wins), both copies are validated
independently, and the log range is checked. (c) The region table's CRC-32C over the
64 KiB copy is now verified (checksum field zeroed for the computation); entry
count/reserved, GUID uniqueness, unknown-required rejection, 1 MiB alignment, in-file
bounds, and region overlap are enforced. (d) Metadata items are now bounded by the
metadata region (not the file), the table must fit the region, items must not overlap
or duplicate, and required item lengths/reserved bits are validated before unpacking.
(e) File Parameters reserved bits, sector sizes, virtual-disk size, and the required
BAT mapping (every block of a fixed disk must be FULLY_PRESENT) are validated.
(f) **The payload block must not overlap the header section, region tables, the log,
the metadata region, or the BAT** - the previous ">= 256 KiB" check accepted a BAT
entry pointing into metadata or the BAT itself; that is now rejected. (g) The module
docstring was corrected (it had contradicted the code on BAT bit fields, state value,
block-size range, and supported layout).

**Tests.** `tests/test_f2_s8_stage1.py` was rebuilt around a self-contained synthetic
fixed-VHDX fixture builder (no disk is ever attached/mounted): valid round-trip,
header selection/recovery, region-table CRC + recovery + overlap/duplicate/unknown/
alignment, metadata bounds/duplicates/lengths, BAT state/reserved/geometry, payload
inside metadata, payload inside BAT, payload beyond file, dynamic/differencing
refusal, record block-boundary, a behavioral "opened read-only" guard, and the
supplementary real scratch VHDX. **57 passed**; `ruff --select E9,F` clean.

**Evidence boundary.** Code proof + synthetic-fixture proof only.
**READER MECHANICS ONLY; A HOSTILE GUEST-WRITTEN DISK IS NOT TESTED.** No production
integration, live isolation, S8 evidence, or F2 readiness is established. No VM was
started; no disk was attached or mounted; Run 2 did not begin.

---

## 2026-10-09 - F2 S8 VHDX reader: BAT interleaving correction (Stage 1)

**State on entry.** HEAD `8d79c1b3` (post-audit hardening), worktree clean except the
pre-existing `tests/conftest.py` and untracked user files.

**Defect found.** The reader treated the BAT as one entry per payload block
(`BAT[i]` for block `i`). [MS-VHDX] 2.5 interleaves payload and sector-bitmap
entries: a chunk holds `ChunkRatio = (2^23 * LogicalSectorSize) / BlockSize` payload
entries followed by one sector-bitmap entry. For the reader's small-disk contract
`ChunkRatio` is large (e.g. 4096 at 1 MiB blocks / 512-byte sectors), so the payload
index equals the block index in practice - but the assumption was wrong in general and
sector-bitmap entries were never validated.

**Correction.** `qwen_train/f2_s8_vhdx.py` now computes `chunk_ratio_for`,
`bat_payload_index` and `bat_sb_index` ([MS-VHDX] 2.5), sizes the BAT from the
payload-block count plus the sector-bitmap-entry count, indexes payload entries
through the interleaving map, and requires every sector-bitmap entry to be
`SB_BLOCK_NOT_PRESENT` (0) with a zero offset - fixed VHDX must not allocate
sector-bitmap blocks. Duplicate nonzero payload offsets are now rejected.

**Tests.** `tests/test_f2_s8_stage1.py` gains a `TestBatInterleaving` class (chunk
ratio formula, payload index interleaving, sector-bitmap index across full and
partial chunks, small-disk no-op). **61 passed**; `ruff --select E9,F` clean. The
supplementary real scratch VHDX still round-trips.

**Limitation.** Live guest-written-disk evidence remains **NOT ESTABLISHED**;
synthetic fixtures only. No VM started, no disk attached or mounted.

---

## 2026-10-09 - F2 S8 VHDX reader: header/region/metadata invariant completion (Stage 1)

**State on entry.** HEAD `89e6965c`, worktree clean except the pre-existing
`tests/conftest.py` and untracked user files.

**Corrections (qwen_train/f2_s8_vhdx.py).**
* Region table: a zero-length entry could `continue` past GUID-uniqueness and
  required-field checks. Uniqueness is now enforced for every entry, and a required
  (or BAT/metadata) region may not have zero length.
* Metadata table: entries are now unpacked as {ItemId, Offset, Length, Flags,
  Reserved}; a nonzero Reserved field or reserved flag bits are rejected;
  `(ItemId, IsUser)` uniqueness is enforced; a zero-length item must carry a zero
  offset; only non-user (system) items populate the field map.
* Headers: two valid copies with EQUAL sequence numbers must be byte-identical;
  a disagreement is refused (no arbitrary tie-breaker).

**Tests (tests/test_f2_s8_stage1.py).** The synthetic builder now writes the full
metadata-entry record (flags/reserved). Added `TestNewInvariants`: zero-length
required region, zero-length entry not bypassing uniqueness, metadata reserved field
and reserved flag bits, duplicate `(ItemId, IsUser)`, zero-length item with nonzero
offset, equal-sequence identical headers accepted, equal-sequence differing headers
refused. **69 passed**; `ruff --select E9,F` clean; the real Hyper-V scratch VHDX
still round-trips.

**Limitations.** Live guest-written-disk evidence remains NOT ESTABLISHED; synthetic
fixtures only. Page83/VirtualDiskID presence is not required. No VM started; no disk
attached or mounted; no production integration or F2/S8 readiness.

---

## 2026-10-09 - F2 S8 VHDX reader: header/log/region/metadata completion (Stage 1)

**State on entry.** HEAD `f56adc7d`, worktree clean except the pre-existing
`tests/conftest.py` and untracked user files.

**Corrections (qwen_train/f2_s8_vhdx.py).** Header: Version MUST be 1; LogVersion
MUST be 0; header reserved bytes [80,4096) MUST be zero; a non-zero LogGuid means an
active log requiring replay (not implemented) and is refused; a nonzero log must be
1 MiB aligned and in-bounds, and a zero log length with a nonzero log offset is
refused. Region table: the header reserved field MUST be zero; the entry `Required`
field is a bitmask whose reserved bits MUST be zero. Metadata: the table header
reserved field and trailing reserved bytes MUST be zero; an unknown item marked
`IsRequired` is refused; the required **Virtual Disk ID** item (16 bytes) must be
present.

**Tests (tests/test_f2_s8_stage1.py).** The builder now writes the header Version and
the Virtual Disk ID item. New `TestHeaderLogAndRegionInvariants` and
`TestMetadataInvariants` cover invalid Version, invalid LogVersion, reserved header
bytes, active LogGuid, out-of-bounds log, valid empty log, region-table reserved
field, invalid `Required` value, metadata-header reserved field, unknown required
metadata, unknown optional metadata, missing/wrong-length Virtual Disk ID. Existing
header/region/metadata/BAT/payload/unsupported tests retained. **82 passed**;
`ruff --select E9,F` clean; the real Hyper-V scratch VHDX still round-trips
(Version=1, LogVersion=0, LogGuid=0, reserved tail zero confirmed by inspection).

**Limitations.** Live guest-written-disk evidence remains NOT ESTABLISHED; synthetic
fixtures only. Page83 metadata is not required. No VM started; no disk attached or
mounted; no production integration or F2/S8 readiness.

---

## 2026-10-09 - F2 S8 VHDX reader: separate region and metadata GUID allowlists (Stage 1)

**State on entry.** HEAD `4f7adf3b`, worktree clean except the pre-existing
`tests/conftest.py` and 38 untracked user files.

**Confirmed defect (PROVEN).** `_check_regions` decided whether a `Required` **region**
was understood using `GUID_KNOWN`, a single set that unioned the supported *region*
GUIDs with the *metadata-item* GUIDs. A Required region whose GUID happened to equal a
metadata-item GUID (e.g. File Parameters `{CAA16737-...}`) was therefore accepted as
"known" even though it is not a supported region. Basis: [MS-VHDX] 2.2.3.2 defines the
`Required` field as "whether the region must be understood to open the file"; region
identity and metadata-item identity are distinct namespaces.

**Correction.** `qwen_train/f2_s8_vhdx.py` now defines `REGION_KNOWN = {GUID_BAT,
GUID_METADATA}` (supported region GUIDs) and consults **only** that set in
`_check_regions`; `KNOWN_METADATA_ITEMS` is unchanged and still governs metadata-item
classification. The conflated `GUID_KNOWN` was removed rather than renamed.

**Tests (tests/test_f2_s8_stage1.py).** `test_required_region_guid_colliding_with_metadata_item_rejected`
(a Required region whose GUID equals `GUID_FILE_PARAMETERS` must be refused; under the
old predicate the GUID was `in GUID_KNOWN` so the fixture was accepted - the test
regresses the defect by inspection) and `test_unknown_optional_region_allowed` (an
unknown non-required region is still permitted). **84 passed**; `ruff --select E9,F`
clean; the real Hyper-V scratch VHDX still round-trips.

**Limitations.** Live guest-written-disk evidence remains NOT ESTABLISHED; synthetic
fixtures only. No VM started; no disk attached or mounted; no production integration
or F2/S8 readiness. No metadata-region checksum was added (the specification defines
none).

---

## 2026-10-09 - F2 S8 VHDX reader: structural-range and payload-interval overlap fixes (Stage 1)

**State on entry.** HEAD `3639953d`, worktree clean except the pre-existing
`tests/conftest.py` and 38 untracked user files.

**Confirmed defects (PROVEN by the independent audit and reproduced here).**

* **A - structural-range overlap.** `_structural_ranges` assembled the header
  section, region-table area, declared regions, and the log, but the list was only
  used to reject payload blocks; nothing compared the structural ranges with one
  another. A file whose log region `[2,3) MiB` coincided with the declared metadata
  region `[2,3) MiB` was **accepted**. Basis: [MS-VHDX] 2.1 (structures occupy
  non-overlapping file ranges).
* **B - partial payload-block overlap.** The BAT loop tracked exact start offsets
  (`seen_offsets`) and checked payloads against structural ranges, but never checked
  payload intervals against each other. With 2 MiB blocks, BAT offsets 4,5,6,7 MiB
  produced spans `[4,6) [5,7) [6,8) [7,9)` and the image was **accepted**. Exact-start
  uniqueness does not imply interval disjointness.

**Corrections (`qwen_train/f2_s8_vhdx.py`).** Added `_check_structural_overlap`,
called on the full structural list after `_structural_ranges` (half-open intervals;
adjacency permitted), which rejects the log overlapping any declared region and
regions overlapping the header/region-table area. Added payload-span collection with
a sorted pairwise disjointness check (`a1 > b0`) in the BAT loop, preserving the
existing state/reserved/alignment/bounds/structural checks and exact-start
uniqueness.

**Tests (`tests/test_f2_s8_stage1.py`).** New `TestOverlapDefects`:
log-overlaps-metadata rejected; partial payload overlap (2 MiB blocks) rejected;
duplicate payload start rejected; valid adjacent payload blocks accepted; valid
payload blocks with a gap accepted. **89 passed**; `ruff --select E9,F` clean; the
real Hyper-V scratch VHDX (2 MiB blocks, log `[1,2) MiB`, metadata `[2,3) MiB`)
still round-trips.

**Spec vs reader invariant.** Structural non-overlap is an explicit layout
requirement ([MS-VHDX] 2.1). Payload interval disjointness is a necessary reader
safety invariant: 2.5.1 states only FileOffsetMB uniqueness, so partial-span overlap
with distinct offsets is not explicitly prohibited but would map two virtual blocks
onto overlapping file bytes.

**Limitations.** Live guest-written-disk evidence remains NOT ESTABLISHED; synthetic
fixtures only. Fixed VHDX only; no active-log replay, dynamic, or differencing
support. No VM started; no disk attached or mounted; no production integration or
F2/S8 readiness.
