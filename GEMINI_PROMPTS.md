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

---

## PROMPT 9 — Fix: citation-verification shape-mismatch discriminator is a no-op

This is a FIX prompt (not an audit). A committed hardening is live-proven wrong;
implement the correction below.

**Context.** `swarm_os/services/legal/citation_verify.py` has an M5 hardening
(commit `0edc975`): a CourtListener `200` means "a cluster exists", NOT "the
citation is correct" — a fabricated/ALTERED cite that re-points at a real
cluster returns 200 (e.g. `400 U.S. 79` → 200, resolves to *Dutton v. Evans*,
whose real page is 74). The committed check compares the cite we sent against
the lookup response's `normalized_citations` to detect this.

**The bug (live-probed, not hypothesized).** The API **echoes the input** in
`normalized_citations` — it never returns the canonical real citation:
- sent `400 U.S. 79` → `normalized_citations: ["400 U.S. 79"]`, cluster 108220
  (*Dutton v. Evans*), whose canonical citations say `400 U.S. 74`.
- sent `400 U.S. 74` → `normalized_citations: ["400 U.S. 74"]`, same cluster.

So `sent_key not in norm_keys` is NEVER true for the mislead-200 class and
`shape_mismatch` never fires — the committed check is a silent no-op in
production. The mocked tests pass only because they feed the API a *different*
normalized citation than the API actually returns.

**The real discriminator (also live-probed).** The cluster payload carries the
canonical citations in `clusters[].citations[]` — a list of
`{volume, reporter, page}` dicts (e.g. `400`/`U.S.`/`74`, plus parallel cites
`91`/`S. Ct.`/`210`, `27`/`L. Ed. 2d`/`213`). Compare the cite we sent against
THESE keys:
- sent `400 U.S. 79` → key `400|us|79` NOT in canonical keys → `shape_mismatch`
- sent `400 U.S. 74` → key `400|us|74` IS in canonical keys → verified

**Required change (surgical, do NOT rewrite the file).**
1. In `verify_citations()` (`citation_verify.py` ~line 352-368), replace the
   `norm_keys` source with canonical keys built from `clusters[].citations[]`
   (each entry → `case_citation_key(f"{volume} {reporter} {page}")`), keeping
   the same `sent_key not in canonical_keys` comparison. Keep `normalized`
   stored on the result (it's informative) but stop treating it as the truth.
2. Update the three mocked tests in `tests/test_legal_citations.py`
   (`test_verify_citations_200_with_matching_shape_is_verified`,
   `test_verify_citations_200_with_altered_shape_is_mismatch`,
   `test_verify_citations_200_empty_normalized_not_mismatch`): feed
   `clusters[].citations[]` canonical data (matching shape → verified; altered
   page → mismatch; no cluster citations → verified-as-before, do not invent a
   mismatch).
3. `legal_advisor.py` already consumes `stats.shape_mismatch` and needs no
   change unless the message text must change.

**Regression watch (do not break real rows).** Probe-verified reporter-series
shape: `case_citation_key("142 Ohio St.3d 57")` → `142|ohiost3d|57` (the "3d"
series is part of the reporter string). If the cluster's canonical reporter is
stored WITHOUT the series ("Ohio St."), a real `142 Ohio St. 3d 57` could be
falsely flagged `shape_mismatch`. EMPIRICALLY validate against the live API —
send the real 200-class rows `142 Ohio St.3d 57`, `19 N.E.3d 900`,
`500 U.S. 444` and confirm they stay `verified` (not mismatch), and that
`400 U.S. 79` fires `shape_mismatch` while `400 U.S. 74` stays verified.
Reporter normalization must make "Ohio St.3d" and "Ohio St." (and any series
token) compare equal when they name the same reporter. If the canonical payload
cannot disambiguate series reliably, prefer an approach that never falsely
flags real rows (miss on the alteration beats a false fabrication signal).

**Acceptance gates.**
- `.\.venv\Scripts\python.exe -m pytest tests/test_legal_citations.py tests/test_legal_advisor.py tests/test_legal_citebench_eval.py -q` → all pass.
- `ruff check . --select E9,F` → clean.
- Live-validate the discriminator (single probe, CourtListener free tier is
  ~50 req/hr): `400 U.S. 79` mismatch, `400 U.S. 74` verified, `500 U.S. 444`
  verified, `142 Ohio St.3d 57` verified, `19 N.E.3d 900` verified.
- Do NOT commit. Report the diff + the live-probe output. Update AGENTS.md
  "Recent Changes" only after a human accepts the fix.
