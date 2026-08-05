# Gemini Audit Prompts — v-horseshoe-v2

Each prompt is a complete, paste-able audit brief. Run ONE at a time. Every one
has the AGENTS.md-update rule baked in. Tell Gemini which number you're running.

## Common preamble (prepend to every prompt)

```
You are auditing a production Swarm-OS platform (FastAPI + async agent loop +
Qdrant + llama.cpp + React 19 consoles). This is a CROSS-CHECK, not a fresh
build — the project is considered complete, CI-green, and production-ready.
MANDATORY: read AGENTS.md first. Its "Recent Changes (do NOT re-apply)"
section is the authoritative changelog — do not redo or contradict it.

Hard rules:
1. After EVERY completed finding or change, update AGENTS.md with a "Recent
   Changes" entry (what/why/how-verified). This is the project's shared memory.
2. Do NOT commit, push, or create PRs. Report + propose.
3. Never call a file dead without confirming zero importers (code+tests+docs).
4. If an area is clean, say so plainly. Do not invent work.
5. Do not suggest reintroducing any qwen3.5-9b references (model is pruned).

Verification:
- Python: python -m pytest tests/ swarm_os/tests/ -q --ignore=tests/test_full_system_hardmode.py
- Import: python -c "import swarm_os.app.main; from swarm_os.app.main import create_app; create_app()"
- Active frontend: npm --prefix organism-console run build && npm --prefix organism-console test
- start-console: npm run build only (NO test script; do not report its absence as a failure)

Environment: Windows. PowerShell may fail with "windows sandbox ... Access is
denied" — that is a sandbox launch issue, not a code problem. Prefer `rg` over
line-numbered Get-Content.
```

---

## PROMPT 1 — Agent loop deep audit (highest-risk code)
Target: `runtime_v2/api/agent_service_v2.py` (857 lines), `runtime_v2/api/_agent_routing.py`,
`runtime_v2/services/stream_runner.py`.

Verify:
- `_CallState` lifecycle — any shared/class-level mutable state that could leak
  between concurrent agent runs?
- Turn budget / max-turns handling — can the loop exit without recording the
  outcome? Can `final` be reached without web_search on internet goals?
- Delegation recursion — depth guard, circular-delegation block, coordinator
  finalization correctness.
- Every `except` path — does it feed the circuit breaker, record a tool_result
  failure event, and persist a reflexion rule? Any silent swallow?
- The `_feed_outcome` / evolution wiring — is completion gating correct?
- Refactor opportunities in the 857-line file that DON'T change behavior.

Output: (A) real defects with file:line, (B) state-leak races, (C) swallowed
errors, (D) safe refactors, (E) "clean" verdict per sub-area.

---

## PROMPT 2 — Async daemon & concurrency audit
Target: `swarm_os/app/main.py` (lifespan + daemons), `swarm_os/services/evolution_daemon.py`,
`genetic_mutation_loop.py`, `memory_daemon.py`, `reflection_loop.py`,
`swarm_os/healing/healing_loop.py`, `organism_console/core/healing_watchman.py`.

Verify:
- asyncio task lifecycle — fire-and-forget tasks with no strong reference
  (GC-able mid-await)? Tasks cancelled on shutdown?
- Shared mutable state between daemons (module globals, caches, files) — races?
- Daemon interval/backoff correctness — can two daemons collide on the same
  files (fitness.jsonl, genomes.jsonl, events.jsonl, reflexion store)?
- Shutdown ordering — do daemons keep writing after clients are closed
  (the "client has been closed" class of bug)?
- Any daemon that can hang the event loop (blocking sync calls without
  asyncio.to_thread)?

---

## PROMPT 3 — Memory system integrity audit
Target: `swarm_os/memory/memory_bridge.py`, `runtime_v2/services/memory_core.py`,
`swarm_os/services/reflection_loop.py`, `swarm_os/services/vector_store.py`,
`swarm_os/lib/vector/qdrant_store.py`.

Verify:
- Event ingestion → embedding → Qdrant upsert path — any drop or silent fail?
- Duplicate-suppression correctness (`_is_duplicate`).
- Consolidation / summarization — timeouts, token caps, slot-busy handling
  (httpx.ReadTimeout fast-path).
- Reflection distillation gate (`fix_class`) — is `model_variability` really
  skipped? Are rules persisted with correct component metadata?
- Qdrant collection naming consistency across all writers/readers (the
  `/memory` 404 bug class).
- Retrieval → `[PAST-MISTAKE WARNING]` injection — can it blow the context
  budget?

---

## PROMPT 4 — Security: prompt-injection & tool boundary audit
Target: `runtime_v2/services/tool_executor.py`, `swarm_os/lib/mcp/*`,
`swarm_os/capabilities/sandbox_repl.py`, `security_gate.py`,
`swarm_os/services/danger_room.py`, `swarm_os/healing/recovery_engine.py`.

Verify:
- Prompt-injection sanitization — instruction-like text from tool results
  redacted before reaching the model? Any path where it isn't?
- Web fetch SSRF — loopback/private/metadata hosts blocked on ALL fetch paths?
- Sandbox escapes — path containment (is_relative_to) on every write/merge,
  not just the AST scan (which is a blacklist)?
- Subprocess isolation — cwd containment + env stripping on every LLM-generated
  code execution (not just recovery_engine; check genetic_mutation_loop too)?
- mcp_register launcher allowlist — any shell-metacharacter bypass?
- The DangerRoom AST scan vs actual execution — any gap between what's scanned
  and what runs?

---

## PROMPT 5 — Active frontend audit (organism-console)
Target: `organism-console/src/` (the ACTIVE console launched by start-dev.ps1).

Verify:
- Data fetching — are all api calls error-handled? Any unhandled promise
  rejection or missing res.ok check?
- SSE parsing (SwarmDashboard2027 AgentConsole) — correct event/payload unwrap?
- State management (zustand/ui-store) — stale state, missing cleanup, leaks?
- React 19 correctness — key props, effect cleanup, memory leaks on unmount.
- Type safety — any `any`/cast that masks a real bug? (tsc is clean; find
  LOGICAL errors, not type errors.)
- The `ai` v3 `useChat` usage — is GHSA-866g actually reachable, and is
  accepted-risk defensible given the actual usage?

---

## PROMPT 6 — LLM cost & fallback-chain audit
Target: `runtime_v2/services/fallback_manager.py`, `_llm_client.py`,
`stream_runner.py`, `usage_log.py`, `runtime_v2/services/online_routing.py`.

Verify:
- Fallback chain correctness — can a billing-402 pin a model forever? Can a
  cooled-down model be retried? Any cross-provider key/base leak?
- The semantic decision cache (`_semantic_decision_cache.py`) — are misses
  still hitting the LLM correctly? Is the write-back path sound?
- usage_log accounting — are all 6 litellm call sites recording? Any call that
  bypasses telemetry?
- Cooldown math — exponential backoff bounds, `record_model_success` clearing.
- Cost posture — is anything routing to a non-flash DeepSeek or an expensive
  model despite the flash-only policy?

---

## PROMPT 7 — Fitness/evolution correctness audit
Target: `swarm_os/services/outcome_fitness.py`, `evolution_daemon.py`,
`swarm_os/kernel/genetics.py`, `selection.py`.

Verify:
- The fitness composite formula F = 0.40·completion + 0.25·test_pass +
  0.20·tool_success + 0.10·efficiency + 0.05·human — is completion gating
  correct (unfinished capped at 0.4)? Any NaN/inf risk on divide-by-zero?
- fitness.jsonl persistence — append atomicity, corruption tolerance, lock?
- Elite selection + crossover + mutation — can the population collapse or the
  best genome be lost?
- tool_genes ordering feeding `_get_allowed_tools` — does it actually
  reorder allowed tools, and could a zero-weight tool be excluded wrongly?
- The 0.0425 best_fitness plateau seen in logs — is that expected or a sign of
  a scoring bug?

---

## PROMPT 8 — End-to-end healing loop audit
Target: `swarm_os/healing/*` (recovery_engine, governor, healing_loop,
healing_service, diagnostician, offline_learner) + `organism_console/core/repair_engine.py`.

Verify:
- Failure detection → recovery → finalize (Governor.finalize) — is outcome
  learning actually wired end-to-end (no dead link)?
- Circuit breaker — can a real incident be masked by the daily cap or 4h pause?
- Repair engine constitutional guards — path allowlist, anti-truncation,
  test-run-before-accept, cure retirement. Any bypass?
- The `turn_budget_exhausted` path — does RepairWatchman act on it?
- diagnostician `fix_class` routing — prompt_sensitivity vs model_variability
  — correct on both governor and reflection paths?
