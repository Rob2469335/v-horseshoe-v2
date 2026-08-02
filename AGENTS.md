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
| `orchestrator.py` | 577 | `Orchestrator.generate()` — text generation loop with tool-call parsing, dedup, routing |
| `orchestrator_v10.py` | 90 | Legacy orchestrator version |
| `message_bus.py` | 78 | Async event bus with `Event` dataclass, `subscribe()`/`publish()` via `asyncio.Queue` |
| `tool_parser.py` | 97 | `ToolParser` — stateless tool-call extraction from LLM text (3 pattern formats + CLI) |
| `settings.py` | — | Settings/config dataclasses |

### swarm_os/api/ (HTTP API)

| File | Lines | Role |
|------|-------|------|
| `routes.py` | 590 | Main router: `/readyz`, `/router`, `/critic`, `/memories`, `/timeline`, `/healing/evaluate`, `/traces/summary`, `/tools/cache`, `/tools/execute`, `/models/autoassign` |
| `api_features.py` | 486 | Feature router: semantic search, chat-search SSE, Upwork analyzer, codebase indexing, snapshot lifecycle, approval workflows |
| `agents.py` | 201 | Agent CRUD + step execution + model management |
| `admin.py` | 207 | Health evaluation, heal cycles, simulation management |
| `schemas.py` | 88 | Pydantic schemas |
| `dependencies.py` | 38 | FastAPI DI: `runtime_dep()`, `get_orchestrator()` |
| `api_health.py` | — | Health endpoint logic |
| `health.py` | — | Health probe helpers |

### swarm_os/services/ (Application Services)

| File | Lines | Role |
|------|-------|------|
| `tool_registry.py` | 304 | `SemanticToolRegistry` — Qdrant-backed semantic tool discovery with async client |
| `llm_client.py` | 208 | `CloudLLMClient` — detects provider (OpenRouter/NVIDIA/llama.cpp) via litellm |
| `genetic_mutation_loop.py` | 222 | Code mutation loop for self-improvement |
| `vector_store.py` | 172 | Qdrant vector store wrapper (AsyncQdrantClient) |
| `reflection_loop.py` | 164 | `ReflectionService` — ASPO rule distiller: failures → correction rules → Qdrant |
| `chat_service.py` | 121 | Context compaction, model auto-assignment, reachability checks |
| `knowledge_graph.py` | 76 | AST import dependency graph (networkx) |
| `system_service.py` | 69 | Multi-layer health (system, LLM, Qdrant) |
| `security_gate.py` | 60 | AST code security scanner (banned calls/modules) |
| `danger_room.py` | 92 | Isolated sandbox for safe code mutation testing |
| `memory_daemon.py` | 31 | Background memory consolidation (5-min interval) |
| `token_manager.py` | 37 | Token budget tracking with async lock |
| `embedding_service.py` | — | Dedicated embedding client (port 8081, nomic-embed) |
| `health.py` | — | Backend health checker |
| `llm/client.py` | 111 | Lower-level LLM client with thread pool + semaphore |

### swarm_os/services/rv_finder/ (Used-RV Deal Finder package)

Package split from the deleted 1,275-line `rv_finder.py`. Exposed as `find_best_rv_deals()` via `__init__.py`; wired to `POST /features/rv-finder/search` in `api_features.py`.

| File | Lines | Role |
|------|-------|------|
| `service.py` | — | `find_best_rv_deals()` orchestrator; type-filter normalization, best_motorhome fallback |
| `parsers.py` | — | HTTP + PPL + web discovery, `DISCOVERY_PARSERS`, `_parse_snippet(title, body, url)`, junk-title filter |
| `analysis.py` | — | Pure domain logic: deal scoring, `_title_motorhome`, `_is_motorhome_like`, life-ease, flags |
| `knowledge.py` | — | Static tables: `KNOWN_WEAK_SPOTS`, `LIFE_EASE_FEATURES`, `KNOWN_MOTORHOME_MODELS` |
| `llm.py` | — | `_llm_deep_dive`: OpenRouter DeepSeek first (60s, `num_retries=0`), qwen3.5-9b local fallback (300s) |
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
| `recovery_engine.py` | 222 | Coordinated recovery with anomaly tracking |
| `healing_service.py` | 85 | `AnomalyTracker`, `FailureDetector`, `RecoveryEngine`, `RollbackManager` |
| `governor.py` | 120 | Governance model tracking |
| `offline_learner.py` | 108 | Batch rule extraction from events.jsonl |
| `reviewer.py` | — | Heal review logic |
| `healing_loop.py` | — | Healing event loop |
| `failure_detector.py` | — | Failure detection probes |

### swarm_os/memory/ (Memory Bridge)

| File | Lines | Role |
|------|-------|------|
| `memory_bridge.py` | 551 | `MemoryBridge` — event ingestion, vector ops, consolidation, GraphRAG, integrates with EventLogRepo, GraphRepo, MemoryDaemon |
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
| `genetics.py` | 325 | Genetic mutation engine (consolidated from genetics + genetics_v2) |
| `selection.py` | 357 | Selection/mating logic |
| `organism.py` | 108 | Organism lifecycle |
| `brain.py` | 102 | Brain logic |

### swarm_os/rest/

| File | Lines | Role |
|------|-------|------|
| `brain.py` | 276 | Brain coordination |
| `swarm_kernel.py` | 246 | Swarm kernel (organism lifecycle) |
| `selection.py` | 277 | Selection algorithms |
| `agent_runtime.py` | 83 | Agent runtime |
| `organism.py` | 95 | Top-level organism |
| `governor.py` | — | Governance logic |
| `bootstrap.py` | — | System bootstrap |
| `migrations.py` | 151 | Data migrations |

### runtime_v2/api/ (Agent Execution)

| File | Lines | Role |
|------|-------|------|
| `agent_service_v2.py` | 454 | `AgentServiceV2` class — `step_agent_stream()` main agent loop. Orchestrates decisions, actions, healing. |
| `_agent_config.py` | 25 | Constants: `MAX_TURNS`, `MAX_DEPTH`, `_DEFAULTS`, `ANALYSIS_AGENTS` |
| `_agent_routing.py` | 83 | `fast_route_coordinator()`, `fast_start_for_agent()`, `lookup_model()` — keyword routing + warmup |

### runtime_v2/services/ (LLM & Tool Services)

| File | Lines | Role |
|------|-------|------|
| `memory_core.py` | 411 | `remember_fat()`, `get_relevant_memories()` — Qdrant-backed memory |
| `_llm_parser.py` | 228 | `extract_json()`, `normalize_decision()`, `normalize_model_json()`, `TOOL_CALL_SCHEMA`, `fire_and_forget()` |
| `stream_runner.py` | 213 | `get_tool_decision()` — orchestration: MCP schema, memory injection, retry loop, LLM call |
| `tool_executor.py` | 191 | `run(tool_name, payload)` — dispatches tool calls |
| `fallback_manager.py` | 190 | `get_live_fallbacks()` — cloud model fallbacks |
| `_llm_client.py` | 124 | `complete_for_tool_decision()`, `stream_content()`, `build_kwargs()`, `SSL setup`, `get_litellm_model()` |
| `model_registry.py` | 71 | `get_model(agent_id)` — agent → model mapping (deepseek-coder → qwen3.5-9b) |
| `_llm_prompts.py` | 71 | `build_tool_decision_system()`, `JSON_REPAIR_PROMPT` (includes `/no_think` for Qwen3) |
| `_llm_cache.py` | 30 | Decision cache with TTL eviction |
| `learning/evolving_critic.py` | 24 | `EvolvingCritic.score()` — metacognition feedback |

### src/ (Next-Gen Agent Runtime & Memory)

| File | Lines | Role |
|------|-------|------|
| `core/agent_runtime.py` | 837 | Next-gen agent runtime |
| `orchestration/orchestrator.py` | 768 | Next-gen orchestrator |
| `agent_memory/working_memory.py` | 655 | `LRUWorkingMemory`, `StringWorkingMemory` |
| `orchestration/policy_graph.py` | 610 | Policy graph for orchestration |
| `orchestration/router.py` | 602 | Next-gen request router |
| `agent_memory/hybrid_memory.py` | 575 | `HybridMemory` — facade combining vector + episodic + working stores |
| `orchestration/health_monitor.py` | 536 | Health monitoring |
| `agent_memory/episodic_store.py` | 518 | `EpisodicStore` — temporal event history |
| `orchestration/circuit_breaker.py` | 422 | Circuit breaker pattern |
| `agent_memory/vector_store.py` | 412 | In-memory vector store (`VectorStore`) |

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

## Qwen3.5-9B Migration

- **Model**: `models/Qwen3.5-9B-Q4_K_M.gguf` (in `models/` directory, 9B params)
- **Model name in API**: `qwen3.5-9b` (used in `config/agent_models.json` and `model_registry.py`)
- **Thinking mode**: Disabled via `/no_think` prepended to all system prompts in `_llm_prompts.py`
- **Server**: `bin\llama.exe serve -m "models\Qwen3.5-9B-Q4_K_M.gguf" --alias "qwen3.5-9b" -c 16384 -fa on -ctk q8_0 -ctv q8_0 -t 2 -tb 4 -b 2048 -ub 512 -np 1 --timeout 300 --port 8080`
- **Fallback**: `reviewer` agent still uses `openrouter` backend (`deepseek/deepseek-r1:free`)
- **Analysis agents prefer cloud**: `code_analyzer`, `researcher`, `reviewer` route to **DeepSeek V4 Flash** (`openrouter/deepseek/deepseek-chat`) for all tool decisions + content streaming whenever `OPENROUTER_API_KEY` is present and cloud is enabled (see `runtime_v2/services/_llm_client.py` `_ANALYSIS_CLOUD_AGENTS` / `_analysis_cloud_enabled()`). Override model via `ANALYSIS_CLOUD_MODEL`; force local via `SWARM_ANALYSIS_CLOUD=off` or `/local` (routing mode `local_only`). Rationale: a 9B local model at ~6 t/s makes codebase audits and web-research synthesis take tens of seconds per decision; the cloud model resolves that while local chat stays on qwen3.5-9b.

---

## Recent Changes (do NOT re-apply)

- **Model switch**: Updated `start_llama.bat`, `config/agent_models.json`, and `model_registry.py` from `deepseek-coder` → `qwen3-14b` → `qwen3.5-9b`
- **Thinking mode**: Added `/no_think` to both system prompt paths in `_llm_prompts.py` for Qwen3 compatibility
- **Ollama → LlamaClient**: `swarm_os/infra/ollama.py` renamed to `llama_client.py`, `OllamaClient` → `LlamaClient`
- **Async migration**: `QdrantClient` → `AsyncQdrantClient` across `vector_store.py`, `tool_registry.py`, etc.
- **New services**: `chat_service.py`, `llm_client.py`, `memory_daemon.py`, `reflection_loop.py`, `security_gate.py`, `system_service.py`, `knowledge_graph.py`, `danger_room.py`, `token_manager.py`
- **New repositories/**: `event_log_repo.py`, `graph_repo.py`, `mutation_repo.py`, `snapshot_repository.py`, `file_snapshot_repository.py`
- **Control plane expansion**: 17 modules in `services/control_plane/` (router, planner, critic, strategy, guardian, etc.)
- **API expansion**: `routes.py` +420 lines, new `api_features.py` (534 lines), new `dependencies.py`
- **RV finder packaged**: 1,275-line `swarm_os/services/rv_finder.py` split into `swarm_os/services/rv_finder/` package (see module map). Bug-fix pass: junk-title filter (`_is_junk_title`), type-filter aliases (`class b/c`, `van`, `motorhome`), title-only classification in `_parse_snippet(title, body, url)`, PPL detail-fetch resilience (`return_exceptions=True`), `best_motorhome` requires a title-confirmed motorhome, deep-dive `num_retries=0` + 60s cloud / 300s local timeouts (litellm retry-hang was eating the old 120s budget). 33 tests in `tests/test_rv_finder.py`. UI: `organism-console/src/components/organism/RvFinderRunner.tsx` (new) calls `POST /features/rv-finder/search` directly with budget/type/deep-dive controls; `AutomationRunner.tsx` branches to it for `automationId === "used-rv-finder"`.

---

## Bug Fixes (Codebase Analysis)

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

---

## Self-Healing & Self-Learning Fixes

- **Rule (a)**: advice

- **Rule (code_analyzer)**: ask for clarification

- **Rule (test)**: Always verify collection names before search.
- **Reflection distiller**: `reflection_loop.py` `_distill()` now calls the sanctioned cloud **DeepSeek V4 flash** (`openrouter/deepseek/deepseek-chat`, `max_tokens=600`, 90s timeout) first, with local `qwen3.5-9b` fallback (`/no_think` system lead + `max_tokens=2048`, 900s timeout). Local qwen3.5-9b burns all `max_tokens` on `reasoning_content` for the long distiller prompt (empty `content`, finish=length at ~5 tok/s); DeepSeek emits the structured `<reflection>` directly. Verified live: distill → Qdrant `ReflexionMemory` store → `check_for_past_mistakes` retrieval → `[PAST-MISTAKE WARNING]` injection.
- **qdrant-client ≥1.18 migration**: `AsyncQdrantClient.search()` was removed. `reflection_loop.py` (`query_points`) and `tool_registry.py` (`query_points`) now use `query_points()` with `getattr(response, "points", response)`; `tests/test_tool_registry.py` updated to mock `query_points.return_value = SimpleNamespace(points=[...])`.

### Auth header cleanup (Invalid API Key warnings)
- `memory_bridge.py`, `token_tracker.py`, `picker.py`, `_commands_ai.py`, `ops/health/system_health.py`: Added `Authorization: Bearer llama` headers to requests hitting ports 8080-8083.
- `recovery_engine.py`, `genetic_mutation_loop.py`, `reflection_loop.py`, `offline_learner.py`: Added `api_key="llama"` + `custom_llm_provider="openai"` to litellm calls.
- `organism_console/cli.py`: Added `load_dotenv(override=True)` so CLI commands automatically read API keys (e.g. `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_BASE_URL`) from `.env`.
- `start-dev.ps1`: Added explicit fallback environment variable assignments for all ZENITH Swarm OS API Keys (`OPENAI_API_KEY`/`OPENAI_API_BASE` for OpenCodeGo, `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, search providers, etc.) so running `.\start-dev.ps1` guarantees every API key is initialized in PowerShell even if `.env` is missing.

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
Removed all stale model references (`qwen2.5:7b-instruct`, `qwen2.5:3b-instruct`, `qwen-tuned`, `qwen3-vl:8b`, `qwen3-embedding:8b`) → `qwen3.5-9b` / `moondream:latest` / `nomic-embed-text-v1.5` across:
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
- `upwork/engine.py` + `reasoning_layer.py`: stale `qwen3:14b` → `qwen3.5-9b`.
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
`OrganismConstellation.tsx` (both consoles): `genomeType` fallback was `'qwen:35b'` → now `'qwen3.5-9b'`. Only shows when no model data exists.

### Vision showed the generation model
`swarm_os/api/routes.py` `/status`: `primary_vision_model` was `installed_models[0]` (qwen3.5-9b). Now filters for actual vision models (`vl`/`vision`/`moondream`/`llava`) → `moondream-latest`.

### Control-plane registry routed to nonexistent `qwen3.5-9b:latest`
`shared_model_registry.py`: profiles/role pool used `qwen3.5-9b:latest` (server alias is `qwen3.5-9b`) and referenced removed models (phi4-mini, qwen2.5-coder:7b, smallthinker:20b, qwen3:4b). Normalized all to `qwen3.5-9b`.

### "Generation 0" on genomes dashboard
`swarm_os/api/admin.py`: `_latest_snapshot_payload` called `build_status(None, None)` so `generation` was always None; `/generation` also omitted the field. Now derives generation from snapshot data + exposes it.

### CLI routing model heuristic simplified
`organism_console/_command_routing.py`: leftover `4b`/`llama3-groq`/`ministral` matching → prefer any `qwen3.5`/`qwen3`/`9b` installed model.

---

## Online-Researched Upgrades (2025-26 SOTA patterns)

### PS/MV failure classification (Diagnostician)
`swarm_os/healing/diagnostician.py`: Every hypothesis now carries `fix_class` = `prompt_sensitivity` (fixable via rule/script changes → sandbox repair) vs `model_variability` (model limitation → escalate to cloud/human). Routes governor recovery paths by failure type instead of confidence guessing. Added format_violation + delegation_loop hypotheses.

### Structured + ranked + decayed reflection memory
`swarm_os/services/reflection_loop.py`: Distiller now uses a structured template (`failure_summary`/`root_cause`/`next_attempt_rules`/`do_not_repeat`). Rules stored with `component`, `timestamp`, `confidence` metadata. Retrieval uses ranked top-k with recency decay + confidence weighting (not single 0.85-threshold hit). Model alias → `qwen3.5-9b`.

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
`runtime_v2/services/online_routing.py` (new) + `runtime_v2/services/_llm_client.py`: analysis agents (`code_analyzer`/`reviewer`/`researcher`) were hard-pushed to cloud whenever a key existed. Now the cloud hop is **win-rate gated**: per-agent success/failure persisted to `_agent_winrates.json`; the cloud hop only stays while win-rate ≥ `SWARM_WINRATE_FLOOR` (0.5) with ≥ `SWARM_WINRATE_MIN_SAMPLES` (5) samples. Low win-rate decays analysis back to local qwen3.5-9b. Closed-loop via `record_analysis_outcome(agent_id, ok)` called in `stream_runner` (success + the final failure path).
- **Off by default**: `SWARM_WINRATE_ROUTING=1` to enable. Defaults to the legacy behavior when disabled.

### IMPLEMENTED — llama.cpp KV prompt-prefix reuse (`--cache-reuse 256`)
`start_llama.bat`, `start-dev.ps1`, `start-dev-fixed.ps1`: added `--cache-reuse 256` to the generation server (`:8080`) so the stable system prompt + tool schema (`[PROJECT MAP]`, `[RELEVANT MEMORIES]`, tool schema) is KV-reused across the repeated `get_tool_decision()` calls — cutting prefill TTFT on the single-slot backend.

### IMPLEMENTED — Reflection lesson token-budget cap (defense-in-depth)
`swarm_os/services/reflection_loop.py` `check_for_past_mistakes(..., max_chars=700)`: the injected `[PAST-MISTAKE WARNING]` hint is now hard-capped so distilled lessons never eat the decision-context budget (lesson budget ≈ 10-20% of context per 2026 guidance). Retrieval already used recency+confidence decay and top-k ranking.

### DEFERRED — further research items (documented, not yet built)
- **Reasoning-aware memory reranker**: swap the generic BGE reranker (`:8082`) for Qwen3-Reranker/MemReranker line to get calibrated relevance scores usable as thresholds. `runtime_v2/services/memory_core.py` `rerank_memories()`.
- **Selective/failure-class-gated reflection**: trigger deep LLM distillation only for diagnosable failure classes (already tagged via `fix_class` in `diagnostician.py`) — reflection measurably regresses already-good paths. Gate in `reflection_loop.py` `_distill()`.
- **MCP batch/parallel dispatch**: dependency-aware `asyncio.gather` over independent tools + merged `batch_execute` (BatchIt pattern) in `stream_runner.py`/`tool_executor.py`. Tool schemas are already cached in-process via `ExternalMCPClientManager.cached_tools` (now thread-safe).
- **Durable checkpointed turns**: Pydantic AI v2 "capability" decomposition + step-level checkpoints so an interrupted multi-delegation `step_agent_stream` resumes at the last completed step instead of replaying from the top.

---

## Speculative Decoding & Local-Model Tuning (2026-08)

Research-backed (llama.cpp `docs/speculative.md`; ggml-org/llama.cpp#15307 OpenVINO backend; PR #20700 Qwen3.5 MTP; unsloth MTP docs; ggml-org/llama.cpp#10594/#10664 draft-on-quant regressions). Bottom line: **MTP is the winning speed lever on this hardware; the Intel NPU is real now but not worth it for a 4B target.**

### IMPLEMENTED - MTP speculative decoding (Qwen3.5 built-in heads), ~2.0x on the 4B
- Qwen3.5 ships MTP (`nextn`) heads ("MTP: trained with multi-steps"). The plain `models/Qwen3.5-9B-Q4_K_M.gguf` and `C:\Users\rober\models\Qwen3.5-4B-Q4_K_M.gguf` GGUFs contain NO MTP tensors (GGUF scan: 426-427 tensors, no `nextn`); the unsloth MTP GGUFs do (`blk.{N}.nextn.{eh_proj,enorm,hnorm,shared_head_norm}`).
- New files: `C:\Users\rober\models\Qwen3.5-4B-UD-Q4_K_XL.gguf` (2.79 GiB, 441 tensors, MTP) and `C:\Users\rober\models\Qwen3.5-0.8B.Q4_K_M.gguf` (0.50 GiB, 335 tensors, MTP). Both share the Qwen3.5 family vocab - verified 16/16 identical token IDs across 0.8B/4B/9B via `llama-tokenize`.
- Measured (prod flags `-t 2 -tb 4 -ngl 99`, isolated): 4B-MTP + `--spec-type draft-mtp,ngram-simple --spec-draft-n-max 3` -> **12.98 t/s vs 6.36 plain 4B (~2.04x)**, `draft_n_accepted: 62/94 = 66%`.
- This llama.cpp build (c0bc8591e) already ships `draft-mtp` (MTP merged upstream 2026-05-16).
- Caveat: Qwen3.5 is DeltaNet-hybrid; PR #20700 notes recurrent-state checkpoint/restore ("two-phase decode") can mute MTP gains - so ngram stays as the fallback and the two are stacked (`draft-mtp,ngram-simple`).

### IMPLEMENTED - Start-script wiring (`start_llama.bat`, `start-dev.ps1`, `start-dev-fixed.ps1`)
- `SWARM_SPEC_DECODE=1` master gate. `SWARM_SPEC_TYPE` selects the implementation (default `ngram-simple`, prior behavior unchanged):
  - `draft-mtp,ngram-simple` -> adds `--spec-draft-n-max 3`
  - `draft-simple,...` + `SWARM_DRAFT_MODEL=<path>` -> in-process draft model (0.8B verified same vocab)
- `SWARM_LOCAL_MODEL=qwen3.5-4b-mtp` -> serves the MTP 4B (`-ngl 99`, alias `qwen3.5-4b,qwen3.5-9b` kept so the runtime is unchanged).
- The earlier 4B-as-draft finding (~1.1x, ineffective) stands; the MTP head supersedes it. Note: the npm/React frontend has NO effect on generation speed - it is a thin client; speed comes entirely from llama.cpp.

### RESEARCHED - Intel NPU (Core Ultra 5 135U / Meteor Lake, Intel AI Boost, device `VEN_8086&DEV_7D1D`)
- OpenVINO is now an official llama.cpp backend (ggml-org/llama.cpp#15307, upstream 2026-03) - GGUF on Intel CPU/iGPU/NPU via `GGML_OPENVINO_DEVICE`, validated on Core Ultra Series 1/2.
- NOT adopted: requires a separate `-DGGML_OPENVINO=ON` build + oneAPI + NPU driver >= v2565; NPU is constrained (`-np 1`, "keep context small" ~1024, Q4_0-primary, no caching); cross-backend in-process draft is unproven. MTP delivered the speedup with zero extra toolchain. Revisit only for the 9B+ as a whole-model NPU target.

### IMPLEMENTED - Grammar-constrained local tool decisions (`SWARM_GRAMMAR_DECODE=1`)
- `runtime_v2/services/_grammar_schema.py` (new): GBNF grammar generated from `TOOL_CALL_SCHEMA` (`_llm_parser.py`); injected into local qwen3.5 calls in `_llm_client.py` only when the gate is on. Cloud/DeepSeek requests NEVER receive `response_format` (contract kept). Sync-guarded by `tests/test_grammar_decode.py::test_schema_remains_synced`.

### IMPLEMENTED - Shared reflexion memory (`SWARM_SHARED_REFLEXION=1`)
- `swarm_os/services/reflection_loop.py`: `store_reflexion()` gained a `scope` payload field (default `"agent"`); `_auto_scope()` upgrades a rule to `"shared"` when confidence >= 0.7 AND the failure is on a generic allowlist (file-not-found/permission-denied/timeout/slot-busy/malformed/truncated/parse). `check_for_past_mistakes()` merges cross-agent `scope=shared` hits into `[PAST-MISTAKE WARNING]` injection only when enabled. 8 tests in `tests/test_shared_reflexion.py`.
