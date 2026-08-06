# Horseshoe Project Map

## Architecture Overview

Four-layer design:
- **swarm_os/** — Core swarm intelligence platform (orchestrator, API, brain, memory, healing, control plane)
- **runtime_v2/** — Async agent runtime (agent loop, LLM client, tool execution, contracts)
- **src/** — Next-gen agent runtime & memory stores (agent_runtime, orchestrator, hybrid memory)
- **organism_console/** — CLI interactive shell frontend

Test framework: pytest (pytest.ini at root)
Python: >=3.14
Build: setuptools, `organism` CLI entrypoint

---

## Module Map

### swarm_os/core/ (Orchestration & Infrastructure)

| File | Lines | Role |
|------|-------|------|
| `orchestrator.py` | 585 | `Orchestrator.generate()` — text generation loop with tool-call parsing, dedup, routing |
| `message_bus.py` | 78 | Async event bus with `Event` dataclass, `subscribe()`/`publish()` via `asyncio.Queue` |
| `tool_parser.py` | 97 | `ToolParser` — stateless tool-call extraction from LLM text (3 pattern formats + CLI) |
| `settings.py` | — | Settings/config dataclasses |

### swarm_os/api/ (HTTP API)

| File | Lines | Role |
|------|-------|------|
| `routes.py` | 626 | Main router: `/readyz`, `/router`, `/critic`, `/memories`, `/timeline`, `/healing/evaluate`, `/traces/summary`, `/tools/cache`, `/tools/execute`, `/models/autoassign` |
| `api_features.py` | 646 | Feature router: `/features/search` (dense-vector search + rerank, `{status: ok\|degraded, fallback, results}` with keyword-scan degraded fallback), chat-search SSE, Upwork analyzer, codebase indexing, snapshot lifecycle, approval workflows |
| `agents.py` | 201 | Agent CRUD + step execution + model management |
| `admin.py` | 207 | Health evaluation, heal cycles, simulation management |
| `schemas.py` | 88 | Pydantic schemas |
| `dependencies.py` | 38 | FastAPI DI: `runtime_dep()`, `get_orchestrator()` |
| `api_health.py` | — | Health endpoint logic |
| `health.py` | — | Health probe helpers |

### swarm_os/services/ (Application Services)

| File | Lines | Role |
|------|-------|------|
| `tool_registry.py` | 291 | `SemanticToolRegistry` — Qdrant-backed semantic tool discovery with async client |
| `llm_client.py` | 208 | `CloudLLMClient` — detects provider (OpenRouter/NVIDIA/llama.cpp) via litellm |
| `genetic_mutation_loop.py` | 234 | Code mutation loop for self-improvement (DangerRoom+SecurityGate+compile+pytest validated, staged for approval; daemonized hourly via `SWARM_GENETIC_MUTATION=1`) |
| `vector_store.py` | 202 | Qdrant vector store wrapper (AsyncQdrantClient) |
| `reflection_loop.py` | 474 | `ReflectionService` — ASPO rule distiller: failures → correction rules → Qdrant (diary + agent tool_failure entries, component-preferring `get_latest_failure`) |
| `chat_service.py` | 137 | Context compaction, model auto-assignment, reachability checks |
| `knowledge_graph.py` | 76 | AST import dependency graph (networkx) |
| `system_service.py` | 69 | Multi-layer health (system, LLM, Qdrant) |
| `security_gate.py` | 70 | AST code security scanner (banned calls/modules) |
| `danger_room.py` | 91 | Isolated sandbox for safe code mutation testing |
| `memory_daemon.py` | 31 | Background memory consolidation (5-min interval) |
| `token_manager.py` | 37 | Token budget tracking with async lock |
| `embedding_service.py` | — | Dedicated embedding client (port 8081, nomic-embed) |
| `health.py` | — | Backend health checker |
| `llm/client.py` | 111 | Lower-level LLM client with thread pool + semaphore |
| `outcome_fitness.py` | — | Real task-outcome fitness feed (research-grounded composite, completion-gated); persisted to `data/evolution/fitness.jsonl`, gated by `SWARM_EVOLUTION=1` |
| `evolution_daemon.py` | — | Outcome-driven evolution daemon: score population by best recorded outcome, elite-selection + crossover + mutate, persist next generation; `_best_genome_tool_weights()` exposes the evolved tool policy |

### swarm_os/services/rv_finder/ (Used-RV Deal Finder package)

Package split from the deleted 1,275-line `rv_finder.py`. Exposed as `find_best_rv_deals()` via `__init__.py`; wired to `POST /features/rv-finder/search` in `api_features.py`.

| File | Lines | Role |
|------|-------|------|
| `service.py` | — | `find_best_rv_deals()` orchestrator; type-filter normalization, best_motorhome fallback |
| `parsers.py` | — | HTTP + PPL + web discovery, `DISCOVERY_PARSERS`, `_parse_snippet(title, body, url)`, junk-title filter |
| `analysis.py` | — | Pure domain logic: deal scoring, `_title_motorhome`, `_is_motorhome_like`, life-ease, flags |
| `knowledge.py` | — | Static tables: `KNOWN_WEAK_SPOTS`, `LIFE_EASE_FEATURES`, `KNOWN_MOTORHOME_MODELS` |
| `llm.py` | — | `_llm_deep_dive`: OpenRouter DeepSeek first (60s, `num_retries=0`), qwen3.5-4b local fallback (300s) |
| `models.py` | — | `RVListing` dataclass + `serialize_listing()` |

### swarm_os/services/control_plane/ (Orchestration Control Plane)

| File | Lines | Role |
|------|-------|------|
| `strategy.py` | 303 | Pluggable routing strategies (Default/Deep) |
| `router.py` | 150 | Routes requests to optimal models based on profiles, cooldowns, strategy |
| `planner.py` | 103 | Task decomposition into `PlanStep` sequences |
| `models.py` | 69 | Model profile dataclasses |
| `trace.py` | 60 | Structured `TraceEvent` observability |
| `shared_model_registry.py` | 60 | Centralized `ModelProfile` definitions for local + cloud models |
| `strategy_registry.py` | 48 | Strategy registration |
| `bootstrap.py` | 36 | Control plane initialization |
| `critic.py` | 36 | Evaluates execution results against structural contracts |
| `state.py` | 32 | State tracking |
| `guardian.py` | 26 | Performance monitoring, cooldown/metacognition triggers |
| `plugin_state.py` | 24 | Plugin state management |
| `policy.py` | 23 | Step budget enforcement (max 12 steps) |
| `registry.py` | 22 | Service registry |
| `fallback_router.py` | 13 | Fallback chain for cloud models |
| `state_manager.py` | 12 | State manager |

### swarm_os/healing/ (Self-Healing)

| File | Lines | Role |
|------|-------|------|
| `recovery_engine.py` | 318 | Coordinated recovery with anomaly tracking (DangerRoom-isolated LLM repair scripts, root-cause dispatch) |
| `healing_service.py` | 85 | `AnomalyTracker`, `FailureDetector`, `RecoveryEngine`, `RollbackManager` |
| `governor.py` | 120 | Governance model tracking |
| `offline_learner.py` | 108 | Batch rule extraction from events.jsonl |
| `reviewer.py` | — | Heal review logic |
| `healing_loop.py` | — | Healing event loop |
| `failure_detector.py` | — | Failure detection probes |

### swarm_os/memory/ (Memory Bridge)

| File | Lines | Role |
|------|-------|------|
| `memory_bridge.py` | 656 | `MemoryBridge` — event ingestion, vector ops, consolidation, GraphRAG, integrates with EventLogRepo, GraphRepo, MemoryDaemon |
| `_memory_bridge_base.py` | 42 | Constants: `CHUNK_SIZE`, `SUM_MODEL`, `VECTOR_SIZE`, `Session`, `Bias` dataclasses |

### swarm_os/infra/ (Infrastructure Clients)

| File | Lines | Role |
|------|-------|------|
| `llama_client.py` | 122 | `LlamaClient` (was `OllamaClient`) — local llama.cpp inference (port 8080), streaming, GLM cloud fork |

### swarm_os/repositories/ (Data Access Layer)

| File | Lines | Role |
|------|-------|------|
| `graph_repo.py` | 118 | Persists `networkx.DiGraph` as GraphML with async save/lock/eviction |
| `event_log_repo.py` | 54 | Tail-reads `events.jsonl` using file offsets, watermark resume |
| `mutation_repo.py` | 49 | Manages pending code mutations with approve/reject/rollback |
| `file_snapshot_repository.py` | 23 | Concrete JSON-file snapshots |
| `snapshot_repository.py` | 12 | Abstract base class for snapshot persistence |

### swarm_os/kernel/ (Kernel)

| File | Lines | Role |
|------|-------|------|
| `genetics.py` | 341 | Genetic mutation engine (consolidated from genetics + genetics_v2) |
| `selection.py` | 357 | Selection/mating logic |
| `organism.py` | 133 | Organism lifecycle |
| `brain.py` | 102 | Brain logic |

### swarm_os/lib/vector/ (Vector Search & Code Indexing)

| File | Role |
|------|------|
| `qdrant_store.py` | `search(collection, query, top_k)` — dense-vector search: embeds via :8081 (nomic-embed), `query_points` by vector (was `query_text`, which silently returned nothing on 768-dim collections). Never raises; degrades to `[]`. |
| `reranker.py` | `rerank(query, candidates, top_k)` — BGE cross-encoder rerank via :8082, semaphore-bounded, graceful fallback to original ordering on outage. Was an EMPTY stub (caused `/features/search` ImportError → 503). |
| `code_indexer.py` | Chunks project files and upserts into Qdrant (`codebase` collection) via :8081 embeddings |
| `context_retriever.py` | `retrieve(query)` — returns relevant code chunks for agent prompts |

### swarm_os/rest/

> **Removed 2026-08**: this directory never existed in the tree — the module map
> below was a stale doc entry. The live evolutionary kernel lives in
> `swarm_os/kernel/`; `swarm_os/swarm_kernel.py` is a thin re-export of it.

### runtime_v2/api/ (Agent Execution)

| File | Lines | Role |
|------|-------|------|
| `agent_service_v2.py` | 857 | `AgentServiceV2` class — `step_agent_stream()` main agent loop. Orchestrates decisions, actions, healing. Persists tool_result failure events + diary writes + turn-budget reflexions. |
| `_agent_config.py` | 25 | Constants: `MAX_TURNS`, `MAX_DEPTH`, `_DEFAULTS`, `ANALYSIS_AGENTS` |
| `_agent_routing.py` | 146 | `fast_route_coordinator()`, `fast_start_for_agent()`, `matches_task_keywords()`, `best_route_target()`, `lookup_model()` — keyword routing + warmup + researcher web-first turn |

### runtime_v2/services/ (LLM & Tool Services)

| File | Lines | Role |
|------|-------|------|
| `memory_core.py` | 442 | `remember_fat()`, `get_relevant_memories()` — Qdrant-backed memory |
| `_llm_parser.py` | 230 | `extract_json()`, `normalize_decision()`, `normalize_model_json()`, `TOOL_CALL_SCHEMA`, `fire_and_forget()` |
| `stream_runner.py` | 347 | `get_tool_decision()` — orchestration: MCP schema, memory injection, retry loop, LLM call |
| `tool_executor.py` | 374 | `run(tool_name, payload)` — dispatches tool calls |
| `fallback_manager.py` | 469 | `get_live_fallbacks()` — cloud model fallbacks, cooldowns, DeepSeek/Ling/OpenCode chain |
| `_llm_client.py` | 377 | `complete_for_tool_decision()`, `stream_content()`, `build_router()` (litellm Router, per-deployment endpoint/key), `build_kwargs()`, `_cloud_response_format()` (strict json_schema), `SSL setup`, `get_litellm_model()` |
| `model_registry.py` | 71 | `get_model(agent_id)` — agent → model mapping (deepseek-coder → qwen3.5-4b) |
| `_llm_prompts.py` | 71 | `build_tool_decision_system()`, `JSON_REPAIR_PROMPT` (includes `/no_think` for Qwen3) |
| `_llm_cache.py` | 30 | Decision cache with TTL eviction |
| `usage_log.py` | 234 | Durable per-model cost telemetry to `data/usage/usage.jsonl` |
| `learning/evolving_critic.py` | 34 | `EvolvingCritic.score()` — metacognition feedback; seeds weights from journal history |
| `learning/critic_journal.py` | 35 | `CriticJournal.log()`/`load()` — durable JSONL journal of critic predictions (read-back enables restart persistence) |
| `learning/meta_critic.py` | 47 | `MetaCritic` self-adjusting critic; `from_history()` replays journal entries to seed weights |

### src/ (REMOVED 2026-08 — was a test-only third agent stack)

> **Removed 2026-08**: `src/` was a third, parallel agent-runtime stack (HybridMemory,
> DynamicRouter, SelfHealingAgentRuntime, ~6.6k lines) that the live app never
> imported — only `tests/test_routing.py` and `tests/test_divide_by_zero.py`
> exercised it. Both the stack and those two tests were deleted; the live swarm
> runs `runtime_v2/` (agent loop) + `swarm_os/` (kernel/memory/healing). Deleted
> with it: the now-unused `scipy` dependency (only `src/` imported it). The
> resilience patterns it tested (circuit-breaker cooldowns, health monitoring,
> escalation) are served live by `swarm_os/healing/` + `fallback_manager.py`
> cooldowns.

### organism_console/ (CLI Frontend)

| File | Lines | Role |
|------|-------|------|
| `_commands_dev.py` | 448 | Dev commands: `diff`, `commit`, `branch`, `debug`, `patch`, `impact`, `compress` |
| `_commands_ai.py` | 426 | AI commands: `heal`, `upgrade`, `goal`, `vote`, `memory`, `simulation` |
| `_commands_system.py` | 382 | System commands: `help`, `status`, `trace`, `cloud`, `tools`, `mcp`, `routing` |
| `commands.py` | 311 | Legacy commands |
| `cli.py` | 233 | CLI entrypoint and main loop |
| `token_tracker.py` | 209 | Token and model tracking display |
| `state_store.py` | 158 | CLI state persistence |
| `renderer.py` | 132 | Output rendering |
| `command_registry.py` | 118 | `CommandRegistry` class + `registry` instance |
| `_command_routing.py` | 116 | `route_natural_language_keywords()`, `classify_intent_with_llm()` |
| `_command_deps.py` | 88 | AST import dependency analysis (`ImportVisitor`, `resolve_module_path`) |
| `_command_context.py` | 24 | `CommandContext` data class |

### start-console/ (Current-Gen Web Console — TanStack Start SSR + React 19)

| File | Role |
|------|------|
| `src/routes/api/chat.ts` | AI SDK v7 chat endpoint: `createFileRoute` + `server.handlers.POST`, `convertToModelMessages`, `createUIMessageStreamResponse` |
| `src/pages/AgentPage.tsx` | Agent chat UI — `useChat` v4 (`DefaultChatTransport`), renders `messages[].parts` (text + tool parts) |
| `src/pages/OpsPage.tsx` | Ops/tutor page (dead trace/admin queries pruned) |
| `src/pages/LearnedMemoriesPage.tsx` | Memory browser |
| `src/components/SwarmTopology3D.tsx` | R3F v9 3D topology (constructor `args`, `[undefined, undefined, n]` instancedMesh) |
| `src/components/organism/OrganismConstellation.tsx` | Genomes visualization (R3F v9) |
| `src/shell/ShellLayout.tsx` | Shell layout via `@tanstack/react-router` |
| `src/lib/types.ts` | Shared types (`StatusResponse.llamacpp_reachable`, `PanelKey` incl. `"memories"`) |
| `src/routeTree.gen.ts` | Generated route tree (regenerate via `npm run generate-routes`) |

Dependency pairing: React 19 ↔ `@react-three/fiber` ^9.5 / `@react-three/drei` ^10, `ai` ^7.0.44, `@ai-sdk/react` ^4, `zod` ^4. Both consoles `tsc` clean; `start-console npm run build` succeeds.

---

## Key Patterns

- **Agent loop** (`step_agent_stream`): turn-based loop (max 8 turns). Each turn: context trim → warmup/fast-route → LLM tool-decision → action dispatch → loop guard. Yields AsyncGenerator[dict].
- **Tool decision**: `get_tool_decision()` in `stream_runner.py` orchestrates MCP schema + memory injection + LLM call + retry + JSON extraction + action coercion.
- **JSON extraction**: `extract_json()` in `_llm_parser.py`. Multiple salvage strategies (brace matching, ast.literal_eval, fence stripping, think-block recovery).
- **Delegation**: recursive `step_agent_stream` call. Max depth 15. Circular delegation blocked. Coordinator always finalizes after first delegation.
- **Healing**: circuit breaker after 3 consecutive errors or loop detection. Delegates to `debugger` agent.
- **Memory**: Qdrant vector store (`memory_core.py`). `remember_fact(category="general"|"self_reflection")`. `get_relevant_memories()` for RAG.
- **Async**: All new services use `asyncio` (AsyncQdrantClient, asyncio.Lock, asyncio.Queue, asyncio.to_thread).
- **Control Plane**: `services/control_plane/` — 17 modules for model routing, task planning, critic evaluation, strategy selection.
- **Repository Pattern**: `repositories/` — Data access layer with EventLog, Graph, Mutation, Snapshot repos.

---

## Qwen3.5 Local Model

- **Model**: `C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf` (MTP 4B, served on :8080). The plain 9B fallback was pruned 2026-08-05 (backup at `C:\Users\rober\AppData\Local\Temp\opencode\prune-backup-2026-08-05\`) — heavy reasoning routes to cloud DeepSeek V4 Flash, so only the 4B-MTP local model is served.
- **Model name in API**: `qwen3.5-4b` (the default MTP 4B served on port 8080; used in `config/agent_models.json` and `model_registry.py`).
- **Thinking mode**: Disabled via `/no_think` prepended to all system prompts in `_llm_prompts.py`
- **Server**: `bin\llama.exe serve -m "C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf" --alias "qwen3.5-4b" -c 16384 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 --port 8080`
- **Fallback**: `reviewer` agent still uses `openrouter` backend (`deepseek/deepseek-r1:free`)
- **Analysis agents prefer cloud**: `code_analyzer`, `researcher`, `reviewer` route to **DeepSeek V4 Flash** (`openrouter/deepseek/deepseek-chat`) for all tool decisions + content streaming whenever `OPENROUTER_API_KEY` is present and cloud is enabled (see `runtime_v2/services/_llm_client.py` `_ANALYSIS_CLOUD_AGENTS` / `_analysis_cloud_enabled()`). Override model via `ANALYSIS_CLOUD_MODEL`; force local via `SWARM_ANALYSIS_CLOUD=off` or `/local` (routing mode `local_only`).

---

## Recent Changes (do NOT re-apply)

- **Autonomous Internet Upgrades & Self-Repair Fixes (2026-08-06)**: (1) Fixed a `TypeError` in `organism_console/core/self_repair_engine.py` that crashed the repair loop when `repair_action` was `None`. (2) Updated `organism_console/loops/autonomous.py` to instruct the `coordinator` agent to route internet research and state-of-the-art upgrade goals to `researcher` instead of `coder`, preventing the local `coder` model from outputting empty internet-search loops. (3) Added `researcher` and `planner` to the valid target agents in the autonomous verification failure feedback loop, ensuring failed upgrades can properly re-route through the research step. (4) Updated `runtime_v2/api/agent_service_v2.py` web-fetch rejection message so agents are explicitly told to implement the changes after fetching docs rather than just synthesizing an answer.

- **Active Frontend Audit Fixes (2026-08-06)**: (1) Fixed a missing `res.ok` check in `organism-console/src/components/organism/SwarmDashboard2027.tsx` `AgentConsole` stream fetch that could cause unhandled errors on 500s. (2) Fixed SSE payload event unwrapping in `AgentConsole` state update where `latestHandoff.type` was missing because the event was sent via the `.event` property. Derived `type` properly via `data.type || data.event` to ensure color-coding works. Verified via `npm --prefix organism-console run build && npm test`, `npm --prefix start-console run build`, and `pytest tests/ swarm_os/tests/ -q`. The GHSA-866g risk in `AgentPage.tsx` `useChat` usage was confirmed unreachable since the app intercepts the stream with a static text Response instead of parsing streaming chunks.

- **Qdrant Initialization 404 bug & Context Budget fix (2026-08-06)**: (1) Fixed a systemic bug in `swarm_os/services/vector_store.py`, `tool_registry.py`, and `reflection_loop.py` where a failed asynchronous Qdrant initialization (expected during a slow startup) would permanently set `_ensured = True` without actually successfully creating the collection. This caused permanent `UnexpectedResponse(404)` errors on all subsequent upserts and reads across the system. Modified the initialization logic to correctly capture boolean success and strictly leave `_ensured = False` if it fails so it retries gracefully on the next tick. (2) Added a graceful 404 handler to `runtime_v2/services/semantic_search.py` so it returns a friendly warning instead of throwing a `RuntimeError` when the codebase index isn't ready. (3) Fixed a context budget logic error in `runtime_v2/services/stream_runner.py` where a large raw `memories_str` could artificially push the computed token headroom negative, completely suppressing the `[PAST-MISTAKE WARNING]` injection. The injected memory size is now calculated post-truncation and reflexion hints gracefully truncate with an ellipsis. Tested locally with `pytest` and import checks (100% pass).

- **Semantic Decision Cache + Repair Engine Fixes (2026-08-05)**: (1) Fixed the `coder` agent's internet-goal bypass guard by unifying `_handle_final` in `agent_service_v2.py` to correctly evaluate the full `_INTERNET_GOAL_RE` expression. (2) Resolved the elusive `TypeError: 'NoneType' object is not subscriptable` crash in `repair_engine.py` (line 795) by casting rule fields from `reflexion_cures.json` and `reflexion_lessons.json` to strings before string-slicing (e.g. `[:80]`), guarding against `null` database values. (3) Enabled the hybrid `SWARM_SEMANTIC_CACHE` integration, complete with a SOTA-aligned `_contains_secrets` heuristic to scrub outputs before vector store persistence and a fully isolated `tests/test_semantic_cache_smoke.py` smoke test. (4) Fixed the `temp_growth` auto-repair bug where the recovery engine attempted to generate a python script via LLM that was blocked by the sandbox due to OS imports; mapped `temp_growth` directly to `clean_temp_files` in `system_recovery.py`.

- **Coder internet goal enforcement fix**: `runtime_v2/api/agent_service_v2.py` — The `coder` agent was bypassing internet search requirements (web_search / web_fetch guards) for internet-flagged goals because the guards were gated by `ANALYSIS_AGENTS`, which deliberately excludes `coder`. Created a new `INTERNET_GOAL_AGENTS` tuple in `_agent_config.py` that includes `coder` and swapped the gating at the three specific enforcement sites while preserving the correct report-only guard.

- **Genetic mutation loop historical-context fix (2026-08-05)**: `swarm_os/services/genetic_mutation_loop.py` — The mutation daemon was passing a static string for failed mutations to `memory_bridge._add()`, depriving the LLM of actionable context on retry. Added surgical capture of the actual `Exception` string into `last_error` within the `except` blocks, appending it to the failure `details` payload. This ensures `get_memory_context` retrieves concrete failure reasons for the next run. Tests 100% green.

- **Goal-loop placeholder-final fail-fast (2026-08-05)**: `organism_console/loops/autonomous.py` — when a delegated agent returns a bare "Task completed."/"Done." final with no file changes and no real analysis, the verification loop would burn a full review cycle (reviewer rejects it, loop retries on the same empty final). Added `_is_placeholder_final()` + a fail-fast guard that treats a placeholder final as an immediate failed attempt with concrete corrective feedback fed back to the agent. Committed `6c9728a`.

- **Clean-output + dep-alignment round (2026-08-05)**: (1) `pytest.ini` filters the litellm `asyncio.iscoroutinefunction` DeprecationWarning (fires on every litellm import; deprecated 3.14 / removed 3.16, upstream) + the requests/urllib3 mismatch — test output is now warning-free. (2) `vector_store._ensure_collection` pre-checks Qdrant is up, raises retries 3→5 (1/2/4/8/16s ~31s) to absorb a slow Qdrant boot, and downgrades final failure to warning (self-heals next tick). (3) Pins aligned with installed patched versions: python-dotenv 1.0.1→1.2.2 (symlink overwrite fix), requests unified at 2.34.2 (lock had vulnerable 2.32.3: netrc/temp-file advisories), pydantic 2.12.5→2.13.4 + pydantic_core 2.41.5→2.46.4 (a manual pydantic-core upgrade to 2.47.0 had broken pydantic 2.13.4 which requires 2.46.4 — venv downgraded + pins aligned). (4) start-console npm undici→7.29.0 + postcss→8.5.25 (0 vulns). Committed `d33cf47` + `bc60ea2`.

- **Copilot guardrail brief added (2026-08-05)**: `COPILOT_PROMPT.md` at repo root — a paste-able brief for Copilot that hard-blocks the PR #6 failure class (mass "reconcile onto clean base" that deleted ~768 tracked files + broke api_features.py). Rules: no file deletion/move/rename unless explicitly asked, no bulk commits, no wholesale file rewrites, no unrequested dep/build/script changes, baseline-tests-before + tests-after, show diff before approval, AGENTS.md update only after acceptance. Matches the project's conventions (FIX:/FEAT:/CI:/ARCH: prefixes, ruff-clean, no qwen3.5-9b references).

- **Memory daemon traceback noise (2026-08-05)**: `consolidate_memories()` and `cluster_graph_rag()` in `memory_bridge.py` dumped full tracebacks when Qdrant was briefly unavailable at startup (qdrant `ResponseHandlingException`/`UnexpectedResponse` wrapping `httpx.ReadError`). That's expected during the startup window (daemon retries next tick), so it now logs a concise warning instead. Fast-paths for `(httpx.ReadError, httpx.ReadTimeout)` + the qdrant wrappers added; genuine failures still `log.exception`. Committed `fa1171e`.

- **Dependency-security bump (2026-08-05)**: Dependabot flagged litellm (Critical auth bypass via Host Header Injection + sandbox escape in custom-code guardrail + user_role/API-key escalation — fixed ≥1.84.0), cryptography (Bleichenbacher oracle in PKCS#7, fixed 50.0.0), and aiohttp (out-of-bounds heap read + websocket deflate, fixed 3.14.3). The venv already had the patched versions (litellm 1.95.0, cryptography 50.0.0, aiohttp 3.14.3) but requirements.txt/requirements-lock.txt pinned the OLD vulnerable ones — bumped the pins to match. Also `npm audit fix` on organism-console bumped undici 7.27.2→7.29.0 (TLS/Set-Cookie/SOCKS5/websocket advisories) + postcss 8.5.22→8.5.25. Remaining npm items are dev-only (vite 5.4.21 dev server) or documented non-applicable (react-router RSC-only). Verified: app.main imports on patched versions; LLM/usage suites 27 passed; frontend build green. Committed `fde3a09`.

- **Backend startup speedup (2026-08-05)**: the backend took tens of seconds to become responsive after launch. Root cause: lifespan started the Genetic Mutation / Evolution / Reflection daemons **immediately** — each first run is heavy (full-repo DangerRoom copy + LLM + compile + pytest; disk-read + crossover; LLM distillation), starving the CPU and the single llama.cpp slot during the startup window. Also `get_mcp_manager()` was serially `await`ed in lifespan, spawning 3 npx subprocesses before serving. Fixed in `a8457bd`: daemons defer their first run (mutation 180s / reflection 120s / evolution 60s), and MCP server loading is now a background task. The API serves immediately; background work happens after. Note: the Genetic Mutation daemon repeatedly produced the same broken mutation ("expected an indented block after function definition on line 135" in `agent_service_v2.py`) — a mutation-quality issue worth revisiting (it burns LLM calls hourly on the same target).

- **Audit prompt library added (2026-08-05)**: `GEMINI_PROMPTS.md` at repo root — 8 reusable cross-check audit briefs for the Gemini/Codex peer audits (agent loop, async daemons, memory integrity, security/prompt-injection, active frontend, LLM cost/fallback chain, fitness/evolution, healing loop). Each includes a common preamble (AGENTS.md-first, no-commit, verify commands, Windows-sandbox gotchas) plus area-specific verification targets and output format. Run one at a time.

- **Full dead-code sweep after the restore (2026-08-05)**: the restored tree carried ~130 files of restored-branch scaffolding and dead duplicates. A 3-agent audit (AST import scan + module-string scan + dynamic-loader check) identified them; deleted in 6 batches, each verified by the full test suite (432 passed / 2 skipped final):
  - **Root scripts (44)**: 18 tracked (`_restart_backend.ps1`, `autonomy-*.ps1`, `ci_cd_integration.py`, `nyc_weather.py`, `openrouter_deepseek_v4_flash.py`, `rob.ps1`/`rob-fix.ps1`, `run_memory_*.ps1`, `safe_patch.ps1`, `scaffold_v2.ps1`, `smoke-test-swarm-os.ps1`, `start-organism-stack.ps1`, `static_analysis.sh`, `transcribe.ps1`, `write_cli.py`, `weather.py`) + 26 untracked throwaway experiment/refactor/benchmark scripts. Kept live: `start-dev*.ps1`, `start_llama.bat`, `ambient_listener.py`, `voice_routing.py`, `whisper_server.py`, `record_voice*.py`, `conftest.py`, `test.db`.
  - **Dead scaffold trees (swarm_os, 67 paths)**: `foundation/`, `execution/`, `app/api/`, `features/` (0-line handlers), `cognition/policy|evaluation`, `governance/`, `orchestrator/`, `memory/storage/`, `lib/vector/{archiver,code_indexer,context_retriever}.py`, `lib/mcp/dynamic_tools/`, plus dead duplicates: `api.py` (empty shadow), `governor.py`, `settings.py`, root `simulation_runner.py`, `kernel/snapshot.py`, `infra/qdrant.py`, `persistence/qdrant.py`, `cognition/reranking.py`, `services/{events,health,status}.py`, `services/llm/client.py`, `control_plane/{fallback_router,guardian,registry,state_manager}.py`, `workers/*`, `scripts/{audit_trigger,system_monitor}.py`, `upwork/{reasoning_layer,win_predictor}.py`, `api/{explorer,health,api_health}.py`, `core/{ci_engine,competition_layer,patch_manager,roman,scoring_engine,state,logging}.py`, `lib/{observer,safety,stress_tester}.py`, `runtime/scheduler.py`, `rag/context_builder.py`, `domain/policies.py`, `events/replay.py`, `healing/{controller,reviewer}.py`, `SMOKE_TEST_FIRST_33.ps1`.
  - **runtime_v2 scaffolds (36 paths incl. organism_console)**: `contracts/`, `core/`, `engine/`, `legacy_bridge/`, `scheduler/`, `state/`, `storage/`, `telemetry/`, `services/{approval_service,delegation_service}.py`, `services/_llm_cache.py` (superseded by `_semantic_decision_cache.py`); organism_console zombies: `main.py`, `core/{control_plane,embedding_client,execution,forgetting_curve,worker}.py`, `events/`, `learning/{critic_bridge,critic_loop,critic_engine}.py`, `refactor/`, `review/{evaluator,system_health}.py`, `skills/{skill_extractor,repair_artifact}.py`, `tools/{skill_executor,tool_registry,detect_stale_tests,remediate_stale_tests,record_healing_event}.py`, `utils/`, `memory/{qdrant_lock,rebuild_from_journal,skill_journal}.py`, `commands.py`.
  - **Dead duplicates (batch 4)**: `zenith/` (whole parallel agent stack, zero importers), `_quarantine/`, `organism-console/{cli,main}.py` (Python dupes in the TS dir), `root/sys_monitor.py`, `memory/sync/ssrg_memory.py`.
  - **Unused Python deps (19)**: removed `black, cachetools, h2, httptools, httpcore2, httpx2, import-linter, mypy, pytokens, tenacity, watchfiles, websockets, orjson, ast_serialize, librt, mypy_extensions, pathspec, platformdirs, grimp` (zero importers + zero reverse-deps). KEPT `truststore` (imported by conftest/cli/bootstrap/app.main).
  - **Unused npm deps**: organism-console `@react-three/fiber/@react-three/drei/three` (migrated to framer-motion/react-force-graph-2d; removed stale `optimizeDeps.exclude:['three']`); start-console `@tanstack/react-router-ssr-query` → replaced with direct `@tanstack/react-query`.
  - **KEPT after verification** (audit false-positives): `core/event_bus.py` (imported by swarm_stream + orchestrator), `kernel/snapshot_index.py` (simulation_runner + kernel/status), `services/llm/client.py` subdir actually has no importer (the live one is `services/llm_client.py`), `import_lock.py`/`self_heal.py` (bootstrap chain), `adaptation/{chat_model_adapter,verification/repair_verifier}.py`, `app/services/research_service.py`, `healing/skill_extractor.py` (test-imported), `kernel/brain.py` (intentional compat facade), `migrations.py` + `kernel/migrations.py` (both live, different callers). Also removed 2 dead tests (mock app/api chat/search routers) from `test_self_heal_and_learning.py` (adaptation coverage retained in 8 other files).

- **Disk prune (2026-08-05, ~6.5 GB)**: after the dead-code sweep, reclaimed ~6.5 GB:
  - The plain 9B GGUF (5.4 GB) pruned — heavy reasoning routes to cloud DeepSeek V4 Flash, so the 9B fallback is redundant on this machine. Backed up (byte-verified) to `C:\Users\rober\AppData\Local\Temp\opencode\prune-backup-2026-08-05\`. `models/` now holds only the 4 live GGUFs (moondream, nomic-embed, reranker, plus the MTP 4B in `C:\Users\rober\models\`). All 9B references removed from code, config, and start scripts (2026-08-05).
  - `organism-console/storage/` (906 MB legacy Qdrant collections, gitignored — not served by the live Qdrant which uses root `/storage/`) deleted from disk.
  - `scratch-app/` (218 MB throwaway TanStack app, zero references) deleted.
  - 14 untracked benchmark/voice/export artifacts (~0.6 MB) removed.


- **Production-hardening pass on restored full codebase (2026-08-05)**: after restoring the full pre-Copilot tree (PR #12), fixed the remaining release blockers:
  - **CodeQL HIGH js/xss-through-dom** in `swarm_os/app/templates/index.html` — backend/error data (chat-search messages, event payloads, snapshot filenames, organism ids/domains, console log lines) was interpolated into `innerHTML` unescaped. Added an `esc()` HTML-escaper and applied it to every data-interpolating innerHTML site.
  - **CodeQL MEDIUM py/stack-trace-exposure** in `swarm_os/api/api_features.py` — raw exception text leaked to clients via SSE `[Error: {e}]`, `HTTPException(detail=f"...{exc}")`, and `str(e)`. Now logs the real error server-side and returns generic messages (chat-search + upwork SSE, mutation approve, `/omnidev/run`, rv-finder). Same fix extended to `routes.py` (`/tools/execute`, `/generate`, autoassign), `agents.py` (CRUD/step/stream/tool), `admin.py` (health evaluate), and `control.py` (model reassign). Structured `{"error": str(exc)}` JSON responses consumed by the CLI were left intact (operator diagnostics, not HTML).
  - **CodeQL HIGH py/path-injection** in `swarm_os/repositories/mutation_repo.py` (5 findings) — `approve()` copied a mutation metadata.json's `pending_file` → `target_path` with no containment check; a poisoned metadata (absolute/`..`-escaping paths) was an arbitrary file write. Added `_resolve_within_root()`: `pending_file` must stay in the mutation staging dir, `target_path` must resolve under the project root. Verified approve succeeds / escape blocked.
  - **psutil enumeration can hang FOREVER in native code on Windows** (`runtime_v2/services/system_intel.py`) — reproduced: the same `process_list` scan sometimes passes, sometimes never returns (a transient/protected process blocks in native code mid-scan). **Threads cannot be killed when stuck in native code**, so a ThreadPoolExecutor timeout leaks the stuck thread. Replaced with **subprocess isolation** (`_run_isolated`): the enumeration runs in a child python the parent `terminate()`s on timeout; worst case is a graceful `system action X timed out` error, never a hang of the agent loop / API / tests. Also switched per-process `cpu_percent` to `interval=None` (non-blocking cached value) so `process_list` isn't O(n·50ms) on busy boxes. `tests/test_system_intel.py` overrides the conftest subprocess mock (module fixtures take precedence) so the real isolation path is exercised; `process_list` accepts the bounded-timeout result.
  - **Dead `organism-console/src/pages/organism-hooks.ts` broke the frontend build** — the restored tree shipped TWO `useOrganismData` implementations; the stale `pages/` one (commit 5dfa9ee) assigned an array to a `TraceSummaryResponse` dict and called `.length` on it, failing `tsc -b` with TS2322/TS2339. The active one is `src/features/organism/organism-hooks.ts` (used by OrganismPage, covered by a 3-test Vitest suite). Deleted the dead `pages/` files. Both consoles build clean.
  - **Accepted-risk npm advisories** (documented, NOT force-fixed): react-router `GHSA-qwww-vcr4-c8h2` (RSC-mode CSRF — non-applicable to this client-only SPA; npm's suggested "fix" downgrades to 7.11.0 which REINTRODUCES the open-redirect advisory); `ai`/`@ai-sdk` `GHSA-866g` (moderate resource-consumption; fix = breaking ai 3.x→7.x rewrite of `useChat`); vite/esbuild + undici (dev-server/dev-only, moderate). `npm audit fix` (non-force) applied for postcss/undici patches.

- **Cross-check hardening round (2026-08-05, Codex + Gemini panel audits)**: an independent Codex audit and a Gemini expert-panel review found additional real defects; fixed:
  - **routes.py leaked raw exceptions in 3 more browser/API-facing sites** (missed by the earlier sweep): `/memory/search` (`detail=str(e)`), `/memories` (`detail=str(exc)`), `/critic` fallback (`"error": str(exc)`). All now return generic messages with the real error logged server-side.
  - **Snapshot migration divergence (real bug, empirically confirmed)**: root `migrations.py` migrates v1→v4 but `kernel/migrations.py` stopped at v3, so restore/simulation load paths produced differently-shaped snapshots than the repository path. Fixed by making `kernel/migrations.py` a thin re-export of the canonical root implementation. This ALSO surfaced a latent crash: `_GENOME_DEFAULTS["lifetime_fitness"]` was a bare float but `Genome.lifetime_fitness` is `Dict[str,float]`, so migrating then `Genome.from_dict()` crashed with `'float' object is not iterable`. Fixed the default to the dict shape + normalize bare floats in `_v3_to_v4`. Verified both paths now produce identical v4 output.
  - **`recovery_engine.py` LLM-repair subprocess hardening** (Gemini's valid concern; note `danger_room.py` itself has NO subprocess — the actual LLM-code execution lives here): the recovery script now runs with `cwd` forced INTO the sandbox staging dir (was repo root, so a mutation could write via relative paths) and with sensitive env stripped (`*API_KEY*`/`*TOKEN*`/`*SECRET*`/`*PASSWORD*` + all `SWARM_*` feature gates) so a malicious script cannot exfiltrate keys or trigger daemon loops. Kept `python -I` isolated mode + `PYTHONNOUSERSITE=1`.
  - **Dead `swarm_os/services/llm/__init__.py`** removed (empty; the live module is `services/llm_client.py`). **start-console README** claimed `npm run test` but no test script exists — replaced with an accurate note (secondary console has no test suite; `../organism-console` is the tested one).
  - **REJECTED from the review**: the proposed conftest autouse env-isolation fixture was based on a misdiagnosis — `test_outcome_fitness.py` uses `monkeypatch.setattr` (auto-reverted) and never mutates `os.environ`, so there is no `SWARM_EVOLUTION` leak to fix; adding the fixture would add overhead for a non-problem. Verified: no test sets `SWARM_EVOLUTION` via env.
  - Verified: snapshot/resume/healing/generate suites pass; full CI green on master.

- **Gemini maintenance-plan round (2026-08-05)**: a second Gemini panel produced a maintenance plan; executed the valid parts, rejected the invalid:
  - **control.py (4 sites) + routes.py (/timeline, /router) `str(exc)` leaks closed** — browser-facing via start-console's CommandCenterPage and the web dashboards; now generic messages + `log.exception`/`log.warning`. (api_features.py:477 `verification_detail` left as-is — it feeds the internal metrics/audit record, not an HTTP response.)
  - **`tests/test_weather.py` deleted** — zombie test: imports no project code, tests only its own hardcoded `urllib` call to api.weather.gov (the `weather.py` it tested was deleted in the dead-code sweep).
  - **REJECTED: deleting `organism-console/` in favor of `start-console`** (Gemini items #1/#4, to resolve console duplication + the `ai` v3 advisory). Evidence disproved the premise: start-console's `/api/chat` calls `localLLM('qwen3.5-4b')` directly at llama.cpp :8080 and is branded "Zenith" (the deleted dead stack) — it BYPASSES the swarm backend (memory/routing/healing/`/generate`) and is not a functional replacement. organism-console's AgentPage calls the real `/generate` path and is the console `start-dev.ps1` actually launches, with the working 3-test Vitest suite. Deleting it would remove the only working chat UI. start-console remains an SSR experiment, not the successor. The `ai` v3→v7 upgrade stays documented accepted-risk (GHSA-866g moderate).
  - Note: the c20d1cf commit message described these fixes but the file diffs were never staged (only the services/llm deletion landed); the real diffs were committed in b3180aa.

- **Elite-swarm audit round (2026-08-05, Gemini swarm + my 8-prompt audit)**: both audit streams converged on real defects; verified + fixed in commit 6531afd:
  - **Evolution no-op (HIGH)**: the agent loop fed outcomes keyed `agent:<id>` but the population uses `genome_<n>` ids, so `best_fitness()` never matched and every genome scored the flat 0.05 prior → frozen population with the 0.0425 elite plateau (empirically confirmed: elites stuck at generation 0 while children reach 496). Added `outcome_fitness.best_aggregate_fitness()`; `_score_genome`/`_best_genome_tool_weights` fall back to it so evolution now runs on the real shared tool-policy signal. Verified: `score_genome` goes 0.05 → 0.91.
  - **LLM-failure finals recorded as completed successes (HIGH)**: `stream_runner` returned `{"action":"final"}` when the model was unreachable/empty/malformed, which the loop treated as a perfect completion (fed `completion=1.0` to evolution + success telemetry). All 4 fallback finals now carry `ok:False`/`system_failure`; `_handle_final` detects it and feeds a FAILED outcome + `tool_result` failure event instead.
  - **playwright SSRF (HIGH)**: `page.goto` on navigate/screenshot/extract_text had no guard — a headless browser could reach loopback/private/cloud-metadata. Now `_ssrf_check` runs before any goto. Verified loopback + 169.254.169.254 blocked pre-launch.
  - **mcp_register metachar + eval bypass (HIGH)**: denylist missed `&`/`\n`/`\r` and bare `>`/`<`; `python -c`/`node -e` executed arbitrary code through allowed launchers. Chars added; `-c/-e/--eval/-p/-i` rejected for python/node (`-m` stays allowed).
  - **Sandbox env stripping (HIGH)**: `genetic_mutation_loop` pytest/py_compile and `sandbox_repl` ran untrusted code with the FULL environment. New `security_gate.clean_sandbox_env()` (strips API keys + `SWARM_*`, sets `PYTHONNOUSERSITE=1`) applied to all three subprocess sites.
  - **tool_executor exception leaks**: mcp_register/mcp catch-alls returned raw `str(exc)` to the LLM — now `log.exception` + generic.
  - **Read-before-write race (MEDIUM)**: `step_agent_stream` cleared the shared `_explored_paths`/`_filesystem_read_cache` globals mid-run, wiping concurrent runs. Now snapshot at entry + restore on completion.
  - **REJECTED**: `asyncio.Lock` on fitness.jsonl (append is already threading-locked; asyncio.Lock is wrong for sync writes from threads); the "`_web_final_rejected` is dead" claim (it IS the observable signal asserted by `test_opencode_parity` — restored, not dead).
  - Also noted (not yet fixed, tracked): `_get_allowed_tools` per-turn synchronous genome-file reads when `SWARM_EVOLUTION=1`; cross-loop `get_reflection_service()` singleton hazard; six agent-loop exit paths that don't feed outcomes.

- **Cost optimization (2026-08)**: Enabled the **semantic decision cache** (`SWARM_SEMANTIC_CACHE=1` in `.env`) — the built-but-off hybrid exact→semantic cache now short-circuits near-duplicate tool decisions (exact SHA-256 LRU = zero false positives; Qdrant cosine ≥0.85 gated; all failures degrade to a miss). This is the single biggest lever for cutting per-call LLM token spend on the tool-decision loop. The write-back path (`cache_tool_decision` in `stream_runner.py`) was already wired. Cost posture confirmed: analysis agents + tool decisions route OpenCode Go subscription ($0/token) with DeepSeek direct (`deepseek/deepseek-v4-flash`, $0.0028/M cache-hit) as the funded paid fallback — the 100K "quota" is a local console counter, not a real limit. Tests: `tests/test_semantic_cache.py` (5).

- **RepairWatchman parse loop extracted to testable helper (2026-08)**: `organism_console/core/repair_engine.py` — the bug-prone per-line event parsing inside `RepairWatchman._watch()` (crashed twice with `'NoneType' object is not subscriptable` on null payloads) is now a pure module-level `_handle_event_line(engine, data)` function, directly unit-testable in isolation. `_watch()` just decodes JSON lines and routes them through it. Regression tests: `test_repair_guards.py::test_handle_event_line_null_payloads_no_crash` (null-payload shapes never raise; real failures still invoke the engine) + `test_watchman_invokes_handle_event_line` (wiring guard).

- **All fixes/corrections now use DeepSeek V4 Flash (2026-08)**: previously the fix/correction paths used local qwen3.5-4b — weak patches and slow repair. Routed every fix/repair/correction LLM call to `openai/deepseek-v4-flash` (funded, $0.0028/M cache-hit): (1) `swarm_os/api/routes.py` `/generate` (the endpoint T2 deep-repair + self-repair call) now defaults to the analysis-cloud DeepSeek model and uses `_is_local_model()` instead of `startswith("openai/")` (which misclassified cloud as local); (2) `genetic_mutation_loop.py` `MODEL` → `openai/deepseek-v4-flash` with cloud base/key; (3) `recovery_engine.py` LLM-guided repair scripts → DeepSeek; (4) `offline_learner.py` rule extraction → DeepSeek. `reflection_loop._distill` was already DeepSeek. Verified: mutation/recovery/offline-learner models + `/generate` default all resolve to `openai/deepseek-v4-flash` with the cloud base/key. 381 tests pass.

- **Full fix loop — analysis agents now actually FIX, not just report (2026-08)**: `runtime_v2/api/_agent_routing.py` + `agent_service_v2.py` + `prompts/system_prompts.py` — a compound goal like "analyze my codebase for bugs and fix them" previously routed to `code_analyzer` (report-only) and never edited anything. Added **fix-intent precedence**: goals containing fix/directive keywords (`fix`, `patch`, `write`, `implement`, `create`, `solve`, `repair`, etc. — excluding research-intent "how to fix X") route deterministically to `coder` (edit-capable). `best_route_target()` and `fast_route_coordinator()` now prefer coder for fix-intent; the coordinator's `_get_decision` guard forces the delegate target to `coder` even when the LLM wrongly picks `code_analyzer`. `coder` gained `web_search`/`web_fetch` (research-then-fix, like a senior engineer) + a prompt directive to read→research→edit→verify instead of just reporting. Verified: `best_route_target("analyze... fix them")` → `coder`; pure analyze → `code_analyzer`; how-to-research stays on `researcher`. Tests: `test_opencode_parity.py` +2 (`test_fix_intent_routes_to_coder`, `test_coordinator_fix_intent_forces_coder_over_analyzer`).

- **Crawl4AI installed (2026-08)**: `crawl4ai>=0.9.0` was declared in `requirements.txt`/`requirements-lock.txt` but **never installed** in the venv — so `web_fetch_handler` (swarm_os/lib/mcp/web_search.py) silently ran its plain-HTTP+regex fallback the whole time, returning stripped HTML (e.g. python.org's "interactive scripts did not run" fallback) instead of browser-rendered markdown. Installed crawl4ai 0.9.2 + its deps (Playwright chromium already present). Verified: `web_fetch_handler('https://www.python.org/')` now goes through the full `[FETCH]`→`[SCRAPE]`→`[COMPLETE]` Crawl4AI pipeline and returns real rendered markdown. No code change needed — the handler already preferred Crawl4AI when present.

- **Internet goals now require web_fetch (deep-read), not just search (2026-08)**: `runtime_v2/api/agent_service_v2.py` — an internet goal could search (Tavily snippets) then call `final` with only snippet-level info; it never deep-read a page. That's not how a human researcher (or opencode) works: search → fetch → read → synthesize. Added `_CallState.did_web_fetch` (set on successful `web_fetch`) and extended the `_handle_final` guard: an internet goal on an `ANALYSIS_AGENT` is rejected on EVERY `final` until BOTH `web_search` AND `web_fetch` have succeeded. Verified: `web_fetch_handler('https://www.python.org/')` returns 7KB of real page content; search-then-final is now blocked with a corrective message to deep-read at least one authoritative page. Tests: `test_opencode_parity.py` +2 (`test_internet_goal_final_blocked_without_web_fetch`, updated allowed-after-search).

- **Internet web_search query cleanup (2026-08)**: `runtime_v2/api/agent_service_v2.py` — the internet-first `web_search` injection was sending the full delegated prompt (including the coordinator's "CRITICAL INSTRUCTION ... you are a router" boilerplate) as the search query, wasting search tokens and polluting results. Added `_clean_search_query()` which strips the instruction wrapper + `Goal:`/`Task:` labels and returns just the goal text. Used for both the analysis-agent internet-first injection and the researcher web-first turn. Verified: `"Goal: analyze... \n*** CRITICAL INSTRUCTION ***\nYou are the coordinator..."` → `"analyze my codebase for bugs and search internet for improvements"`. Test: `test_opencode_parity.py::test_clean_search_query_strips_coordinator_boilerplate`.

- **Internet goals now web-search FIRST (2026-08)**: `runtime_v2/api/agent_service_v2.py` `_get_decision()` — a compound internet goal ("analyze codebase + search internet") routed to an `ANALYSIS_AGENT` used to burn all 8 turns on the 4-step filesystem warmup + repeated reads, hit "max turns reached", and never called `web_search` (the guard rejected the final but there was no budget left to search). Fixed: `_INTERNET_GOAL_RE` detects internet-involving goals and injects `web_search` on **turn 0, BEFORE the warmup** for any analysis agent. Code-only goals keep the deterministic filesystem warmup. Verified live: `[code_analyzer] internet goal — fast-start turn 0 → web_search (before warmup)` → `web_search ok=True` → `Task completed.` (no more max-turns). Test: `test_opencode_parity.py::test_internet_goal_web_searches_before_warmup`.

- **RepairWatchman null-payload crash fixed (2026-08)**: `organism_console/core/repair_engine.py` `_watch()` parse loop crashed with `'NoneType' object is not subscriptable` on event lines whose `payload`/`result`/`arguments` were explicitly `None` (`.get("payload", {})` only substitutes `{}` for a *missing* key, not a null one). Both the `tool_result` and `turn_budget_exhausted` branches are now None-tolerant (`(data.get("payload") or {})`, `payload.get("result") or {}`, `str(... or "")`). Regression test: `tests/test_repair_guards.py::test_watchman_parses_null_payload_events_without_crash`.

- **SOTA self-learning loop: Outcome-Driven Evolution (2026-08, research-backed)**: Bridged the gap between the genetic kernel and the live agent loop so evolution now runs on REAL task outcomes instead of LLM chat noise (research grounding: AlphaEvolve, AgentOptimizer, Reflexion successors). (1) **`swarm_os/services/outcome_fitness.py`** (new) — the live `step_agent_stream` loop feeds real outcomes (task completion, tool success rate, turn efficiency) into a persisted `data/evolution/fitness.jsonl` via `_feed_outcome()`, computed with the research-grounded composite `F = 0.40·completion + 0.25·test_pass + 0.20·tool_success + 0.10·efficiency + 0.05·human` with **completion gating** (unfinished goals capped at 0.4). Gated by `SWARM_EVOLUTION=1` (zero overhead otherwise). (2) **`swarm_os/services/evolution_daemon.py`** (new) — runs evolutionary generations on that persisted fitness: load population, score by best recorded outcome, elite-selection (keep top 2 unchanged), crossover + mutate, persist next generation. Wired into `main.py` as a daemon when `SWARM_EVOLUTION=1`. (3) **Evolved tool policy** — `_get_allowed_tools()` in `agent_service_v2.py` orders allowed tools by the best genome's `tool_genes` (highest real-outcome fitness), so the agent's tool-selection policy literally evolves from real outcomes instead of staying a fixed hand-authored list. Verified: `web_search` (proven most effective) rises to the top of allowed tools. (4) **Learned rules seed repairs** — `repair_engine.py` `get_similar_lessons()` now merges LLM-distilled ReflexionMemory rules (via `check_for_past_mistakes`) with the static lesson KB, so autonomous repairs are seeded by LEARNED corrections from past failures. Tests: `tests/test_outcome_fitness.py` (5); targeted suites 45 + 27 pass.

- **Self-healing/self-learning loop completion (2026-08, research-backed)**: Closed the remaining gaps found in the loop audit. (1) **Genetic mutation daemon wired** — `swarm_os/app/main.py` now starts a `run_genetic_mutation` daemon (hourly) when `SWARM_GENETIC_MUTATION=1` (off by default). The loop was previously ONLY invocable via `python genetic_mutation_loop.py --func ...` (`__main__`), so the advertised "code mutation loop" never ran on its own. Safe to daemonize because every mutation goes through DangerRoom + SecurityGate + compile + pytest validation and stages to `.data/pending_mutations/` for explicit approval (never auto-modifies live code). (2) **Purged ReflexionMemory noise** — 144 of 150 stored rules were `component:"unknown"`/`system:None` genetic-kernel noise from the pre-fix diary-driven distiller, polluting `check_for_past_mistakes()` retrieval. Deleted all non-agent-component points via Qdrant bulk delete; the store now holds only real `code_analyzer` rules. (3) **RepairWatchman now acts on `turn_budget_exhausted`** — `organism_console/core/repair_engine.py` was only handling `tool_result` failure events; the new `turn_budget_exhausted` events (recorded by `agent_service_v2.py` on max-turns) were dropped with no consumer. The watchman now records a ReflexionMemory rule (`action=max_turns_reached`) on such events so turn exhaustion closes the learning loop. (4) **Critic persistence** — `CriticJournal` gained a `load()` method (previously write-only) and `MetaCritic.from_history()`/`EvolvingCritic` now seed weights from the journal on startup, so the critic's learned weight adjustments survive restarts instead of resetting to defaults every boot. Research grounding: Reflexion (NeurIPS'23) episodic verbal feedback → the `[PAST-MISTAKE WARNING]` + reflexion loop; journal-replay weight seeding mirrors online-learning persistence. Tests: `tests/test_critic_persistence.py` (4); full suite 364 passed / 1 skipped.

- **Production upgrades (2026-08)**: (P1) **litellm Router migration** — `runtime_v2/services/_llm_client.py` now routes CLOUD tool-decision + stream calls through a litellm `Router` (`build_router()`) whose `model_list` declares each deployment with its OWN `api_base`/`api_key` (native providers carry none), so Router's health-checked failover can never leak a provider's credentials and a primary outage genuinely degrades to the next provider. Legacy `build_kwargs()`/`acompletion` retained for the local path + tests; Router returns None → falls back to legacy. (P2) **Structured outputs** — `_cloud_response_format()` uses strict `json_schema` (TOOL_DECISION_JSON_SCHEMA) when `litellm.supports_response_schema` is true (Gemini/OpenRouter), else `json_object`; local grammar decode unchanged. (P4) **`/features/search` wired up** — `swarm_os/lib/vector/reranker.py` was an EMPTY stub (the endpoint's `from ..lib.vector.reranker import rerank` raised ImportError → 503). Implemented rerank (cross-encoder via :8082, semaphore-bounded) + fixed `qdrant_store.search` to dense-vector search (embed via :8081, query_points by vector — was `query_text` which silently returned nothing on 768-dim collections). Endpoint now returns `{status: ok|degraded, fallback, results}` with a **keyword-scan degraded fallback** (scroll payloads, token-match, falls back to swarm_memory when the requested collection is empty). (Cleanup) **Deleted 27 tracked + 6 untracked dead root scripts** (check_sort, find_msgs, find_state, broken_weather, prime_*, read_lines, reconcile_patch, internet_self_heal_test, stress_test*, run_interactive_swarm, run_swarm, swarm_server, sys_monitor, system_monitor (root dup), zen_core, audit_md, export_memories, coordinator_routing, safety_protocol, C-horseshoe-v2os, _tmp_worker_smoke, MASTER_UPGRADES, run_5/8_healing_agents (superseded by main.py ReflectionDaemon), run_vulture_analysis, chaos_test*, broken_test, integration_test, stress_test, metacognition_test) + pruned conftest `collect_ignore`. Kept LIVE: voice_routing.py, whisper_server.py, ambient_listener.py. Tests: 360 passed / 1 skipped.

- **Disk prune (2026-08)**: Deleted 3 stale GGUF models from `models/` that had zero live references (start scripts / config / runtime): `qwen2.5-coder-7b.gguf` (4.36 GB), `qwen3-4b-tuned-latest.gguf` (2.33 GB), `phi4-mini-latest.gguf` (2.32 GB) — ~9.6 GB reclaimed; `models/` went 16.4 GB → 6.8 GB. Kept the live set at the time (the manual-fallback GGUF was later pruned too, 2026-08-05, see Recent Changes): `moondream-latest.gguf` (:8083), `qllama-bge-reranker-v2-m3-latest.gguf` (:8082), `nomic-embed-text-v1.5.Q8_0.gguf` (:8081), Whisper `small.en`/`base.en`. Also **untracked 906 MB of legacy Qdrant DB** accidentally committed under `organism-console/storage/` (281 files, `horseshoe_memory`/`horseshoe_swarm_memory_final_v12`/`horseshoe_traces` collections — NOT served by the live Qdrant which uses root `/storage/`). Untracked via `git rm -r --cached` (files kept on disk) + added `/organism-console/storage/` to `.gitignore`. Deleted originals backed up (byte-verified) to `C:\Users\rober\AppData\Local\Temp\opencode\prune-backup-2026-08-04\models\`. Verified post-prune: all 5 live llama.cpp model servers still serve; backend `/readyz` ready; CLI boots.

- **Production-Readiness Stabilization & Zero-Lint Clean Sweep (2026-08)**:
  - **SelfHeal Recovery Fix**: Fixed `swarm_os/import_lock.py` where failed import locks constructed `SelfHeal()` but never invoked `healer.heal(m)` before throwing `ImportError`. Now invokes `healer.heal(f[0])` with exception handling.
  - **Orphaned Test Detection Fix**: Corrected inverted module verification check (`importlib.util.find_spec`) in `organism_console/tools/detect_stale_tests.py` that caused false-positive detection of valid tests.
  - **Windows Service Discovery & Async Loop Fixes**: Replaced fragile import fallback checks in `runtime_v2/services/system_intel.py` with `hasattr(psutil, "win_service_iter")`. Fixed `asyncio.run()` runtime nesting crash in `organism_console/ui/live_stream.py` by offloading to a single-thread executor when invoked inside an active event loop.
  - **Code Health Clean Sweep**: Reduced static analysis (`ruff` E9/F) errors across `swarm_os/`, `runtime_v2/`, and `organism_console/` from 192 down to **0 errors**. Removed dead code (redundant `intent` classification in `orchestrator.py`, vestigial `local_model_names` fetching in `picker.py`, dead `cache_key` blocks in `stream_runner.py`), added proper `typing.TYPE_CHECKING` guards for string type annotations (`failure_detector.py`, `geo.py`), and added explicit public symbol re-exports (`swarm_os/services/control_plane/__init__.py`, `organism_console/__init__.py`). Verified with 349/349 tests passing.
- **Alias corrected to the actual model**: The default MTP 4B was served under a misleading dual alias (so the runtime "didn't change"). Now the server advertises the honest alias `qwen3.5-4b` only, and the entire runtime was renamed to `qwen3.5-4b`: `start_llama.bat` / `start-dev.ps1` / `start-dev-fixed.ps1` (default + `qwen3.5-4b` + `qwen3.5-4b-mtp` branches), `config/agent_models.json`, `model_registry.py`, `stream_runner.py`, `shared_model_registry.py`, `orchestrator.py`, `reflection_loop.py` (`LOCAL_MODEL`), `recovery_engine.py`, `offline_learner.py`, `genetic_mutation_loop.py`, `llm/client.py`, `rv_finder/llm.py`, `upwork/*`, `brain.py`, `_memory_bridge_base.py`, `capabilities/models.py`, `workers/*`, `kernel/genetics.py`, `routes.py`, `zenith/llm/router.py`, organism_console (cli/state_store/commands/routing), both consoles' frontend, and all tests.
- **Model switch**: Updated `start_llama.bat`, `config/agent_models.json`, and `model_registry.py` from `deepseek-coder` → `qwen3-14b` → `qwen3.5-4b`
- **Thinking mode**: Added `/no_think` to both system prompt paths in `_llm_prompts.py` for Qwen3 compatibility
- **Ollama → LlamaClient**: `swarm_os/infra/ollama.py` renamed to `llama_client.py`, `OllamaClient` → `LlamaClient`
- **Async migration**: `QdrantClient` → `AsyncQdrantClient` across `vector_store.py`, `tool_registry.py`, etc.
- **New services**: `chat_service.py`, `llm_client.py`, `memory_daemon.py`, `reflection_loop.py`, `security_gate.py`, `system_service.py`, `knowledge_graph.py`, `danger_room.py`, `token_manager.py`
- **New repositories/**: `event_log_repo.py`, `graph_repo.py`, `mutation_repo.py`, `snapshot_repository.py`, `file_snapshot_repository.py`
- **Control plane expansion**: 17 modules in `services/control_plane/` (router, planner, critic, strategy, guardian, etc.)
- **API expansion**: `routes.py` +420 lines, new `api_features.py` (534 lines), new `dependencies.py`
- **RV finder packaged**: 1,275-line `swarm_os/services/rv_finder.py` split into `swarm_os/services/rv_finder/` package (see module map). Bug-fix pass: junk-title filter (`_is_junk_title`), type-filter aliases (`class b/c`, `van`, `motorhome`), title-only classification in `_parse_snippet(title, body, url)`, PPL detail-fetch resilience (`return_exceptions=True`), `best_motorhome` requires a title-confirmed motorhome, deep-dive `num_retries=0` + 60s cloud / 300s local timeouts (litellm retry-hang was eating the old 120s budget). 33 tests in `tests/test_rv_finder.py`. UI: `organism-console/src/components/organism/RvFinderRunner.tsx` (new) calls `POST /features/rv-finder/search` directly with budget/type/deep-dive controls; `AutomationRunner.tsx` branches to it for `automationId === "used-rv-finder"`.

- **Dep safety pins**: `mcp>=1.28.1,<2` (prevents accidental v2 SDK breakage), `uvicorn>=0.52.1` (shutdown timeout, memory leak fix)

- **Reflection distillation gated to diagnosable failures**: `_distill()` in `swarm_os/services/reflection_loop.py` gained a `fix_class` parameter (from `diagnostician.py`: `prompt_sensitivity` vs `model_variability`). `model_variability` failures skip the LLM call entirely (early return `""`), keeping the single llama.cpp generation slot free for real work. Missing/unknown `fix_class` defaults to running distillation (fail-open). `run_reflection()` threads `fix_class` from diary entries AND classifies untagged entries via the Diagnostician (same deterministic logic the Governor uses), so the gate is live end-to-end — not forward-looking. Tests: `tests/test_distill_gate.py` (5 tests — MV skip, PS runs, None runs, diary→MV wire, diary→PS wire).

- **Dedicated 0.8B summarizer server on port 8084**: `start-dev.ps1` now serves `Qwen3.5-0.8B.Q4_K_M.gguf` (alias `qwen3.5-0.8b`) on a dedicated `:8084` port for memory consolidation/distillation. `swarm_os/memory/_memory_bridge_base.py` `LLAMA_SUMM`/`SUM_MODEL` point there (was the main 4B slot on 8080), and `swarm_os/services/reflection_loop.py` `_distill()` uses it as the local fallback — so summarization no longer steals the generation slot and distills finish fast (~15 t/s, 512-token cap, 120s timeout).

- **Distiller 402 skip removed**: When OpenRouter returns 402 (credit exhaustion), the distiller now falls through to the dedicated 0.8B summarizer (`qwen3.5-0.8b` on `:8084`) instead of skipping entirely — a fast, small last-resort local fallback that no longer burns 300s on the single generation slot.

- **Distiller now tries ALL cloud providers**: `swarm_os/services/reflection_loop.py` `_distill()` — the distiller previously only tried OpenRouter then local. Now cycles through Groq, NVIDIA, Gemini, and OpenCode paid API before falling back to local. Cloud max_tokens lowered to 300 to fit under OpenRouter credit limits. 402 errors auto-retry with fewer tokens (parses "can only afford N" from the error message).

- **CLI health probe retry logic**: `organism_console/ui/banner.py` — the startup health check for `rob` shows `checking ...` instead of `FAIL` when the backend is busy (distiller, memory consolidation). Retries 3 times with 3s gaps instead of one-shot timeout.

- **Multi-intent coordinator routing**: `runtime_v2/api/_agent_routing.py` `fast_route_coordinator()` now detects goals matching multiple agent keywords (e.g. "analyze codebase AND search internet") and falls through to the LLM coordinator for proper decomposition instead of fast-routing the entire goal to just the first keyword match.

- **ngram-mod spec decode hardcoded**: `start-dev.ps1` — the `@spec` PowerShell splat inside `Start-Job -ScriptBlock` was dropping the ngram-mod flags. Fixed by passing args as literal parameters. n-match tuned to 16 (minimum without quality warning, per llama.cpp). Result: ~8-9 t/s vs ~6 t/s plain.

- **Cloud fallback chain reordered + OpenCode paid API added**: `runtime_v2/services/fallback_manager.py` — the fallback chain had local llama.cpp first (wrong — defeats cloud-first design). Reordered: Groq free → NVIDIA free → Gemini free → OpenRouter free (depleted credits, last) → **OpenCode paid** (`deepseek-chat` via `$OPENAI_API_KEY`/`$OPENAI_API_BASE`) → local qwen3.5-4b last. `_llm_client.py` `build_kwargs` routes `openai/gpt-*`, `openai/o1-*`, `openai/o3-*`, and `openai/deepseek-*` to the OpenCode API. `stream_runner.py` fallback filter uses `_is_local_model()`.

- **DeepSeek direct is now the PRIMARY cloud fallback**: `runtime_v2/services/fallback_manager.py` — new `_get_deepseek_direct_fallback()` returns `deepseek/deepseek-v4-flash` (litellm's native `deepseek/` provider → `api.deepseek.com/v1`, keyed by `DEEPSEEK_API_KEY`; verified via `get_llm_provider`) whenever the key is set, and it is prepended ahead of the Groq/NVIDIA/Gemini/OpenRouter/OpenCode chain. Cheapest path ($0.14/M input miss, **$0.0028/M cache-hit input**, $0.28/M output). `build_kwargs` needs no change — litellm natively routes `deepseek/*`. Also added `"insufficient balance"` to `_PERMANENT_ERROR_MARKERS` (the direct 402 body reads `"Insufficient Balance"`, which did not match `"insufficient credits"`), so a billing-402 pins the model at max cooldown and `get_live_fallbacks()` skips it until a top-up + `record_model_success` clears it. No-op (returns `[]`) when the key is absent. `stream_runner.py`/`_llm_client.py` cloud filters already include it (`deepseek/*` is not `openai/`-local).

- **NVIDIA free tier now leads the cloud chain (#1) with deepseek-v4-flash**: `runtime_v2/services/fallback_manager.py` — the NVIDIA NIM free API (keyed by `NVIDIA_API_KEY`) hosts `deepseek-ai/deepseek-v4-flash`, so the chain order is now **NVIDIA free v4flash → DeepSeek direct → Groq → Gemini → OpenRouter → OpenCode → local**. NVIDIA models sort with `deepseek` first and `flash` before `pro`/`coder`, so the free flash model leads the NVIDIA batch. Cost: NVIDIA free tier = $0 (added to `usage_log.py` pricing table); DeepSeek direct stays #2 at $0.0028/M cache-hit input.

- **DeepSeek-first cloud chain (always-connected DeepSeek)**: `runtime_v2/services/fallback_manager.py` — researched 2026-08 provider landscape (DeepSeek V4 Flash = $0.14/$0.28/M direct with $0.0028 cache-hit; OpenRouter hosts the same flash at **$0.09/$0.18** via the `-0731` build, routed across ~22 upstream providers for best single-endpoint uptime; NVIDIA free NIM hosts `deepseek-ai/deepseek-v4-flash`). New `_get_deepseek_openrouter_fallback()` (guaranteed OpenRouter DeepSeek entries even if the catalog fetch fails) plus a reordered, deduped chain that keeps ALL DeepSeek-capable endpoints contiguous so a provider failure falls to the NEXT DeepSeek — never straight to a non-DeepSeek model: **NVIDIA free v4flash → DeepSeek direct → OpenRouter DeepSeek (0731 + base flash) → other OpenRouter cheap/free → Groq → Gemini → OpenCode → local**. OpenRouter batch sort now prefers `deepseek` models first. `usage_log.py` pricing table updated with `deepseek-v4-pro`, `-0731` ($0.09/$0.18), and OpenRouter base flash ($0.14/$0.28).

- **FLASH-ONLY policy (no `deepseek-v4-pro`)**: `runtime_v2/services/fallback_manager.py` — DeepSeek models are allowed in the fallback chain ONLY as v4-flash variants. `deepseek-v4-pro` is 3x the flash price ($0.435/$0.87 vs $0.14/$0.28) and legacy `deepseek-chat`/`deepseek-r1` aliases retired 2026-07-24, so both `_fetch_openrouter_models()` and `_fetch_nvidia_models()` skip any DeepSeek model id without `flash` in the name (this also drops `deepseek-coder-*` variants). The OpenRouter fallback default list (used when the catalog fetch fails) now lists the flash variants instead of the retired `r1:free`/`chat:free`. `deepseek-chat` strings elsewhere (analysis-cloud default, distiller, rv-finder) are OpenRouter's flash alias, not pro.

- **Ultra-cheap Ling worker tier + cloud fan-out ON by default**: `runtime_v2/services/fallback_manager.py` — InclusionAI **Ling-2.6-flash** (104B MoE / 7.4B active / 256K ctx) at **$0.01/$0.03 per M** on OpenRouter (14x cheaper than DeepSeek input), plus free `ling-3.0-flash:free`. Researched 2026-08: Ling is positionally an ultra-cheap, high-volume **fan-out/routing tier** model — its coding/terminal scores are low (SciCode 27.1, Terminal-Bench 21.2, HLE 6.2) so it does NOT lead hard analysis. New `_get_ling_flash_fallback()` injects both entries into the cloud chain right after the DeepSeek entries: **NVIDIA free → DeepSeek direct → OpenRouter DeepSeek (0731+base) → Ling-3.0-free → Ling-2.6 → other free → OpenCode → local**. `usage_log.py` pricing registered (`ling-2.6-flash` 0.01/0.003/0.03; free = 0). Tests: `tests/test_ling_fallback.py` (5).
- **Routing default flipped `local_only`→`auto` + CLI tracker fixed**: `runtime_v2/services/_llm_client.py::get_routing_mode()` and `organism_console/cli.py::main()` now default `SWARM_ROUTING_MODE=auto` (local-first, cloud fan-out fallback active) so the DeepSeek/Ling tier is actually used; `/local` (`cmd_local`) still forces fully-offline `local_only`, and `SWARM_ANALYSIS_CLOUD`/`SWARM_ROUTING_MODE` still gate the analysis cloud hop. `CLOUD_MODEL_ALLOWLIST` (set by `/cloud on`) is a dead env var — never read by the backend; routing is governed solely by `SWARM_ROUTING_MODE` (+`OPENROUTER_API_KEY` for analysis agents). `organism_console/token_tracker.py::_classify_provider()` was buggy (any `/` model map済 to `openrouter_paid`; local `openai/qwen3.5-4b` and bare `qwen3.5-4b` were miscounted as paid OpenRouter) — rewrote to mirror the backend's provider naming: local llama.cpp, `deepseek/` direct bucket, `openai/deepseek-*`→paid, `nvidia_nim/`→nvidia, `openrouter/...:free`→free. Added `deepseek`/`openai_paid` to `_PROVIDER_KEYS` + status labels + colors. Tests: `tests/test_cli_tracker.py` (7).

- **OpenCode Go leads the cloud chain (funded account, v4-flash ONLY, DeepSeek-direct LAST)**: `runtime_v2/services/fallback_manager.py` — the user moved their card funding onto the OpenCode Go subscription, so the chain now leads with the three `deepseek-v4-flash` options INLINE (**NVIDIA free NIM → OpenCode Zen FREE → OpenCode Go PAID**), then Groq/Gemini free → Ling ultra-cheap fan-out → OpenRouter → **DeepSeek direct (paid api.deepseek.com) LAST**. OpenCode serves ONLY `deepseek-v4-flash` (GLM/Kimi/Qwen/pro entries fully removed per user preference — no `openai/go/` markers anywhere). Corrected the OpenCode endpoint — `OPENAI_API_BASE` was `https://api.opencode.go/v1` (a domain that does NOT resolve); the real endpoints are `https://opencode.ai/zen/v1` (FREE tier) and `https://opencode.ai/zen/go/v1` (PAID Go, verified 200 live with the key). Model strings: `openai/zen/deepseek-v4-flash` → free Zen base, `openai/deepseek-v4-flash` → paid Go base. `_is_local_model()` treats `zen/` as cloud; `get_live_fallbacks()` splits local-vs-cloud by `_is_local_model()` instead of `startswith("openai/")` (which misclassified OpenCode cloud models as local and pushed them last). **Analysis-cloud default flipped to the funded OpenCode Go flash**: `_llm_client.py::_analysis_cloud_model()` now returns `openai/deepseek-v4-flash` (was `openrouter/deepseek/deepseek-chat`) and `_analysis_cloud_enabled()` requires `OPENAI_API_KEY` (was OpenRouter); the forbidden-model safeguard also enforces the Go flash. `reflection_loop.py` `CLOUD_MODEL` and `rv_finder/llm.py` deep-dive now also use `openai/deepseek-v4-flash`. `usage_log.py` prices Go/Zen at $0 (subscription-billed); CLI tracker classifies them `openai_paid`. Tests: `tests/test_opencode_go_chain.py` (5), `tests/test_analysis_cloud_routing.py` (updated to OpenCode Go default).



- **`is_cloud` + billing-402 degrade unified on `_is_local_model()` (4-expert review consensus)**: `runtime_v2/services/_llm_client.py` + `runtime_v2/services/stream_runner.py` — after the analysis-cloud default became `openai/deepseek-v4-flash`, three places still used `startswith("openai/")` to mean "local llama.cpp", misclassifying the PRIMARY cloud model as LOCAL: (1) `complete_for_tool_decision` `is_cloud` sent llama.cpp-only params (`id_slot`/`n_predict`/`cache_prompt`) AND the grammar `response_format` to the OpenCode Go endpoint; (2) `stream_runner`'s billing-402 degrade skipped the local-fallback branch for the cloud model; (3) even when it passed, `get_litellm_model` re-entered the analysis-cloud hop and returned the same doomed cloud model (`fallback_model="qwen3.5-4b"` was discarded). Fixed: `is_cloud = not _is_local_model(litellm_model)` (matches the fallback split), and `get_litellm_model` gained `force_local: bool = False` which skips the analysis-cloud branch — the 402 path now calls `get_litellm_model(agent_id, "qwen3.5-4b", force_local=True)` so a billing failure genuinely degrades to local qwen. Also `stream_runner` clears the `fallbacks` list on that local retry so litellm cannot re-try the doomed cloud chain. Also removed two tautological test assertions (`assert ... or True` in `test_opencode_parity.py` and `test_healing_e2e.py`). Tests: `test_analysis_cloud_routing.py` +2 (force_local, cloud-not-local classification).

- **4-expert security hardening pass (2026-08)**: `swarm_os/` + `runtime_v2/` — the Security expert's findings implemented:
  - **`code_exec` alias removed** (`capability_router.py`): it mapped to `SandboxReplHandler` under an unguarded name, bypassing the `is_state_changing`/approval gate that only checked `sandbox_repl`. Removed from `HANDLER_MAP` + all tool/affinity lists (`brain.py`, `default.py`, `migrations.py`, `genetics.py`, `tool_registry.py`) → the canonical tool is `sandbox_repl`.
  - **`sandbox_repl` gate**: `SecurityGate.scan_code()` (new AST scan for inline code) runs before ANY Python execution — blocks `exec/eval/compile/__import__/open` + `subprocess/os/sys/socket/ctypes/pty/shlex`. Runs under `python -I` (isolated mode). Blocked code returns `{"ok": False, "stderr": "Security Gate blocked..."}` instead of executing.
  - **Screen self-bypass closed** (`screen.py`): `set_screen_autonomous`/`reset_screen_action_count` are now gated behind `SCREEN_AUTONOMOUS` — an agent in human-control mode CANNOT flip itself to autonomous (real mouse/keyboard takeover) or reset the runaway-action cap.
  - **`mcp_register` allowlist** (`tool_executor.py`): only `npx`/`node`/`python`/`python3`/`uvx` launchers allowed; shell metacharacters (`&&`, `||`, `;`, `|`, `$(`, backtick) rejected.
  - **Prompt-injection hard boundary** (`tool_executor.py` `_sanitize_string`): instruction-like directives ("ignore previous", "you are now", "system override"…) are now REDACTED (replaced with a marker) not just HTML-escaped + annotated — the model never sees the raw instruction text.
  - **Read-before-write extended to `write`** (`tool_executor.py`): writing over an EXISTING un-explored file is now blocked (was patch-only, so a poisoned `write` silently clobbered real code). Plus per-run isolation: `_explored_paths`/`_filesystem_read_cache` are cleared at each `step_agent_stream`, and read-cache entries are evicted on write/patch (no stale reads).
  - **Web-fetch SSRF denylist** (`web_search.py` `_ssrf_check`): blocks loopback/private/link-local addresses, cloud-metadata hosts (169.254.169.254, metadata.google.internal), and hosts resolving to non-public IPs.
  - **PowerShell sandbox gate** (`sandbox_repl.py`): the `powershell` branch now rejects destructive/system-mutating commands (Remove-/Stop-/Set-/Format-/new-service, rm/del, kill/taskkill, shutdown, diskpart, icacls, redirects/pipes) — it was the last un-gated code-exec path (Python was already AST-scanned).
  - **Opt-in loopback API token** (`main.py` middleware): if `SWARM_API_TOKEN` is set (in `.env`/env), every request except `/health` `/readyz` `/` `/docs` must carry `Authorization: Bearer <token>` — closes the unauthenticated-writer surface (code exec, agent steps, heal/admin) for any local process/browser page when deployed. CLI (`api_client.py` sync + async) reads the same env var and attaches the header. No-op when unset (keeps local single-user dev open). Tests: `tests/test_api_token.py` (5).
  - Tests: `tests/test_security_hardening.py` (14, incl. PowerShell gate).
- **4-expert cleanup pass (2026-08)**: deleted dead modules/files (zero importers, verified): `swarm_os/genetics/mutator.py` (divergent dict-based `mutate` — the one real correctness hazard, only consumed by dead `cycle.py`), `swarm_os/cycle.py`, `swarm_kernel_BACKUP.py`, `_legacy_kernel_backup/`, `swarm_os/agents/`, `swarm_os/genome/`, `swarm_os/simulation/` (empty), `swarm_os/default.py`, `swarm_os/registry.py`, `swarm_os/organism/` (contracts-only package), `swarm_os/app/services/status_service.py` (scaffold), and the `swarm_os/selection.py` re-export shim (0 importers). AGENTS.md's stale `swarm_os/rest/` module-map entry replaced with a "removed" note. `swarm_os/swarm_kernel.py` (root) stays — it IS used by the CLI runner + `test_resume_flow.py`.
- **`src/` third agent stack removed (2026-08)**: `src/` was a ~6.6k-line parallel agent runtime (HybridMemory/DynamicRouter/SelfHealingAgentRuntime) the live app never imported — only `tests/test_routing.py`, `tests/test_agent_memory.py`, and `tests/test_divide_by_zero.py` exercised it (via `sys.path.insert` path-hacks). All four were deleted after research confirming: zero non-test importers, no config/build references, and the resilience patterns it tested are served live by `swarm_os/healing/` + `fallback_manager.py` cooldowns. `docs/ARCHITECTURE.md` gained a "REMOVED" status note. (`scipy` stays in requirements-lock — the loose root `record_*.py` audio scripts use it.)
- **Durable per-model cost telemetry**: `runtime_v2/services/usage_log.py` (new) — writes one JSON line per LLM completion to `data/usage/usage.jsonl` (gitignored), appending under a threading lock. Records real litellm `usage` only (never content-length estimates): model, provider bucket (`deepseek_direct`/`openrouter`/`openai_paid`/`groq`/`nvidia`/`gemini`/`local`), prompt/completion/cached tokens, estimated cost, source, agent_id. Cost table: DeepSeek direct $0.14/M miss / $0.0028/M cache-hit / $0.28/M output; OpenRouter DeepSeek $0.0896/$0.1792; local = $0; unknown cloud = `null` (honest, no guessing). `estimate_cost()`, `extract_usage()`, `record_response()`, `usage_report(days)` aggregator. Wired into all 6 litellm call sites: `_llm_client.py` `complete_for_tool_decision` (source=`tool_decision`) + `stream_content` (`stream_content`), `api/routes.py` `/generate`, `chat_service.py` `autoassign`, `rv_finder/llm.py` deep-dive, `reflection_loop.py` `_distill` (+402-clamped retry as `distill_retry`). Imported lazily at each site (avoids runtime_v2↔swarm_os circulars); never raises (write failures debug-logged). Replaces the in-memory-only `token_tracker.py` counters (which reset every restart) for cost analysis. 7 tests in `tests/test_usage_log.py`.

- **Healing watchman yes/no prompt**: `organism_console/core/healing_watchman.py` — when the Governor flags a healing action as `approval_required`, the watchman now shows a Rich `Confirm.ask()` yes/no prompt directly in the CLI instead of requiring a separate `/heal run approve` command.

- **Crawl4AI web fetch integration**: `swarm_os/lib/mcp/web_search.py` `web_fetch_handler` — replaced plain HTTP+regex HTML stripping with Crawl4AI (browser-level extraction → clean LLM-friendly markdown). Falls back to the original HTTP path if Crawl4AI is unavailable. `pip install crawl4ai`, added to requirements.

- **v1.0 Production Readiness & Test Harness Stabilization**:
  - **API Route Integrity (`swarm_os/api/routes.py`)**: Cleanly removed unreachable dead code without disrupting dynamic model discovery (`_safe_ollama_models`) or capability enumeration (`_build_capabilities`).
  - **Test Harness Lifespan Hang (`tests/conftest.py`)**: Resolved an indefinite hang during FastAPI `TestClient` startup. Because `swarm_os/app/main.py` locally imports `get_mcp_manager` and `run_system_probes` inside its lifespan block, module-level mocks were bypassed. Added explicit call-site mocks for the local bindings so background subprocesses and psutil calls do not hang test execution.
  - **Headless / Background Windows UI Resilience (`swarm_os/lib/mcp/screen.py`)**: Gated Win32 GDI calls (`GetForegroundWindow`, `EnumWindows`, `BitBlt`) against `1400: Invalid window handle` and GDI bitmap failures when running in non-interactive background or CI sessions. `foreground_window`, `list_windows`, and `screenshot(save=True)` now gracefully return safe fallbacks instead of crashing.
  - **Comprehensive Automated Test Coverage**: Achieved a 100% pass rate across 324 automated tests (291 core pytest suite, 10 screen control computer-use tests, 6 client integration smoke tests, 4 backend smoke tests, 12 CLI command tests, and 1 full system hardmode integration test).

---

## Bug Fixes (Codebase Analysis)

### MEDIUM — `/memory` queried a nonexistent Qdrant collection (`upwork_learning`) → every query 404'd
`organism_console/_commands_ai.py::cmd_memory`: the CLI hardcoded the Qdrant collection `upwork_learning` for both `query` and `inject`, but the memory bridge writes sharded `agent_memory_*_v2` collections — so every `/memory query` printed "Qdrant search failed with status 404" and every `/memory inject` printed "Qdrant upsert failed with status 404" (silently broken feature). Added `_resolve_collection()` which lists live Qdrant collections and prefers `agent_memory_general_v2` → `general` → `agent_episodic_memory` → `swarm_memory` → any `agent_memory_*`/`ReflexionMemory`, with a graceful "no collection found" message when Qdrant has none. Verified live: `/memory query hello` returns real memories from `agent_memory_general_v2`; `/memory inject` persists.

### MEDIUM — `/upgrade` skill phase crashed on missing `fastembed` (undocumented dependency)
`swarm_os/memory/intelligence/skill_memory_engine.py`: `from fastembed import TextEmbedding` was a module-level hard import, but `fastembed` is **not** in `requirements.txt`. The `/upgrade` command chain (`SelfImprovementAgent.__init__` → `SkillMemoryEngine()`) raised `ModuleNotFoundError: No module named 'fastembed'` (caught by `/upgrade`'s try/except, so the skill-memory phase silently degraded). Fixed: `fastembed` is now an optional import (guarded `try/except ImportError`, `TextEmbedding = None` fallback); `SkillMemoryEngine()` constructs fine without it and `embed()` raises a clear "pip install fastembed" `RuntimeError` only when actually called. Regression test: `tests/test_cli_terminal.py::test_skill_memory_engine_tolerates_missing_fastembed`.

### HIGH — Self-healing loop closed end-to-end (tool failures now reach every consumer)
`runtime_v2/api/agent_service_v2.py` + `swarm_os/services/reflection_loop.py`: Audited the detection→distillation→retrieval→application loop and found three silent drops:
1. **Tool failures never reached `events.jsonl`** — `_handle_tool`'s failure branch called `_remember_failure` but never `_record_event("tool_result", ...)`. The event store only ever got `generation_completed`/`agent_action`/`stream_completed`, so RepairWatchman and `/autofix` (which tail `events.jsonl` for `event_type == "tool_result"`) were **starved** — they could never repair anything. Fixed: `_handle_tool` now records a `tool_result` failure event (tool, arguments, ok:false, error) so the repair path sees every failure.
2. **The distiller distilled the WRONG failures** — `run_reflection()` reads `organism_diary.jsonl`, but that diary is written by the genetic kernel (`kernel/organism.py`) with eval noise (`http_422`, `[WinError 10061]`, no `component`). Agent tool failures never wrote to the diary. Result: 137/149 ReflexionMemory points were `component:"unknown"` noise rules. Fixed (two parts): (a) `_remember_failure` now also appends a `tool_failure` entry with `component`/`agent` to `DIARY_PATH`; (b) `get_latest_failure()` now **prefers component-tagged entries** over bare-error genetic noise, falling back to the last error only if no agent failure exists. Verified: `run_reflection` now distills `File not found: agent_service.py` and skips `http_422`.
3. **Turn-budget exhaustion was invisible** — max-turns only yielded a string; no event/reflexion/heal. Compound goals (filesystem + web_search) that ran out of turns left no trace. Fixed: the max-turns path now records a `turn_budget_exhausted` event AND a ReflexionMemory rule (`action=max_turns_reached`, correction to minimize tool calls / interleave exploration with the required tool), so the circuit breaker / ReflectionDaemon / watchman can act on it.

The retrieval/application link (stored rule → `[PAST-MISTAKE WARNING]` injected via `check_for_past_mistakes`) was already working and was verified against a live Qdrant query. Tests: `tests/test_failure_lessons.py` +4 (diary write w/ component, get_latest_failure prefers component, tool_result event persisted, dedup preserved); full suite 354 passed.

### HIGH — Coordinator short-circuits to `final` on stale episodic memory (deterministic guard, not just a prompt rule)
`runtime_v2/api/agent_service_v2.py` + `runtime_v2/api/_agent_routing.py`: A goal like "analyze my codebase for bugs and search internet for improvements and upgrades" triggered the multi-intent fallback to the LLM coordinator (correct), but the qwen3.5-4b coordinator then returned `action=final` claiming the task "was already completed" — trusting injected episodic memory (e.g. IDs `32619be6…`, `c71f9e2a…`) over the coordinator's own rule 4 ("DO NOT use action=final if the goal has an action verb"). Prompt rules alone were insufficient. Added a **hard code-level guard**: in `_get_decision()`, when `agent_id == "coordinator"` and the LLM returns `action=final`, `matches_task_keywords(prompt)` is checked; if the goal contains any routing keyword (analyze/search/fix/build/review/…), the final is **coerced to `{"action": "delegate", "target_agent": best_route_target(prompt), "task": prompt}`** — never a prose answer to a real task. Greetings/chat still pass through. New helpers `matches_task_keywords()` / `best_route_target()` in `_agent_routing.py`; 5 tests in `TestCoordinatorShortCircuitGuard`.

### HIGH — Analysis agent skipped web_search on internet goals (prompt + deterministic guard)
`runtime_v2/prompts/system_prompts.py` + `runtime_v2/api/agent_service_v2.py`: An "analyze codebase and search internet for improvements" goal made `code_analyzer` read a few files then immediately call `action=final` with `"response": "Task complete."` — it never ran `web_search`, and the premature-final guard only blocked finals with NO files read (`_fetched_content`), not finals that skipped the internet step. Two fixes: (1) the `code_analyzer` system prompt now makes web_search + web_fetch MANDATORY for internet goals and requires a real multi-paragraph synthesized answer (not a one-liner); (2) a code-level guard — `_CallState.did_web_search` is set on a successful `web_search`, and `_handle_final` rejects the first `final` (CONTINUE + corrective message) on internet-involving goals (search internet/the web, improvements/upgrades, best practices, modern/latest/sota) when the agent is an `ANALYSIS_AGENT` and never searched. Also flipped the analysis-cloud decision model to OpenCode Go v4-flash (`openai/deepseek-v4-flash`) which follows instructions far better than local qwen3.5-4b. Tests: `test_opencode_parity.py` (3 new: block-without-search, allow-after-search, existing verify guard). **Follow-up (2026-08): the original guard was a one-shot latch** — `if not state._web_final_rejected:` set the flag on the first `final`, so a second `final` sailed through and the agent "completed" the goal without ever doing the internet research (reproduced live: `code_analyzer` read files, one rejection, then a passing `final` → "max turns reached", never called `web_search`). Now `_handle_final` rejects on **every** `final` until `did_web_search` is true (the MAX_TURNS loop bounds the rejection, so no infinite loop) — the agent cannot finalize an internet goal without actually running `web_search`/`web_fetch`. Tests: `test_opencode_parity.py` +1 (`test_internet_goal_blocks_second_final_too`).


### HIGH — Goal verification suite was picking up pre-existing uncommitted work as "agent changes"
`organism_console/loops/autonomous.py`: `run_test_suite()` selected test targets from `git diff --name-only` and the loop gated on `git status --porcelain` having *any* output — so a dirty working tree (dozens of `M` files from earlier sessions) made every goal-loop attempt run unrelated pre-existing tests, feed their failures back to the coordinator, and derail the goal into "fix the test assertion failures" instead of the user's actual request. Fixed: the loop now snapshots the tree **before each attempt** (`_git_status_paths()` helper, porcelain parsing) and only verifies `changed_this_attempt = current - baseline`. `run_test_suite()` gained a `baseline: set[str] | None` param that skips pre-existing paths, and now reads `git status --porcelain` (which includes untracked agent-created files) instead of `git diff --name-only` (tracked only). The read-only "no changes" branch keys on `changed_this_attempt` being empty rather than the whole tree being clean.

### CRITICAL — `PolicyNode` NameError in self-healing escalation path
`src/core/agent_runtime.py`: the `LEVEL_2_FALLBACK` escalation branch constructed `PolicyNode(...)` but never imported it (it's exported from `src.orchestration.policy_graph`). Any task that escalated to a fallback agent crashed with `NameError: name 'PolicyNode' is not defined`. Added the import.

### HIGH — Fallbacks leaked the primary's `api_base`/`api_key` across all providers (cross-provider chain was dead)
`runtime_v2/services/_llm_client.py` + `runtime_v2/services/stream_runner.py`: `build_kwargs` computed `api_base`/`api_key` ONCE from the primary model and passed a flat list of **string** fallback ids into litellm's `fallbacks` — litellm reuses the primary request's kwargs for every string fallback, so NVIDIA/Groq/Gemini fallbacks all pointed at the OpenCode Go URL and inherited the OpenCode `api` key. Any primary outage degraded "the chain" into the same wrong endpoint repeatedly (the earlier `%22...zen/go/v1%22` showed up on the Groq + NVIDIA attempts, not just OpenCode). Refactored: new `_endpoint_for()` (single source of truth for both primary and fallbacks) + `_fallback_entry()`; `build_kwargs` now emits per-fallback **dicts** `{model, api_base?, api_key?}` so native providers (`nvidia_nim/`, `groq/`, `gemini/`, `openrouter/`, `deepseek/`) carry **no** explicit base/key (litellm uses its own provider config) and OpenCode Zen/Go entries carry their own endpoint. Applies to both `complete_for_tool_decision` and `stream_content`. This is also a credential-hygiene fix (no cross-provider key leak). Test: `test_opencode_go_chain.py::test_fallbacks_scoped_to_own_endpoint_no_cross_provider_leak`; 22 pass.

### CRITICAL — Quoted `OPENAI_API_BASE` in `.env` leaked `"` into every cloud URL (whole fallback chain failed → massive slowdown)
`swarm_os/config/settings.py::_load_dotenv()` copied env values verbatim (`os.environ[key] = value.strip()`), never stripping surrounding quotes. With `OPENAI_API_BASE="https://opencode.ai/zen/go/v1"` in `.env`, the literal `"` characters landed in `os.environ` and survived even `load_dotenv(override=True)` (verified empirically). Every `os.getenv("OPENAI_API_BASE")` caller (`_llm_client.py` `build_kwargs`, `swarm_os/services/reflection_loop.py`, `rv_finder/llm.py`) then passed `"https://opencode.ai/zen/go/v1"` as api_base, which litellm URL-encoded as `%22` and treated as a relative path — `unknown url type: '/%22https://opencode.ai/zen/go/v1%22/chat/completions'`. Because litellm's fallback machinery reused that one broken base across ALL providers (OpenCode Go → NVIDIA → Groq → Gemini), every provider failed and **each tool decision retried 3× across the broken chain** — the cloud was effectively dead-on-arrival and every agent run ground to a crawl. Fixed: `_load_dotenv()` now strips a leading+trailing matching `'`/`"` pair (matching the PowerShell loader in `start-dev.ps1` and python-dotenv semantics). Regression-checked: `python -c "import swarm_os.config.settings, os; assert os.getenv('OPENAI_API_BASE')=='https://opencode.ai/zen/go/v1'"`; 21 tests pass (`test_opencode_go_chain`, `test_analysis_cloud_routing`, `test_usage_log`). (Root cause of the "what's taking so long" agent stalls.)

### CRITICAL — `get_live_fallbacks` missing from `fallback_manager.py` (every tool-decision crashed with ImportError)
`runtime_v2/services/fallback_manager.py`: the module had `_cached_fallbacks` populated by `refresh_fallbacks_if_needed()` but **no `get_live_fallbacks()` function** to retrieve them. Every tool-decision site imported it — `stream_runner.py` (`from runtime_v2.services.fallback_manager import get_live_fallbacks, _is_local_model`), `_llm_client.py`, `swarm_os/api/agents.py`, `swarm_os/services/chat_service.py` — so every LLM call raised `ImportError: cannot import name 'get_live_fallbacks'`, tripped the circuit breaker after 3 consecutive failures, and derailed every `code_analyzer`/`debugger` run (the coordinator delegated, then the delegate crashed on its first decision). Added `get_live_fallbacks(mode="auto")` at the end of the module — calls `refresh_fallbacks_if_needed(mode)`, then filters out cooled-down models via `is_model_cooled_down()` before returning the live chain. Verified: `python -c "from runtime_v2.services.fallback_manager import get_live_fallbacks"` OK; 26 tests pass (`test_opencode_go_chain`, `test_ling_fallback`, `test_analysis_cloud_routing`, `test_usage_log`).

### HIGH — `openai/deepseek-chat` (OpenCode paid) filtered out of cloud fallbacks
`runtime_v2/services/stream_runner.py`: a locally-defined `_is_local_model()` shadowed the imported one from `fallback_manager.py` and didn't treat `openai/deepseek-*` as cloud — so the new OpenCode paid fallback (added to `fallback_manager.py`) was always filtered out of the tool-decision cloud chain. Removed the shadowing local definition so the shared `_is_local_model()` (which correctly handles `deepseek`) is used.

### HIGH — Shared pooled httpx client closed by caller, breaking every stream after the first
`organism_console/api_client.py` + `organism_console/ui/live_stream.py`: `call_api_async_stream()` was refactored to return the module-level pooled `AsyncClient`, and `live_stream.py` called `await client.aclose()` in its `finally` — closing the shared pool for every future caller (the 2nd+ stream then failed with a closed-client error). Fixed: `call_api_async_stream()` now returns only the response (returns `None` on `RequestError`, matching the old contract), callers close `resp` (releases the connection to the pool) instead of the client, and `_get_async_client()` self-heals if the pool was ever closed.

### MEDIUM — `EMBED_DIM` undefined in code-indexing modules
`swarm_os/lib/vector/code_indexer.py` and `swarm_os/lib/vector/context_retriever.py` referenced `EMBED_DIM` (in `_ensure_collection` and embed-failure fallbacks) but never defined it — every collection-create and every embed error path raised `NameError`. Added `EMBED_DIM = 768` (nomic-embed-text dimension).

### LOW — `make_request(i)` with undefined `i` in a routing test
`tests/test_routing.py` `TestFailoverUnderLoad::test_concurrent_failover` called `make_request(i)` from a `for _ in range(20)` comprehension; `i` was undefined and only resolved via scope leakage. Now `for i in range(20)`.

### MEDIUM — uvicorn venv out of line with declared `>=0.52.1`
The venv had uvicorn 0.49.0 while `requirements.txt`/`requirements-lock.txt` pin `uvicorn>=0.52.1` (shutdown timeout + memory-leak fix). Upgraded the venv to 0.52.1.

### LOW — stale `qwen2.5-coder:7b` example in chat autoassign prompt
`swarm_os/services/chat_service.py` `autoassign()` prompt example referenced `qwen2.5-coder:7b`; updated to `qwen3.5-4b`. Removed unused `SESS_MODEL` constant from `_memory_bridge_base.py`.

### HIGH — Runaway summarizer with no token cap
`swarm_os/memory/memory_bridge.py`: Added explicit `max_tokens: 500` caps and `timeout=60.0` to all `LLAMA_SUMM` calls (`_summarize`, `consolidation`, `cluster_graph_rag`) to prevent the 0.8B model from generating indefinitely (up to its 8192-token ceiling) and hanging graph_rag.

### MEDIUM — Coordinator short-circuits on stale episodic memory + generic verification
`runtime_v2/prompts/system_prompts.py`, `organism_console/loops/autonomous.py`: Added a strict rule to the `coordinator` preventing it from short-circuiting to `action=final` based on episodic memory if the goal contains action verbs (`analyze`, `search`, `fix`). Also patched the autonomous verification loop to dynamically evaluate read-only goals (where no files are modified) using a `reviewer` LLM, instead of blindly passing them.

### MEDIUM — Swallowed error details in memory logging
`swarm_os/memory/memory_bridge.py`: Upgraded bare `logger.warning("... error: %s", exc)` calls to `logger.exception()` to capture full stack traces for "vector store error", "summarization error", "consolidation LLM failed", and "GraphRAG clustering failed".

### HIGH — 9 per-request `httpx.AsyncClient` instances (no connection pooling)
`swarm_os/cognition/reranking.py`, `swarm_os/api/routes.py`, `swarm_os/capabilities/subagent.py`, `swarm_os/healing/failure_detector.py`, `swarm_os/infra/llama_client.py` (GLM path), `swarm_os/persistence/qdrant.py`, `swarm_os/infra/qdrant.py`, `swarm_os/services/chat_service.py`, `organism_console/api_client.py`: Each was creating/destroying a new `httpx.AsyncClient()` on every call — wasting TLS handshake + DNS resolution. Converted all 9 to module-level or instance-level lazy singleton pools with connection limits (`max_keepalive_connections=5, max_connections=20`) matching the already-pooled services.

### MEDIUM — 21 `asyncio.wait_for` calls migrated to `asyncio.timeout` context manager
`runtime_v2/services/tool_executor.py` (12 calls), `swarm_os/capabilities/lsp_tool.py` (3), `swarm_os/capabilities/sandbox_repl.py`, `swarm_os/healing/recovery_engine.py`, `swarm_os/services/reflection_loop.py`, `swarm_os/services/rv_finder/llm.py`, `src/core/agent_runtime.py`, `src/orchestration/orchestrator.py`: `asyncio.wait_for()` is deprecated in favor of the `async with asyncio.timeout()` context manager (cleaner cancellation, better composability, builtin `TimeoutError`). All 21 calls converted; `except asyncio.TimeoutError` → `except TimeoutError` where needed.

### MEDIUM — Silent `except:pass` blocks now log at debug level
`swarm_os/api/routes.py:66` (model-discovery failure was silently swallowed), `swarm_os/capabilities/lsp_tool.py` (stderr drain, kill block, client close/evict failures): Added `log.debug(...)` to previously bare `except Exception: pass` blocks so failures are findable in debug logs without changing runtime behavior.

### CRITICAL — `healing_watchman.py` NameError (`heal_result` not defined) from corrupt indentation
`organism_console/core/healing_watchman.py`: The recovery block (imports + the `symptom`/`run_coro_sync`/`finalize` body) had been accidentally de-indented to module scope, so `heal_result = ...` was referenced at import time before `_tick()` ever ran — the CLI crashed on startup with `NameError: name 'heal_result' is not defined`. Restored the imports to the top of the file and re-indented the block back inside `_tick()`.

### HIGH — grep/search filesystem op always returned "Unknown operation"
`swarm_os/lib/mcp/filesystem.py`: the alias normalizer maps `grep`/`search`/`find`/`grep_search`/`search_files` → `"search"`, but the dispatch handler only has an `elif operation == "grep":` branch. Every agent grep/search call fell through to `{"ok": False, "error": "Unknown operation: search"}` — silently breaking grep for every agent (and starving the `_record_fs_exploration` exploit-guard that keys on `grep`/`search`). Fixed by normalizing these aliases → `"grep"` so the handler (and `tool_executor` exploration tracking) match.

### MEDIUM — Copy-paste literal `` `n `` instead of `\n` in generated tool text
`organism_console/tools/tool_registry.py` `call_generate_api`: two f-strings embedded a literal backtick-`n` (`Goal: {goal}`nUsing memories: ...`), producing a visible `` `n `` in output. Replaced with real `\n` newlines.

### MEDIUM — Latent `AttributeError` in dead `learning/critic_engine.py`
`organism_console/learning/critic_engine.py` calls `self.repo.embed(...)` but `SkillRepository` (`skills/skill_repository.py`) defines no `embed` method. This class is dead code (only a stale `run_memory_evolution.ps1` references it; the active CriticEngine is `organism_console/review/critic_engine.py`) so it was left as-is rather than wiring a live path, but fixed its bare `except:`.

### MEDIUM — Bare `except:` swallowing KeyboardInterrupt/SystemExit
Converted `except:` → `except Exception:` (or specific types) in `swarm_os/core/patch_manager.py` (2×), `swarm_os/core/ci_engine.py`, `swarm_os/capabilities/lsp_tool.py`, `zenith/memory/graph_memory.py` (→ `OSError, SyntaxError`), `organism_console/learning/critic_engine.py` (→ `ValueError, TypeError`).

### MEDIUM — Thread-safety race in `get_mcp_manager()` singleton
`runtime_v2/services/tool_executor.py`: concurrent first calls could both spawn `ExternalMCPClientManager`. Guarded with an `asyncio.Lock`. (Added `import asyncio`.)

### CRITICAL — CPU P-core Single-Slot Optimization (`-np 1 -t 2 -tb 4`) & Gated Delta Net Fix
`start-dev.ps1`, `start-dev-fixed.ps1`, `start_llama.bat`: Default `n_slots = 4` (`-np 4`) with `-t 2` caused severe thread starvation on 2-core P-core CPUs, cutting generation speed in half (`3.04 tok/s` vs 6.01 tok/s baseline). Furthermore, `-ngl 99` caused Vulkan to disable fused Gated Delta Net ops (`fused Gated Delta Net (chunked) not supported, set to disabled`). Fixed by explicitly setting `-np 1 -t 2 -tb 4 -ngl 0` (`n_slots = 1` for zero thread starvation, 2 P-core generation threads, 4 SMT prefill threads, native CPU Gated Delta Net kernels). Achieves full `5.08–6.01 tok/s` generation and `80–107 tok/s` prompt prefill.

### HIGH — Orchestrator & LLM Client Timeout Bumps for Concurrent Reranking Bursts
`runtime_v2/services/stream_runner.py`, `runtime_v2/services/_llm_client.py`, `runtime_v2/api/agent_service_v2.py`: When agents like `code_analyzer` launch, semantic memory search triggers up to 47 concurrent reranking tasks on port 8082, temporarily saturating DDR5 memory bandwidth. Previous `90s`/`120s` timeouts caused premature aborts (`timeout=True`). Raised `_STEP_TIMEOUT` to `180.0s` (`stream_runner.py`) and litellm/call timeouts to `300.0s` (`_llm_client.py`, `agent_service_v2.py`).

### HIGH — Tool-decision timeouts now retry (single-slot queueing isn't a dead model)
`runtime_v2/services/stream_runner.py`: With `-np 1`, a decision timeout almost always means the request was queued behind a busy stream, not that the model is down. The retry branch previously excluded timeouts (`and not is_timeout`), so a 3-minute generation on the lone slot made `code_analyzer` give up after 1 attempt (`Tool decision failed after 1 retries (timeout=True)`). Timeouts now sleep 5s (letting the blocking generation finish) and re-enter the retry budget (3 attempts max). The outer `asyncio.timeout(300.0)` in `agent_service_v2.py` still bounds the loop, so it fail-fasts if the slot stays saturated. Also fixed the timeout reflexion memory, which wrongly blamed "RAM pressure / OS-level swapping" — it now records the real cause (busy single slot or sustained memory pressure).

### HIGH — 250-token cap caused truncated tool-decision JSON
`runtime_v2/services/_llm_client.py`: `local_max_tokens` was 250 — tool-decision JSON (thought + action + params) truncated mid-JSON, triggering retry loops. Raised to 4096, matching cloud path.

### MEDIUM — `threading.Lock()` in async code
`swarm_os/core/orchestrator.py`: `_generation_lock` was a blocking `threading.Lock` inside `async def generate()`. Replaced with `asyncio.Lock` + `async with`.

### MEDIUM — Hardcoded 8192 context vs server's 16384
`runtime_v2/services/_llm_client.py` + `stream_runner.py`: `num_ctx`/`_context_limit` were 8192, wasting half the 16K model context. Both raised to 16384.

### MEDIUM — Race on `_cached_models` globals
`swarm_os/core/orchestrator.py`: TOCTOU race between TTL check and HTTP fetch. Added `_models_cache_lock` (asyncio.Lock).

### MEDIUM — `test_step_agent_shape` was dead code
`tests/test_agents_smoke.py`: Unconditional `@pytest.mark.skip` never ran the test. Changed to conditional skip on 503 (backend down).

### LOW — Substring matching false positives in goal classification
`organism_console/loops/autonomous.py`: `"read" in "ready"` matched. Changed to word-boundary regex.

### LOW — `re.escape(m.strip())` lost trailing-space markers
`swarm_os/core/orchestrator.py`: `"class "` → `class` after strip, matching bare words. Removed trailing spaces from markers.

### LOW — Unparameterized type hint
`runtime_v2/services/_llm_client.py`: `AsyncGenerator[tuple, None]` → `AsyncGenerator[tuple[str, str], None]`.

### LOW — Redundant fence re-stripping
`runtime_v2/services/_llm_parser.py`: Salvage scan re-stripped fences already removed at top. Now reuses cleaned `text`.

### FULL AUDIT — 17 bugs across 7 files (2026-08-03)

**CRITICAL — `start-dev.ps1` `$specArgs` array was built but never passed into `Start-Job`**
`start-dev.ps1`: Lines 89-114 built `$specArgs` with the correct spec flags, but line 143 hardcoded `--spec-type $specType --spec-ngram-mod-n-match 24 ...` inline in the `Start-Job` block, completely ignoring `$specArgs`. Effect: `SWARM_SPEC_DECODE=0` (disable spec decode) had zero effect — spec flags were always sent. `SWARM_SPEC_TYPE=draft-mtp,ngram-simple` never got `--spec-draft-n-max 3`. `SWARM_DRAFT_MODEL` was silently ignored. Fixed by passing `$specArgs` and `$cacheReuseArg` as `-ArgumentList` and using `@spec` / `@cacheReuse` splatting inside the ScriptBlock.

**HIGH — `--cache-reuse 1024` unconditionally passed to MTP GGUF (unsupported)**
`start-dev.ps1`, `start_llama.bat`: The MTP GGUF (`kv_unified=false`) does not support `--cache-reuse`. The server logs `"cache_reuse is not supported by this context, it will be disabled"` on every boot and silently drops the flag. Fixed: `--cache-reuse` is now conditional — skipped when `$genModel` contains `"UD"` (MTP model identifier). `start_llama.bat` uses `findstr /i "UD"` for the same check.

**HIGH — `AsyncQdrantClient` missing `timeout` parameter (caused 408 startup errors)**
`swarm_os/services/vector_store.py`: `AsyncQdrantClient(url=settings.qdrant_url)` had no explicit timeout. During startup, Qdrant returned HTTP 408 (not yet ready), producing `"Error ensuring collection: Unexpected Response: 408"` and `"Failed to count Qdrant points: 408"` in every boot log. Fixed: `timeout=10.0`.

**HIGH — `_ensure_collection` had no retry logic (silent fail on startup race)**
`swarm_os/services/vector_store.py`: `_ensure_collection` ran once as a background task immediately on `__init__`. If Qdrant wasn't ready, it silently gave up. Fixed: 3-attempt exponential backoff (1s / 2s / 4s). On final failure, logs at `error` level with full context.

**HIGH — Blank error messages: `Memory consolidation failed:`, `vector store error:`**
`swarm_os/memory/memory_bridge.py`: `logger.warning("Memory consolidation failed: %s", exc)` was calling the outer catch but the exc was an outer-scope exception that had its message dropped. Fixed by adding `exc_info=True` so the full traceback appears in logs.

**MEDIUM — ngram-mod `n-match=24` too large, `n-match=8` too small → settled at 16**
`start-dev.ps1`: `--spec-ngram-mod-n-match 24` was too large for short tool-decision JSON (15.6% acceptance), and `n-match=8` triggered a llama.cpp quality warning (`ngram_mod n_match=8 is too small`). Settled at `n-match=16`, `n-min=32`, `n-max=64` — minimum without quality warning, ~8-9 t/s on the MTP 4B.

**MEDIUM — `vector_store.py` `count()` silently swallowed all exceptions**
`swarm_os/services/vector_store.py:199`: `except Exception: return 0` with no logging. Changed to `except Exception as e: logger.debug("Qdrant count failed: %s", e); return 0`.

**MEDIUM — `consolidate_memories()` / `cluster_graph_rag()` silently swallowed LLM HTTP failures**
`swarm_os/memory/memory_bridge.py`: Lines 607 and 678 had bare `except Exception: pass` inside the LLM POST blocks, causing fallback to string concatenation with no log. Fixed: `logger.warning("consolidation LLM failed for outcome '%s': %s", outcome, exc)` and `logger.warning("graph_rag cluster LLM failed for cluster %d: %s", idx, exc)`.

**MEDIUM — `_summarize()` burned 300s on busy slot instead of skipping gracefully**
`swarm_os/memory/memory_bridge.py`: `_summarize()` waited up to `timeout=300.0` on the single llama.cpp slot. When the main agent was mid-generation, this caused `ReadError`/`ReadTimeout` warnings in the logs. Fixed: explicit `except (httpx.ReadError, httpx.ReadTimeout)` caught before the generic handler and logged at `debug` level (slot busy is expected behaviour, not an error).

**MEDIUM — `memory_core.py` search timeout 5s too short under RAM pressure**
`runtime_v2/services/memory_core.py:300`: Qdrant search POST had `timeout=5.0`. With 29 GB RAM and DDR5 pressure from 4 llama.cpp instances, cold Qdrant queries often exceeded this. Raised to `timeout=15.0`.

**MEDIUM — 5 silent `except: pass` blocks in `memory_core.py`**
`runtime_v2/services/memory_core.py`: `_get_embedding_dimension()` (line 23), `remember_fact()` Qdrant PUT (line 228), `get_relevant_memories()` per-shard search (line 314), `dump_all_failures()` scroll loop (line 401), `get_failure_digest()` shard info (line 433) all swallowed exceptions silently. All now log at `debug` or `warning` level as appropriate.

**MEDIUM — `memory_core.py` used `print()` instead of `logging` throughout**
`runtime_v2/services/memory_core.py`: `rerank_memories()`, `init_memory_qdrant()`, `get_embedding()`, `_get_kg()`, `_save_kg()`, `deprecate_memory()` all used `print(f"...")` for error reporting — invisible in structured backend logs. All converted to `_log.warning(...)` / `_log.debug(...)`.

**LOW — `reflection_loop.py` 402 retry and Diagnostician failures silently swallowed**
`swarm_os/services/reflection_loop.py`: Lines 378 and 409 had `except Exception: pass`. Now `logger.debug("402 retry with fewer tokens failed: %s", exc)` and `logger.debug("Diagnostician failed during reflection: %s", exc)`.

**LOW — `main.py` SSL verification override failure silently swallowed**
`swarm_os/app/main.py:36`: `except Exception: pass` in the SSL context override block. Now `logger.warning("SSL verification override failed: %s", _ssl_exc)`.

**HIGH — GraphRAG + consolidation slot-busy timeouts logged as full tracebacks**
`swarm_os/memory/memory_bridge.py::cluster_graph_rag` (:683) and `consolidate_memories` (:607) post to the single-slot 0.8B summarizer on 8084 (`LLAMA_SUMM`). Both lacked the `httpx.ReadError/ReadTimeout` fast-path that `_summarize` has — when the slot is busy with a main-agent generation or another consolidation, they fell through to the generic `except Exception: logger.exception(...)`, spamming a full `httpx.ReadTimeout` traceback (surface symptom: `graph_rag cluster LLM failed for cluster 0: httpx.ReadTimeout`). Now both catch `(httpx.ReadError, httpx.ReadTimeout)` first, log at `debug` (slot busy = expected), and use the fallback text — matching the `_summarize` pattern. Generic non-timeout failures still log at error.

**LOW — `memory_bridge.py` `_is_duplicate()` swallowed JSON/hash errors**
`swarm_os/memory/memory_bridge.py:466`: `except Exception: return False` with no log. Now `logger.debug("duplicate check error: %s", exc)`.

**MEDIUM — `MemoryDaemon` and `EmbeddingService` startup races**
`swarm_os/services/memory_daemon.py` and `swarm_os/services/embedding_service.py`: On boot, `start_manager_daemon` fired instantly, hitting Qdrant (`httpx.ConnectTimeout`) and `EmbeddingService` hit `llama.cpp` before it loaded. Fixed: Added `await asyncio.sleep(15.0)` to daemon startup, and a 3-attempt exponential backoff retry to `embed()`.

**MEDIUM — Crawl4AI Web Fetch returning empty markdown on JS-heavy pages**
`swarm_os/lib/mcp/web_search.py`: The newly added Crawl4AI integration lacked timeouts, so heavy pages (like Cloudflare or lazy-loaded docs) returned empty markdown. Fixed: Added `CrawlerRunConfig(page_timeout=15000, remove_overlay_elements=True, word_count_threshold=10)` to wait for rendering and filter out empty fetches.

---

## Self-Healing & Self-Learning Fixes

- **Rule (a)**: advice

- **Rule (code_analyzer)**: ask for clarification

- **Rule (test)**: Always verify collection names before search.
- **Reflection distiller**: `reflection_loop.py` `_distill()` now calls the sanctioned cloud **DeepSeek V4 flash** (`openrouter/deepseek/deepseek-chat`, `max_tokens=600`, 90s timeout) first, with local `qwen3.5-4b` fallback (`/no_think` system lead + `max_tokens=2048`, 900s timeout). Local qwen3.5-4b burns all `max_tokens` on `reasoning_content` for the long distiller prompt (empty `content`, finish=length at ~5 tok/s); DeepSeek emits the structured `<reflection>` directly. Verified live: distill → Qdrant `ReflexionMemory` store → `check_for_past_mistakes` retrieval → `[PAST-MISTAKE WARNING]` injection.
- **qdrant-client ≥1.18 migration**: `AsyncQdrantClient.search()` was removed. `reflection_loop.py` (`query_points`) and `tool_registry.py` (`query_points`) now use `query_points()` with `getattr(response, "points", response)`; `tests/test_tool_registry.py` updated to mock `query_points.return_value = SimpleNamespace(points=[...])`.

### Auth header cleanup (Invalid API Key warnings)
- `memory_bridge.py`, `token_tracker.py`, `picker.py`, `_commands_ai.py`, `ops/health/system_health.py`: Added `Authorization: Bearer llama` headers to requests hitting ports 8080-8083.
- `recovery_engine.py`, `genetic_mutation_loop.py`, `reflection_loop.py`, `offline_learner.py`: Added `api_key="llama"` + `custom_llm_provider="openai"` to litellm calls.
- `organism_console/cli.py`: Added `load_dotenv(override=True)` so CLI commands automatically read API keys (e.g. `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_BASE_URL`) from `.env`.
- `start-dev.ps1`: loads all API keys from `.env` (gitignored) and warns on the console when a cloud key is missing — cloud API keys are intentionally NOT hardcoded in the script (previously they were inlined as fallback defaults, which leaked live secrets into the repo; they've been removed).

### Cloud Model Policy (Free Models + DeepSeek V4 Flash Only; No Claude/Anthropic Allowed)
- `runtime_v2/services/_llm_client.py` & `swarm_os/services/llm_client.py`: Installed a hard interception safeguard against expensive models (`claude`, `anthropic`, `sonnet`, `opus`, `gpt-4`). Any request targeting an Anthropic/Claude model is automatically intercepted and redirected to **DeepSeek V4 Flash** (`openrouter/deepseek/deepseek-chat` / `deepseek/deepseek-chat`).
- `runtime_v2/services/fallback_manager.py`: Configured cloud fallback models so that ALL free models (`pricing.prompt == "0"` or `:free` in model ID on OpenRouter, plus Groq free tier, Nvidia NIM free tier, Gemini free tier, and DeepSeek free models) are fetched and included in fallback chains, while explicitly filtering out any Claude or Anthropic models.
- `organism_console/_commands_system.py`: `/cloud on` sets `os.environ["CLOUD_MODEL_ALLOWLIST"] = "free"` and confirms that all free cloud models are enabled.

### Healing pipeline
- `recovery_engine.py`: `DangerRoom` is async-only — changed `with` → `async with` + `await scan_sandbox()`. `restart_backend` no longer self-kills (skips own PID, uses `sys.executable`). `restart_llamacpp` uses absolute path. `micro_restart` awaits coroutine actions.
- `agent_service_v2.py`: LLM decision errors now feed the circuit breaker instead of aborting the run. Oscillating failures decay (not hard-reset) so they still reach healing. Loop-triggered healing honors `healing_attempts < 1` cap.
- `agent_service_v2.py`: Per-call `_CallState` dataclass replaces shared instance attrs — concurrent runs no longer clobber each other's `_handler_status`/`_premature_finals`/`_tool_*`.
- `healing_loop.py`: Escalation branch fixed (`< 1` after `+= 1` was always False). Now warns once, heals on repeat.
- `healing_service.py`: Real heal counters replace fabricated `last_heal_success=True` / `heals_today=0`.
- `failure_detector.py`: `run_coro_sync` uses daemon thread; no leaked loop on timeout. `admin.py` uses `check_sync()` (was calling nonexistent `.status()`).
- `autonomous.py`: Governor mode strings aligned (`auto_execute`/`sandbox_first`/`approval_required`/`reject`) — recovery actually executes now.

### Repair engine constitutional guards (defense-in-depth, enforced in code)
`organism_console/core/repair_engine.py` (+ `self_repair_engine.py`), per OWASP AI-Agent / SafeAgent / Zeltrex best practice — the LLM is treated as untrusted:
- **Path allowlist/blocklist**: auto-repair only touches `.py` files inside `src/`, `swarm_os/`, `runtime_v2/`, `organism_console/`. Blocked: `tests/`, `config/`, `.env`, `models/`, `docs/`, `data/`, `logs/`, build/pipeline files, `AGENTS.md`, package manifests, and the healing knowledge base. `_is_repairable_path()`.
- **Anti-truncation guard**: LLM whole-file rewrites that shrink the file >20% are rejected and reverted (`_anti_truncation_ok`, Zeltrex L4).
- **Test-run-before-accept**: `_snapshot_and_validate` reverts unless the repaired module's own tests pass (`_run_related_tests`, filename + import-content matching, 90s cap). Fail-closed.
- **Circuit breaker (R17-R19)**: daily repair cap (`MAX_DAILY_REPAIRS`, default 50) + 3 consecutive failures → 4h pause (`repair_breaker.json`, `_circuit_allows_repair`/`_record_repair_result`). Fail-closed on breaker-open/cap-hit.
- **Cure retirement**: matching cures that fail decay confidence (`failure_count`); below 0.25 they are removed from the knowledge base instead of accumulating forever (`_maybe_retire_cure`, `record_feedback`).
- Guard tests: `tests/test_repair_guards.py`.

### Closed healing/learning loops (fully autonomous)
- `healing_loop.py`: Added `HealingLoop.finalize(decision, result)` — feeds the real recovery outcome back through `Governor.finalize()` so the learner records SUCCESS/FAILURE and strategy stats update. Previously `Governor.finalize()` had **no callers** (dead outcome-learning loop).
- `organism_console/core/healing_watchman.py` (new): Background daemon thread that ticks `HealingLoop` every 60s and auto-recovers infra components in governor-approved modes, then `finalize()`s the incident. Auto-started in `cli.py` REPL mode (single-command mode excluded) + `atexit` stop.
- `loops/autonomous.py`: `/goal` loop now calls `_healing_loop.finalize(decision, result)` after each `RecoveryEngine.recover()`.
- `swarm_os/app/main.py`: Added a `ReflectionDaemon` background task (10-min interval) calling `run_reflection()` — ASPO rule distillation now runs automatically on the server instead of only via `run_5/run_8_healing_agents.py` scripts.
- `_commands_ai.py` `/upgrade`: After the autonomous goal loop, runs `SelfImprovementAgent().analyze_and_upgrade()` + `execute_upgrade()` (skill-memory generalization/forgetting) — was previously dead code with no callers.
- `repair_engine.py` `RepairWatchman`: `start(start_at_end=True)` skips the pre-existing history and only repairs NEW `tool_result` failures (avoids an LLM-repair burst on CLI launch). Auto-started in `cli.py` REPL alongside the infra watchman — code-level tiered repair (T0/T1/T2) now runs automatically.
- `_commands_ai.py` `/heal run`: Fixed `NameError` — `anomalies` was only defined when `/healing/evaluate` was reachable, so an offline backend crashed the fallback path. Now initialized to `0` before the probe.
- `runtime_v2/services/stream_runner.py`: The tool-decision memory step now also queries `ReflexionMemory` (`check_for_past_mistakes`) and injects a `[PAST-MISTAKE WARNING]` hint into the system prompt — distilled ASPO rules now **steer the running agent** (previously the collection was write-only; only legacy `brain.py` read it).
- `swarm_os/healing/governor.py`: `decide()` now **reads back `strategy_stats` win-rates** (≥5 samples): win-rate ≥80% lowers the auto-execute bar (0.6/0.3), win-rate <40% forces `approval_required` regardless of diagnosis confidence. Previously `finalize()` wrote stats nobody consulted. `strategy_win_rate` exposed on the decision; registry injectable via `strategy_registry=`.

### Learning pipeline
- `offline_learner.py`: Added missing litellm config so rule extraction actually calls the LLM.
- `memory_core.py`: KG read-modify-write guarded with `threading.Lock`. MoE shard routing always includes `general` shard.
- `evolving_critic.py`: Journal future retained; `score()` returns `{score, weights}` instead of bare float.

### Closed reflexion-learning loop (tool + decision failures → [PAST-MISTAKE WARNING])
Research-grounded (Reflexion NeurIPS'23 episodic verbal feedback; AgentHER arxiv 2603.21357 hindsight relabeling; Self-Healing Framework arxiv 2605.06737 failure taxonomy; ReMe ACL'26 dynamic procedural memory). Gap found: tool failures only went to episodic `remember_fact`, but the decision loop's `[PAST-MISTAKE WARNING]` reads only `ReflexionMemory` — so lessons never steered future runs.
- `runtime_v2/api/agent_service_v2.py`: new `_remember_failure()` + `_failure_lesson()` static helper. Failed tool calls (`_handle_tool`, line ~287) now persist BOTH episodic memory AND a structured `store_reflexion` rule (correction + `do_not_repeat`, confidence 0.75, component=agent_id). `_failure_lesson` emits grounded, non-LLM corrections for `filesystem`/File-not-found ("list the parent dir first"), `web_search`/timeout, and generic contract checks. Task text is embedded as `agent:{agent_id} analyzing auditing codebase {action} failed {error}` so it matches future `agent:{agent_id} {user_message}` queries. Dedup: identical (agent, action, error) suppressed for 5 min via `_failure_lessons_seen`.
- `runtime_v2/services/stream_runner.py`: new `_store_decision_reflexion()` helper; the three decision-failure sites (empty response, malformed JSON, timeout/final error) now write ReflexionMemory too, not just episodic `remember_fact`.
- `swarm_os/services/reflection_loop.py`: `store_reflexion()` gained a `do_not_repeat` payload field (retrieval already read it).
- Tests: `tests/test_failure_lessons.py` (4 tests — lesson content, generic fallback, reflexion store, dedup).

### HIGH — 17 silent state/telemetry-loss `except Exception: pass` blocks now log warnings
`swarm_os/healing/governor.py` (4 blocks: failure-record persistence, finalize learner update, strategy stats update), `swarm_os/healing/healing_loop.py` (governor finalize), `runtime_v2/api/agent_service_v2.py` (3 blocks: router success, remember_fact, remember_failure), `runtime_v2/services/stream_runner.py` (4 blocks: record_model_success/failure, record_analysis_outcome), `swarm_os/healing/learner.py` (timeline cache persist), `swarm_os/healing/strategy_registry.py` (win-rate persist), `swarm_os/adaptation/healing/healing_engine.py` (state persist), `swarm_os/adaptation/observability/healing_metrics.py` (2 blocks: metrics persist), `swarm_os/app/services/learning_service.py` (outcomes persist), `swarm_os/kernel/swarm_kernel.py` (organism step failure), `swarm_os/api/routes.py` (2 blocks: /status and /tools/cache Qdrant counts), `organism_console/core/repair_engine.py` (circuit-breaker persist + JSONL parse infinite-loop fix): All previously bare `except Exception: pass` blocks that silently discarded state/telemetry now log at `log.warning` or `log.debug` level so degradation is traceable in logs.

### HIGH — RepairWatchman infinite loop on corrupt `events.jsonl` line
`organism_console/core/repair_engine.py:775`: A malformed JSON line in `events.jsonl` caused `_last_position` to never advance, re-reading the same bad line endlessly. Fixed by incrementing `_last_position` past the bad byte when JSON parsing fails, plus a `log.warning`.

### CRITICAL — Test suite hangs from real `npx` MCP subprocesses during TestClient lifespan
`tests/conftest.py`: `TestClient(app)` triggers FastAPI lifespan which calls `get_mcp_manager()` spawning real `npx` subprocesses (SQLite MCP, memory, context7) that bypass `subprocess.Popen` mocks (the MCP SDK uses `anyio.create_subprocess_exec`). `session.initialize()` had no timeout — a cold npm cache or non-responding MCP server caused indefinite hangs. Added `global_mcp_manager_mock` autouse fixture that patches `get_mcp_manager` to return a mock, bypassing real npx spawns entirely.

### MEDIUM — `UnboundLocalError` in `tool_executor.py` `system`/`screen` handlers
`runtime_v2/services/tool_executor.py`: Three inner `import asyncio` statements inside the `run()` function made `asyncio` a local variable, so the `system` and `screen` handler blocks (which used `asyncio.timeout()` but had no local import) raised `UnboundLocalError`. Removed the redundant inner imports; the top-level `import asyncio` is sufficient.

### LOW — Remaining `asyncio.wait_for` in test file
`tests/test_lsp_tool.py:82`: `await asyncio.wait_for(first.process.wait(), timeout=5.0)` → `async with asyncio.timeout(5.0): await first.process.wait()`. This was the 22nd and final `asyncio.wait_for` call in the codebase; all others were already migrated in the prior round.

### opencode-parity behaviors (agents navigate/verify like a human maintainer)
Goal: the analysis/coder agents should behave like a senior engineer (or opencode) — never guess paths, always read before writing, keep a working checklist, and verify after editing. `tests/test_opencode_parity.py` (9 tests) covers all of it.
- **Project map injection** (`runtime_v2/services/project_map.py`, new): reads `AGENTS.md` and distills the Architecture Overview + Module Map tables (runtime_v2/swarm_os sections sorted first) into a ~6KB `[PROJECT MAP]` block, injected into the system prompt for `code_analyzer`, `researcher`, `coder`, `debugger`, `reviewer` (not the tiny coordinator). Agents always know the real module layout instead of hallucinating paths. Parsing is failure-tolerant (empty string on error).
- **Deterministic discovery — `glob` op** (`swarm_os/lib/mcp/filesystem.py`): new `operation=glob` (`path` + `pattern` like `**/*.py`), fnmatch over recursive walk with banned-dir/ext/size filters, capped at 200 matches, returns root-relative paths. Agents find real files via glob/list instead of guessing. Grep already existed.
- **Read-before-write guard** (`runtime_v2/services/tool_executor.py`): explored-path tracking (`_explored_paths`). Successful `list`/`read`/`grep`/`glob` mark paths (or their parents) as explored; a `patch` on an existing file that was never seen is blocked with a corrective error ("call read/list first"). New-file `write` stays allowed.
- **Todo tracking** (`runtime_v2/api/agent_service_v2.py`): new `action=todo` (`operation: add|done|list`, `items`, `item_id`) maintained in `_CallState` and re-injected into the decision context every turn (survives message compaction), so the agent keeps a visible working checklist instead of a flat 8-turn loop.
- **Verify-after-change** (`agent_service_v2.py`): after a successful `write`/`patch` on a code file (`*.py/.js/.ts/.tsx/.jsx/.go/.rs`), `state.pending_verify` is set and `action=final` is rejected once ("run sandbox_repl first") until a `sandbox_repl` succeeds. No more premature SUCCESS on un-tested edits.
- **Warmup rewrite** (`runtime_v2/api/_agent_routing.py`): `code_analyzer` now deterministically 1) reads `AGENTS.md`, 2) globs `runtime_v2/**/*.py`, 3-4) reads the two key files — grounding before the LLM ever decides.

### MCP tooling (open-code parity: docs + deep web read)
- **Context7 MCP added** (`swarm_config.json`): `npx @upstash/context7-mcp` — up-to-date library docs (`resolve-library-id`, `query-docs`) for `researcher`/`coder`/`debugger`/`reviewer`/`code_analyzer` via `action=mcp`. Verified: 16 total tools across `sqlite`(5) + `memory`(8) + `context7`(2) load cleanly through `ExternalMCPClientManager`.
- **Fixed `mcp_client.py` for MCP SDK 2.0**: the SDK renamed `Tool.inputSchema` → `Tool.input_schema` (2026-07-28 spec), breaking every external MCP tool with `'Tool' object has no attribute 'inputSchema'`. Now `getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)`.
- **`web_fetch` native tool** (`swarm_os/lib/mcp/web_search.py::web_fetch_handler` + `tool_executor.py`): deep-read a single URL (strip HTML/JS/CSS → readable text, `max_chars` cap, browser UA) — the swarm analogue of an opencode WebFetch, which search snippets don't provide. Added to `researcher` + `code_analyzer` tool lists.
- **Not installed**: `@cyanheads/git-mcp-server` (dumps JSON logs to stdout, corrupts stdio MCP) and official Python `mcp-server-git`/`mcp-server-fetch` (use removed `Server.list_tools` API, incompatible with installed MCP SDK 2.0.0). Git is already covered by CLI commands + `sandbox_repl`; deep fetch now native. Revisit only if MCP SDK is downgraded.

### Whole-computer command center (read-only system analysis)
- **`system` tool** (`runtime_v2/services/system_intel.py`, new; wired in `tool_executor.py`; `action=system` definition + tool list in `system_prompts.py`): the swarm's whole-machine analysis capability — READ-ONLY, no destructive ops. Sub-actions: `system_inventory` (hostname/OS/CPU/RAM/swap/disks/network interfaces via psutil), `process_list` (sort=cpu|memory|name|pid, top=N), `service_list` (Windows services), `net_connections` (TCP/UDP sockets + owning process), `disk_analyzer` (path, max_depth, top — largest dirs/files via pathlib walk, banned dirs), `installed_apps` (registry Uninstall hives, both 64/32-bit), `startup_items` (Run/RunOnce keys), `registry_query` (read-only, restricted to SOFTWARE), `event_log_query` (Windows Event Log tail via pywin32, optional level filter). Runs through `asyncio.to_thread` (blocking psutil/winreg). Coordinator routes "analyze computer/system/hardware/processes" → `code_analyzer`; the tool is offered to `code_analyzer`, `researcher`, `debugger` (not coordinator). 9 tests in `tests/test_system_intel.py`.

### Screen control (computer-use tier, human-control gated)
- **`screen` tool** (`swarm_os/lib/mcp/screen.py`, new; wired in `tool_executor.py`; `action=screen` definition in `system_prompts.py`): the Anthropic Computer Use loop native on Windows via win32 APIs (no pyautogui/mss deps). Sub-actions: `screenshot` (saves PNG to `logs/screenshots/`, returns path + dims + foreground window), `foreground_window`, `list_windows`, `cursor_position` (read-only — always allowed), and `mouse_move`/`left_click`/`right_click`/`double_click`/`scroll`/`type`/`key` (input — GATED). **Human-control mode is the DEFAULT**: input actions are blocked with a "propose first, wait for approval" result until `SWARM_SCREEN_AUTONOMOUS=1` or `set_screen_autonomous(True)`. Action cap (default 200, `SWARM_SCREEN_MAX_ACTIONS`) stops runaway loops; `reset_screen_action_count()` clears. Unicode typing via `SendInput`/`KEYEVENTF_UNICODE`; keys support combos (`ctrl+s`, `alt+tab`). 10 tests in `tests/test_screen_control.py`.

### Memory daemon
- `memory_bridge.py`: Removed duplicate consolidation daemon (watch_loop no longer spawns its own). Only main.py's explicit `start_manager_daemon` runs.

### BUG — Memories not updating (empty models/types in Qdrant)
`swarm_os/memory/memory_bridge.py` `_add()`: Agent events are written as `EventEnvelope` (data nested in `payload`), but `_add()` read `model`/`task_id`/`outcome` at top level only → every stored point had `models=[]`, `types=[]`. Now unwraps `payload`. Also widened `FLUSH_TRIGGERS` in `_memory_bridge_base.py` to include actual agent event types (`generation_completed`, `stream_completed`, `tool_result`, `agent_action`, etc.) — previously only 3 legacy trigger names matched, so sessions rarely flushed.

### Model migration complete — no more qwen2.5/qwen-tuned
Removed all stale model references (`qwen2.5:7b-instruct`, `qwen2.5:3b-instruct`, `qwen-tuned`, `qwen3-vl:8b`, `qwen3-embedding:8b`) → `qwen3.5-4b` / `moondream:latest` / `nomic-embed-text-v1.5` across:
- `orchestrator.py` (router profiles, fallback), `services/llm/client.py`, `capabilities/models.py`, `brain.py`
- Healing/learning: `recovery_engine.py`, `kernel/genetics.py`, `genetic_mutation_loop.py`, `shared_model_registry.py`, `fallback_router.py`
- Workers: `generation_worker.py`, `supervisor.py`
- Console: `_commands_ai.py`, `_commands_dev.py`, `_commands_system.py`, `_command_routing.py`, `core/embedding_client.py`, `voice_routing.py`, `write_cli.py`
- Zenith router, frontend (`AlertBanner.tsx`, `organismData.ts`), and all tests.

### Success-rate classification fixed (dashboard showed 0%)
`swarm_os/api/routes.py` `/router` stats: outcome was read ONLY from `learning_outcome.result`, which most events lack. Now checks `learning_outcome` + `payload.status` + `status` + `payload.outcome` + `outcome`, classifying `success/completed/ok/healthy` vs `fail/failed/error/unhealthy` vs `unknown`.

### Timeline buckets all showed 0 success
`swarm_os/api/routes.py` `/timeline`: Same one-field outcome bug — every bucket showed `success_count:0`. Now uses shared `_classify_event_outcome()` helper (also used by `/router`).

### Dashboard showed "Active Models: 11" with phantom models
`swarm_os/api/routes.py` `_safe_ollama_models()`: Was scanning `models/` dir for GGUF files AND reporting file-path ids like `.\models\foo.gguf`. Now only reports models **actually being served** on ports 8080-8083, normalizes file-path ids to clean names, and sorts with the generation model first.

### Memory store only grew one point per session (LLM-bound)
`swarm_os/memory/memory_bridge.py` `_flush()`: Whole session was collapsed into ONE summary point behind a serial `_summarize()` LLM call (~12 events → 1 point). Now stores **each event as its own embedded memory point** (via `_event_text()`) plus the session summary — the store can grow toward tens of thousands of memories without being LLM-bound.

---

## Frontend Audit Fixes (organism-console)

### CRITICAL fixes
- `lib/types.ts`: `TracesResponse.items` → `traces` (backend returns `{count, traces}`). `TraceSummaryResponse` → dict shape (backend returns `{count, window, status_counts, phase_counts, model_counts, latency_ms}`).
- `MemorySearchPage`: was querying `/traces` (raw trace events) for a timeline chart — now hits `/timeline?window_minutes=20000`.
- `AgentPage`: `/generate` returns `{content, model}` but the adapter read `response/answer/output/result` — added `content`.
- `WorkspacePage`: tool-cache was read as camelCase (`cacheSize`) — backend sends `cache_size`/`cached_keys`. Fixed to snake_case.
- `MemorySearchPanel`: backend sends `sender`, UI read `source` — added fallback mapping.
- `DebateRoomPanel`: `/features/debate` endpoint **did not exist** — added SSE endpoint in `api_features.py` streaming planner→reviewer→coordinator.
- `OmniDevInterface`: `/omnidev/run` endpoint **did not exist** — added in `api_features.py` (routes task through coordinator agent).
- `SwarmDashboard2027` SSE: stream emits `{event, id, timestamp, payload}` but handler read `data.type` — fixed to unwrap `event`/`payload`. Also `orchestrator.py` now emits `GENERATION_COMPLETED` to the event bus so the feed is live.
- `OpsPage`: `traceItems` now reads `.traces` (not `.items`); summary synthesized from raw traces.

### HIGH fixes
- `GenomesSection`: used constant `appConfig.backendBaseUrl` — now uses editable Topbar `backendUrl` from `useUiStore`.
- `upwork/engine.py` + `reasoning_layer.py`: stale `qwen3:14b` → `qwen3.5-4b`.
- `SwarmDashboard2027.tsx`: radar chart now reads real model distribution from `/router` + `/agents` (was `Math.random()` mock data), NDJSON stream parsing in `AgentConsole`, `res.ok` checks, RAF cleanup in `AnimatedNumber`, travel keyframes moved into `injectStyles` (removed `dangerouslySetInnerHTML`).
- `LearnedMemoriesPage.tsx`: stable memory key (`stable memory?.id ?? memory?.memory_id`).

---

## Online-Researched Upgrades (Round 2 — httpx pooling, task safety, Qdrant indexes)

### httpx pooled clients (no more per-call AsyncClient)
`fallback_manager.py`, `upwork/engine.py`, `upwork/reasoning_layer.py`, `web_search.py`: replaced per-request `async with httpx.AsyncClient(...)` with a module-level lazy singleton `_get_client()` using `httpx.Timeout(connect/read/write/pool)` tuples + `Limits(max_connections=100, max_keepalive_connections=20)`. Fixes wasted TLS/DNS handshakes + socket exhaustion under load. Also fixed `web_search.py` `verify=False` → default SSL verification (security).

### Fire-and-forget task safety in memory_bridge
`memory_bridge.py`: `_add()` spawned 3 `asyncio.create_task(graph_repo.*)` with no reference — tasks were GC-able mid-await, exceptions silently swallowed (silent memory data loss). Added `self._bg_tasks` set + `_spawn()` helper with strong reference + error-observer callback.

### Qdrant payload indexes
`vector_store.py`: added `category` (KEYWORD) + `timestamp` (FLOAT) payload indexes on top of existing `tasks`/`types`/`models`/`consolidated` — filtered memory queries avoid full payload scans as the store grows past 10k points.

### asyncio.timeout() sweep
`stream_runner.py`, `agent_service_v2.py`, `swarm_stream.py`: `asyncio.wait_for(...)` → `async with asyncio.timeout(...)` context manager (composable, cancels cleanly, raises builtin TimeoutError).

---

## Second-Pass Fixes (phantom 35B + vision model + control-plane registry)

### "Genomes: 35B" was a hardcoded fallback
`OrganismConstellation.tsx` (both consoles): `genomeType` fallback was `'qwen:35b'` → now `'qwen3.5-4b'`. Only shows when no model data exists.

### Vision showed the generation model
`swarm_os/api/routes.py` `/status`: `primary_vision_model` was `installed_models[0]` (qwen3.5-4b). Now filters for actual vision models (`vl`/`vision`/`moondream`/`llava`) → `moondream-latest`.

### Control-plane registry routed to a nonexistent server alias
`shared_model_registry.py`: profiles/role pool referenced a removed server alias and removed models (phi4-mini, qwen2.5-coder:7b, smallthinker:20b, qwen3:4b). Normalized all to `qwen3.5-4b`.

### "Generation 0" on genomes dashboard
`swarm_os/api/admin.py`: `_latest_snapshot_payload` called `build_status(None, None)` so `generation` was always None; `/generation` also omitted the field. Now derives generation from snapshot data + exposes it.

### CLI routing model heuristic simplified
`organism_console/_command_routing.py`: leftover `4b`/`llama3-groq`/`ministral` matching → prefer any `qwen3.5`/`qwen3` installed model.

---

## Online-Researched Upgrades (2025-26 SOTA patterns)

### PS/MV failure classification (Diagnostician)
`swarm_os/healing/diagnostician.py`: Every hypothesis now carries `fix_class` = `prompt_sensitivity` (fixable via rule/script changes → sandbox repair) vs `model_variability` (model limitation → escalate to cloud/human). Routes governor recovery paths by failure type instead of confidence guessing. Added format_violation + delegation_loop hypotheses.

### Structured + ranked + decayed reflection memory
`swarm_os/services/reflection_loop.py`: Distiller now uses a structured template (`failure_summary`/`root_cause`/`next_attempt_rules`/`do_not_repeat`). Rules stored with `component`, `timestamp`, `confidence` metadata. Retrieval uses ranked top-k with recency decay + confidence weighting (not single 0.85-threshold hit). Model alias → `qwen3.5-4b`.

### Outcome-driven model cooldowns (fallback_manager)
`runtime_v2/services/fallback_manager.py`: Added `record_model_failure()` / `record_model_success()` with exponential backoff (30s→600s). `get_live_fallbacks()` filters out cooled-down models BEFORE the LLM call. `stream_runner.py` wires failures → cooldown on timeout/error, success → clears cooldown. A failing local/cloud model is skipped on the next call instead of retried blindly.

### Silent-degradation probes (failure_detector)
`swarm_os/healing/failure_detector.py`: `check_context_utilization()` flags context >85% (before truncation). `check_retry_rate()` flags models stuck in cooldown. Both feed the `check()` health score alongside the connectivity probes — catches "model started hallucinating" type degradation that never returns a 5xx.

### Isolated recovery-script execution
`swarm_os/healing/recovery_engine.py`: LLM-generated recovery scripts now run via `python -I` (isolated mode, ignores site-packages/user site) in the DangerRoom sandbox with `PYTHONNOUSERSITE=1` — no network inheritance beyond what the process needs, plus the existing AST scan + traversal guard.

---

## Codebase Audit Fixes (Round 3 — task safety, races, httpx lifecycle)

### CRITICAL — Fire-and-forget asyncio tasks GC-able / exceptions swallowed
`src/core/agent_runtime.py`, `src/agent_memory/episodic_store.py`, `src/agent_memory/hybrid_memory.py`: background tasks (`_delayed_task_removal`, `_load()`, `_persist()`, `_persist_loop()`) were spawned with no strong reference — GC could reap them mid-await and exceptions were silently dropped. Added `_bg_tasks: Set[asyncio.Task]` + `add_done_callback(_bg_tasks.discard)` to each; `HybridMemory.close()` now cancels bg tasks. Also `get_event_loop()` → `get_running_loop()` in `agent_runtime.py`.

### MEDIUM — Cooldown race in fallback_manager
`runtime_v2/services/fallback_manager.py`: `_cooldowns` read-modify-write had no lock (TOCTOU — two concurrent failures could both see "not cooled down" and double-count). Added module-level `threading.Lock()` + `_cooldowns_lock_sync()`; `_is_cooldown_active()` now locked.

### MEDIUM — 6 bare `except:` in picker
`organism_console/ui/picker.py`: bare `except:` catches `KeyboardInterrupt`/`SystemExit`/`GeneratorExit`. All 6 → `except Exception:`.

### MEDIUM — None deref in recovery_engine
`swarm_os/healing/recovery_engine.py`: `actions` could be None → `for action in actions` crashed. Now `actions: Optional[dict]` with `if not isinstance(result, dict): result = {}` guard.

### httpx client lifecycle (no leaked TLS/DNS)
- `swarm_os/infra/llama_client.py`: added `async aclose()` closing the pooled `httpx.AsyncClient`.
- `swarm_os/services/embedding_service.py`: added `async aclose()`.
- `swarm_os/services/llm_client.py` + `swarm_os/core/orchestrator.py`: added module-level `close_global_client()` for their lazy httpx singleton.
- `swarm_os/memory/memory_bridge.py`: `MemoryBridge.close()` now closes the embedding client.
- `swarm_os/app/main.py` lifespan: shuts down orchestrator + llm_client + llm + bridge `aclose()` on exit.

### Subprocess safety in recovery_engine
`restart_llamacpp`/`restart_backend` now spawn with `start_new_session=True`, `stdout=DEVNULL`/`stderr=DEVNULL`, and early-exit detection via `proc.wait(timeout=2.0)` — no orphaned child processes or console handles leaking into the API server.

### SSL + logging hygiene
- `fetch_commit.py`: `verify=False` → `verify=True` (default TLS verification).
- `swarm_os/services/genetic_mutation_loop.py`: `logging.basicConfig` now guarded by `if logging.getLogger().handlers == []` (no duplicate handlers on import).
- `organism_console/core/repair_engine.py`: RepairWatchman iteration failure now `log.warning` instead of silent.

### Verified already-correct (no change)
`@app.on_event` not used (lifespan already in place); `orchestrator._fetch_installed_models` already guarded; `routes._safe_ollama_models` already safe; `_commands_system.py` CLI degraded-value fallbacks acceptable.

---

## organism-console Dashboard Fixes (mirrored from start-console)

`organism-console/src/components/organism/SwarmDashboard2027.tsx`:
- RadarChart fed **real model distribution** from `/router` + `/agents` (was `Math.random()` mock data) — maps agent→model→share count on a 10s poll.
- AgentConsole `handleSend` now **parses the NDJSON stream** (`content`/`output`/`text`, agent/model, per-type colors) instead of discarding the body.
- `res.ok` checks on status/tools/timeline/agents fetches; `catch` blocks log errors (were silent).
- `AnimatedNumber` RAF handle now cancelled on unmount; travel keyframes moved into `injectStyles` (removed `dangerouslySetInnerHTML`).

`organism-console/src/pages/LearnedMemoriesPage.tsx`: card `key={idx}` → `memory?.id ?? memory?.memory_id ?? \`memory-${idx}\``.

`organism-console/src/pages/OpsPage.tsx`: brought to parity with the start-console tutor page (was a degraded copy).
- Removed dead trace/admin queries + imports + state (`/readyz`, `/status`, `/tools`, `/traces`, `/traces/summary`, `/admin/*` were fetched every 15-60s but never rendered) and the unused `appConfig` import.
- Restored the missing **"Scary situations"** and **"Basic computer help"** automation groups (were imported but never rendered) and the missing lesson sections (Words to know, Before you start, What success looks like, When to ask for help, Common mistakes) via `AutomationGroup`/`LessonSection`/`LessonCard` helpers.
- Hardened `renderUpworkResult` (`items`/`bullets`/`missing` → `|| []`) in **both** consoles — missing array fields previously crashed the page on partial backend responses.

---

## start-console Migration to Current-Gen Stack (was never committed/built)

`start-console/` is an untracked, never-built parallel console (TanStack Start SSR + React 19) whose deps had drifted: R3F v8 (React 18 pairing) on React 19, `ai` v7 (user-level, undeclared) vs code written for ai v3, removed `createAPIFileRoute`. Research-driven reconciliation — **kept the forward migration** (per R3F docs: v8↔React18, v9↔React19):

### Dependency fixes (`start-console/package.json`)
- Added missing declared deps: `ai@^7.0.44`, `@ai-sdk/react@^4.0.47`, `@ai-sdk/openai@^4.0.25`, `zod@^4`, `framer-motion@^12` (previously resolving from user-level `C:\Users\rober\node_modules`).
- Upgraded `@react-three/fiber` ^8.18 → **^9.5** (React 19 line) + `@react-three/drei` ^9.122 → **^10**; three ^0.160 kept. Fixes all `JSX.IntrinsicElements` errors (v9 uses `ThreeElements`).

### AI SDK v7 API migration
- `src/routes/api/chat.ts`: `createAPIFileRoute` (removed from TanStack Start) → `createFileRoute` + `server.handlers.POST` (needs `import type {} from '@tanstack/start-client-core'` to load the `server` option augmentation). `convertToCoreMessages` → `await convertToModelMessages`. `tool.parameters` → `inputSchema`. `toDataStreamResponse` → `createUIMessageStreamResponse` + `toUIMessageStream`. `system` → `instructions`.
- `src/pages/AgentPage.tsx`: `useChat` from `@ai-sdk/react` v4 — `input`/`handleInputChange`/`handleSubmit`/`isLoading` removed from the API → local `useState` input + `sendMessage({ text })` + `status === 'streaming'`; endpoint via `DefaultChatTransport({ api: '/api/chat' })` from `ai`. `messages[].content`/`toolInvocations` → **`messages[].parts`** (text parts + tool parts carrying `input`/`output`/`state`).

### Type/route fixes
- `SwarmTopology3D.tsx`: R3F v9 `instancedMesh args` `[null, null, n]` → `[undefined, undefined, n]`; `bufferAttribute` uses `args={[array, itemSize]}` (v9 requires constructor args).
- `__root.tsx`: type-only `ReactNode` import (verbatimModuleSyntax); `children?` optional for `shellComponent` type.
- `OrganismConstellation.tsx`: `useRef<any>(null)` (React 19 requires arg), removed dead `globalScale` param + unused `React`.
- Removed unused `React` imports (`GenomesSection`, `OrganismCockpit`, `organism-hooks.test`, `OpsPage`).
- `OpsPage.tsx`: pruned ~120 lines of dead trace/admin queries + helper fns (tutor page no longer renders them).
- `lib/types.ts`: added `llamacpp_reachable?: boolean` to `StatusResponse` (backend `/status` sends it); added `"memories"` to `PanelKey`.
- `ShellLayout.tsx`: `react-router-dom` → `@tanstack/react-router` (`Outlet`/`useLocation`).
- Regenerated `routeTree.gen.ts` (`npm run generate-routes`) to register `/memories` + `/api/chat`.

Verification: `organism-console` tsc clean, `start-console` tsc clean + `npm run build` succeeds, full `pytest` suite 240 passed / 2 skipped.

---

## 2026 SOTA Upgrades (implemented + roadmap)

Applied from the 2025-26 online research pass on semantic caching, prompt-reuse, online routing, and reflection budgets. Each implemented item is **off by default** (env-gated) so nothing changes until enabled.

### IMPLEMENTED — Semantic decision cache (hybrid exact→Qdrant) for the tool-decision loop
`runtime_v2/services/_semantic_decision_cache.py` (new): on `get_tool_decision()` — (1) exact SHA-256 of the last user message hit-tests an in-process LRU (zero false positives), (2) on miss, embeds the query (nomic :8081) and searches a `decision_cache` collection, returning the stored decision only above `SWARM_SEMANTIC_CACHE_THRESHOLD` (default `0.85`), (3) writes results back so near-duplicate decisions short-circuit.
- **Off by default**: enable with `SWARM_SEMANTIC_CACHE=1`. Rationale: the earlier pure-exact in-dict cache was deliberately disabled, so the semantic layer is opt-in + never raises/blocks (failures degrade to a miss).
- Metrics via `decision_cache_stats()` (`hits`/`semantic_hits`/`misses`/`errors`/`lookups`).
- Wired into `runtime_v2/services/stream_runner.py` `get_tool_decision()` (lookup at top, write-back on success).

### IMPLEMENTED — Online win-rate routing for analysis agents (consistency-aware cloud)
`runtime_v2/services/online_routing.py` (new) + `runtime_v2/services/_llm_client.py`: analysis agents (`code_analyzer`/`reviewer`/`researcher`) were hard-pushed to cloud whenever a key existed. Now the cloud hop is **win-rate gated**: per-agent success/failure persisted to `_agent_winrates.json`; the cloud hop only stays while win-rate ≥ `SWARM_WINRATE_FLOOR` (0.5) with ≥ `SWARM_WINRATE_MIN_SAMPLES` (5) samples. Low win-rate decays analysis back to local qwen3.5-4b. Closed-loop via `record_analysis_outcome(agent_id, ok)` called in `stream_runner` (success + the final failure path).
- **Off by default**: `SWARM_WINRATE_ROUTING=1` to enable. Defaults to the legacy behavior when disabled.

### IMPLEMENTED — llama.cpp KV prompt-prefix reuse (`--cache-reuse 256`)
`start_llama.bat`, `start-dev.ps1`, `start-dev-fixed.ps1`: added `--cache-reuse 256` to the generation server (`:8080`) so the stable system prompt + tool schema (`[PROJECT MAP]`, `[RELEVANT MEMORIES]`, tool schema) is KV-reused across the repeated `get_tool_decision()` calls — cutting prefill TTFT on the single-slot backend.

### IMPLEMENTED — Reflection lesson token-budget cap (defense-in-depth)
`swarm_os/services/reflection_loop.py` `check_for_past_mistakes(..., max_chars=700)`: the injected `[PAST-MISTAKE WARNING]` hint is now hard-capped so distilled lessons never eat the decision-context budget (lesson budget ≈ 10-20% of context per 2026 guidance). Retrieval already used recency+confidence decay and top-k ranking.

### BUG — Distiller burned ~300s on the single llama slot after OpenRouter 402 (credit exhaustion)
`swarm_os/services/reflection_loop.py` `_distill()`: when OpenRouter returned a 402 (out of credits — a non-transient error), the distiller fell through to the local qwen3.5-4b fallback, which generated 2048 tokens at ~5 t/s (~300s) on the single llama slot and returned empty content anyway (qwen spends all tokens on `reasoning_content`). For 5 of every 10 minutes the generation slot was occupied by this no-op burn, blocking every LLM-dependent endpoint. Now detects 402/"credits" in the exception message and **skips the local fallback** so the slot stays free for real work.

### DEFERRED — further research items (documented, not yet built)
- **Reasoning-aware memory reranker**: swap the generic BGE reranker (`:8082`) for Qwen3-Reranker/MemReranker line to get calibrated relevance scores usable as thresholds. `runtime_v2/services/memory_core.py` `rerank_memories()`.
- **Selective/failure-class-gated reflection**: trigger deep LLM distillation only for diagnosable failure classes (already tagged via `fix_class` in `diagnostician.py`) — reflection measurably regresses already-good paths. Gate in `reflection_loop.py` `_distill()`.
- **MCP batch/parallel dispatch**: dependency-aware `asyncio.gather` over independent tools + merged `batch_execute` (BatchIt pattern) in `stream_runner.py`/`tool_executor.py`. Tool schemas are already cached in-process via `ExternalMCPClientManager.cached_tools` (now thread-safe).
- **Durable checkpointed turns**: Pydantic AI v2 "capability" decomposition + step-level checkpoints so an interrupted multi-delegation `step_agent_stream` resumes at the last completed step instead of replaying from the top.

---

## Speculative Decoding & Local-Model Tuning (2026-08)

Research-backed (llama.cpp `docs/speculative.md`; ggml-org/llama.cpp#15307 OpenVINO backend; PR #20700 Qwen3.5 MTP; unsloth MTP docs; ggml-org/llama.cpp#10594/#10664 draft-on-quant regressions). Bottom line: **`ngram-mod` is the winning speed lever on this hardware (21.4 t/s dec, free); MTP is second (~12 t/s, prefill-taxed); the 0.8B draft is a regression; the Intel NPU is real now but not worth it for a 4B target.**

### IMPLEMENTED - MTP speculative decoding (Qwen3.5 built-in heads), ~2.0x on the 4B
- Qwen3.5 ships MTP (`nextn`) heads ("MTP: trained with multi-steps"). The plain `C:\Users\rober\models\Qwen3.5-4B-Q4_K_M.gguf` GGUF contains NO MTP tensors (GGUF scan: no `nextn`); the unsloth MTP GGUFs do (`blk.{N}.nextn.{eh_proj,enorm,hnorm,shared_head_norm}`).
- New files: `C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf` (2.79 GiB, 441 tensors, MTP) and `C:\Users\rober\models\Qwen3.5-0.8B.Q4_K_M.gguf` (0.50 GiB, 335 tensors, MTP). Both share the Qwen3.5 family vocab - verified 16/16 identical token IDs across 0.8B/4B via `llama-tokenize`.
- Measured (prod flags `-t 2 -tb 4 -ngl 99`, isolated): 4B-MTP + `--spec-type draft-mtp,ngram-simple --spec-draft-n-max 3` -> **12.98 t/s vs 6.36 plain 4B (~2.04x)**, `draft_n_accepted: 62/94 = 66%`.
- This llama.cpp build (c0bc8591e) already ships `draft-mtp` (MTP merged upstream 2026-05-16).
- Caveat: Qwen3.5 is DeltaNet-hybrid; PR #20700 notes recurrent-state checkpoint/restore ("two-phase decode") can mute MTP gains - so ngram stays as the fallback and the two are stacked (`draft-mtp,ngram-simple`).

### IMPLEMENTED - Start-script wiring (`start_llama.bat`, `start-dev.ps1`, `start-dev-fixed.ps1`)
- `SWARM_SPEC_DECODE=1` master gate (**default ON now**; `0` to disable). `SWARM_SPEC_TYPE` selects the implementation (default `ngram-mod`):
  - `ngram-mod` (DEFAULT) -> adds `--spec-ngram-mod-n-match 24 --spec-ngram-mod-n-min 48 --spec-ngram-mod-n-max 64`
  - `ngram-simple` -> fallback (`--spec-ngram-simple-size-n 4 --spec-ngram-simple-size-m 16 --spec-ngram-simple-min-hits 1`)
  - `draft-mtp,ngram-simple` -> adds `--spec-draft-n-max 3`
  - `draft-simple,...` + `SWARM_DRAFT_MODEL=<path>` -> in-process draft model (0.8B verified same vocab)
- **Default ON runtime speed gates** (set in the start scripts; `0` to disable):
  - `SWARM_GRAMMAR_DECODE=1` — GBNF grammar constrains local tool decisions (valid JSON first try, fewer retries).
  - `SWARM_SEMANTIC_CACHE=1` — near-duplicate tool decisions short-circuit the LLM via the decision cache.
- `--cache-reuse 1024` (was 256) on the generation server — wider prompt-prefix KV reuse for the repeated decision system prompt.
- `SWARM_LOCAL_MODEL=qwen3.5-4b-mtp` -> serves the MTP 4B (`-ngl 99`, alias `qwen3.5-4b`).
- **Default model is now the MTP 4B** (`SWARM_LOCAL_MODEL` unset). The plain 9B fallback was pruned 2026-08-05 (backup at `C:\Users\rober\AppData\Local\Temp\opencode\prune-backup-2026-08-05\`) — heavy reasoning already routes to cloud DeepSeek V4 Flash and local chat runs faster on the 4B-MTP, so only the 4B-MTP is served.
- The earlier 4B-as-draft finding (~1.1x, ineffective) stands; the MTP head supersedes it. Note: the npm/React frontend has NO effect on generation speed - it is a thin client; speed comes entirely from llama.cpp.
- **Rerank burst bounded**: `runtime_v2/services/memory_core.py` caps concurrent `/v1/rerank` calls with a `threading.BoundedSemaphore(2)` - analysis-agent launch fired dozens of concurrent rerank requests that saturated DDR5 (the root cause of the old 90/120s timeouts).

### A/B benchmark (2026-08, prod flags `-t 2 -tb 4 -ngl 99`, tool-decision workload) - ngram-mod wins
Head-to-head across all spec types on the 4B (gen = long paragraph, dec = tool-decision with large prefill + short JSON output, RUNS=2):
| Config | Dec t/s | Gen t/s | Prefill t/s | TTFB | Verdict |
|--------|---------|---------|-------------|------|---------|
| plain (no spec) | 5.98 | 5.93 | 23.7 | 2.72s | baseline |
| `draft-mtp` | 12.07 | 9.18 | 16.5 | 3.86s | ~2x but MTP prefill-tax (~0.70x) |
| **`ngram-mod`** | **21.38** | 8.99 | **36.4** | 3.31s | **best; free (no extra model)** |
- `draft-simple` + **0.8B draft = 2.48 t/s dec - a 2.4x REGRESSION** (draft model too slow despite 79% acceptance). The 0.8B earns no slot in the stack. The 0.8B MTP build (`Qwen3.5-0.8B.Q4_K_M.gguf`, 335 tensors) is kept only as a tokenizer/vocab reference; the redundant plain 0.8B (`Qwen3.5-0.8B-Q4_K_M.gguf`, 320 tensors, no nextn) was deleted.
- Chaining `draft-simple,draft-mtp` fails to boot: `GGML_ASSERT n_embd == llama_model_n_embd(ctx_tgt)` - the 0.8B draft's hidden width mismatches the MTP nextn width, so a cross-model MTP chain is architecturally impossible here.
- Rationale: `ngram-mod` matches long n-grams from the stable system prompt + tool schema, so the decision loop drafts ~56-token continuations at 88% acceptance with no prefill tax (36.4 t/s = best prefill of any config). MTP's 2.0x remains valid but its prefill penalty costs more on the short-output decision workload.

### RESEARCHED - Intel NPU (Core Ultra 5 135U / Meteor Lake, Intel AI Boost, device `VEN_8086&DEV_7D1D`)
- OpenVINO is now an official llama.cpp backend (ggml-org/llama.cpp#15307, upstream 2026-03) - GGUF on Intel CPU/iGPU/NPU via `GGML_OPENVINO_DEVICE`. Preview-quality: "Extensive accuracy validation, performance optimizations, and broader architecture coverage are work in progress" (llama.cpp `docs/backend/OPENVINO.md`).
- NOT adopted: requires a separate `-DGGML_OPENVINO=ON` build + oneAPI + NPU driver >= v2565; NPU is constrained (`-np 1`, "keep context small" ~1024, Q4_0-primary, no caching); cross-backend in-process draft is unproven. MTP delivered the speedup with zero extra toolchain. Revisit only for the 9B+ as a whole-model NPU target.
- **RE-EVALUATED 2026-08-03 (after NPU driver updated to 32.0.100.4778 / 2026-04-27, far above v2565): STILL NOT VIABLE for the 9B — verdict is a hard NO on two independent blockers, neither driver-related:**
  1. **Architecture: Qwen3.5 does not load on the OpenVINO backend at all.** It is DeltaNet-hybrid, emitting `SSM_CONV` ops (`ssm_conv1d.weight`, `cache_r_l0`) that the backend does not support — fails with `pre-allocated tensor ... in a buffer (OPENVINO0) that cannot run the operation (CPY)` (ggml-org/llama.cpp#20562, still open). The backend covers only dense archs (Llama 3.x, Qwen2.5, Qwen3 dense, Gemma, Phi, Mistral, Hunyuan, MiniCPM). No OpenVINO build runs the Qwen3.5 GGUFs on CPU/GPU/NPU.
  2. **`--cache-reuse` is inapplicable on the NPU path.** `--cache-reuse` is a native-backend (CPU/Vulkan) prompt-prefix KV reuse feature; the OpenVINO backend replaces the graph executor, `GGML_OPENVINO_CACHE_DIR` model caching is explicitly "not supported on NPU devices", and stateful KV execution is "not effective on NPUs". NPU also needs small context (~1024; 8K is a preview on 32GB Series 2 only) vs the runtime's required 16384.
- Even for a supported dense model, NPU would lose the current wins: Q4_0-primary (Q6_K→Q4_0_128 requant = quality loss vs Q4_K_M), `-fa 1` required, `-np > 1` unsupported, and spec-decode (ngram-mod 21.4 t/s / MTP 2x) is a native-backend feature the OpenVINO path cannot combine with. The NPU driver update matters for OpenVINO GenAI/Studio-Effects workloads, not for llama.cpp. Revisit only if (a) the backend grows `SSM_CONV`/DeltaNet-hybrid support AND (b) NPU model caching + >8K context ship. Current build (`bin/`, CPU+Vulkan only, no `ggml-openvino.dll`) unchanged.
- **RE-CHECKED 2026-08-03 against driver 32.0.100.4841 (bundles OpenVINO 2026.2.1): verdict UNCHANGED.** OpenVINO 2026.2 did add `CausalConv1D`/`GatedDeltaNet` kernels and Qwen3.5/Qwen3.6 model support — but **"Only on CPUs & GPUs"** (2026.2 release notes); NPU is still excluded for Qwen3.5. The llama.cpp OpenVINO backend still cannot translate Qwen3.5's `SSM_CONV`/`GATED_DELTA_NET` ops (ggml#20562 still open, last touched 2026-05). NPU-side additions (Flash Attention, UMD dynamic model caching, longer contexts) apply only to **Series 3 (Panther Lake)** and to OpenVINO's own pipeline, not to the llama.cpp backend on this Meteor Lake 135U — where `GGML_OPENVINO_CACHE_DIR` remains "not supported on NPU" and context is still ~1K. Driver 4841 is worth installing for OpenVINO GenAI CPU/GPU + Studio Effects; it does nothing for llama.cpp on this machine.
- **DirectML also ruled out (2026-08-03), worse than OpenVINO:** (1) DirectML NPU support was a 2024 developer preview and is now **deprecated** — DirectML is in maintenance mode (microsoft/DirectML#710) and has been **removed from the Intel NPU driver** entirely; Microsoft's successor is New WindowsML (ONNX Runtime). (2) llama.cpp's DirectML backend is **GPU-only, never NPU** (ggml#7772 — NPU DirectML is metacommands-only with no custom GGML kernels). (3) It has **no `GATED_DELTA_NET`/`SSM_CONV` kernels**, so Qwen3.5's recurrent layers fall back to CPU — the same broken fallback that produced gibberish/crashes on SYCL (ggml#20423). (4) Even for dense models it would be slower than the already-installed Vulkan backend on this iGPU and can't combine with ngram-mod/MTP spec-decode. DML NPU would also require ONNX Runtime + model conversion (not GGUF). No DirectML anywhere in this stack.

### IMPLEMENTED - Grammar-constrained local tool decisions (`SWARM_GRAMMAR_DECODE=1`)
- `runtime_v2/services/_grammar_schema.py` (new): GBNF grammar generated from `TOOL_CALL_SCHEMA` (`_llm_parser.py`); injected into local qwen3.5 calls in `_llm_client.py` only when the gate is on. Cloud/DeepSeek requests NEVER receive `response_format` (contract kept). Sync-guarded by `tests/test_grammar_decode.py::test_schema_remains_synced`.

### IMPLEMENTED - Shared reflexion memory (`SWARM_SHARED_REFLEXION=1`)
- `swarm_os/services/reflection_loop.py`: `store_reflexion()` gained a `scope` payload field (default `"agent"`); `_auto_scope()` upgrades a rule to `"shared"` when confidence >= 0.7 AND the failure is on a generic allowlist (file-not-found/permission-denied/timeout/slot-busy/malformed/truncated/parse). `check_for_past_mistakes()` merges cross-agent `scope=shared` hits into `[PAST-MISTAKE WARNING]` injection only when enabled. 8 tests in `tests/test_shared_reflexion.py`.

- **Final System Audits & Core Hardening (2026-08)**: 
  - **State Leak in Agent Loop Resolved**: Fixed untime_v2/api/agent_service_v2.py. The \step_agent_stream\ generator was vulnerable to cross-run state contamination when aborted early (e.g., via FastAPI disconnects). Wrapped the generator in an async \	ry...finally\ block to securely isolate and reset \_explored_paths\ and \_filesystem_read_cache\ for each worker, preventing agents from hallucinating file states from concurrent requests.
  - **True Evolutionary Differential Scoring (Evolution Daemon)**: Fixed \swarm_os/services/evolution_daemon.py\. The genetic kernel was plateauing at the default prior (0.0425) because fitness tracking was aggregated instead of mapped to specific \genome_id\s. Implemented true differential scoring that correlates task outcomes directly with the genome that produced them.
  - **Epsilon-Greedy Exploration (20%)**: Added epsilon-greedy exploration to \volution_daemon.py\'s \get_active_genome()\ to ensure newborn genomes are actually evaluated in production rather than being starved by the incumbent elite.
  - **Turn-Budget Penalty Enforcement**: Fixed \gent_service_v2.py\ early-exit paths (max turns, circuit breaker, looping aborts). These paths were terminating without calling \_feed_outcome\, dropping vital telemetry. All terminal exit paths now explicitly register their failures with the evolutionary kernel, strictly penalizing inefficient tool policies.

  - **Internet-Goal Loop Fix**: Fixed a bug in \untime_v2/api/agent_service_v2.py\ where agents without a filesystem warmup sequence (like \coder\) were permanently skipping the turn-0 \web_search\ injection on internet goals, causing them to get stuck in a circuit-breaker loop trying to finalize without fetching web content. Also added \web_fetch\ to the \debugger\ agent's allowed tools in \untime_v2/prompts/system_prompts.py\ so it can properly analyze research without looping.
