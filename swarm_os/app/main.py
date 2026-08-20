from __future__ import annotations

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from dotenv import load_dotenv

load_dotenv(override=True)

import os

# Set default HTTP timeout in environment if not already set
# read=None is critical for SSE streaming — LLM responses can take 30-120s
os.environ.setdefault("HTTPX_DEFAULT_TIMEOUT", "300.0")

# Enable SSL verification for security
# Only disable SSL verification if explicitly requested via environment variable
if os.environ.get("DISABLE_SSL_VERIFICATION", "").lower() in ("1", "true", "yes"):
    import ssl

    try:
        ssl._create_default_https_context = ssl._create_unverified_context
        ssl.create_default_context = ssl._create_unverified_context
        os.environ["LITELLM_VERIFY_SSL"] = "False"
        os.environ["CURL_CA_BUNDLE"] = ""
        os.environ["REQUESTS_CA_BUNDLE"] = ""
    except Exception as _ssl_exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "SSL verification override failed: %s", _ssl_exc
        )
else:
    os.environ.setdefault("LITELLM_VERIFY_SSL", "True")


# ---------------------------------------------------------------------------
# RuntimeGraph — every service the routes need, built once at startup
# ---------------------------------------------------------------------------


@dataclass
class RuntimeGraph:
    orchestrator: Any = None
    agent_service: Any = None
    healing: Any = None
    event_store: Any = None
    settings: Any = None
    cache: Any = None
    router: Any = None
    snapshot_repo: Any = None
    simulation_service: Any = None
    _agent_runtime_instance: Any = (
        None  # BUG FIX: cache instance to avoid creating new one per access
    )

    @property
    def agent_runtime(self):
        # BUG FIX: Return the same AgentRuntime instance every call.
        # Previously this @property called AgentRuntime() on every access,
        # breaking statefulness (tools list, caches, etc.)
        if self._agent_runtime_instance is None:
            from swarm_os.agent_runtime import AgentRuntime

            self._agent_runtime_instance = AgentRuntime()
        return self._agent_runtime_instance

    # agents.py calls runtime.agents — wire it to agent_service
    @property
    def agents(self):
        return self.agent_service


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    log = logging.getLogger("swarm_os.startup")

    try:
        from swarm_os.core.settings import get_settings

        settings = get_settings()
        log.info("Settings loaded")
    except Exception as exc:
        log.warning(f"Settings unavailable: {exc}")
        settings = None

    try:
        from swarm_os.services.orchestrator import Orchestrator

        orchestrator = Orchestrator()
        log.info("Orchestrator ready")
    except Exception as exc:
        log.warning(f"Orchestrator unavailable: {exc}")
        orchestrator = None

    try:
        from swarm_os.events.store import EventStore
        from pathlib import Path

        event_store = EventStore(Path("data/events"))
        log.info("EventStore ready")
    except Exception as exc:
        log.warning(f"EventStore unavailable: {exc}")
        event_store = None

    try:
        from swarm_os.healing.healing_service import HealingService

        healing = HealingService()
        log.info("HealingService ready")
    except Exception as exc:
        log.warning(f"HealingService unavailable: {exc}")
        healing = None

    try:
        from runtime_v2.api.agent_service_v2 import AgentServiceV2

        agent_service = AgentServiceV2(
            orchestrator=orchestrator,
            settings=settings,
            event_store=event_store,
        )
        log.info("AgentServiceV2 ready")
    except Exception as exc:
        log.warning(f"AgentService unavailable: {exc}")
        agent_service = None

    try:
        from swarm_os.services.control_plane.bootstrap import (
            bootstrap_control_plane,
            get_router,
        )

        bootstrap_control_plane()
        router = get_router()
        log.info("Control plane bootstrapped")
    except Exception as exc:
        log.warning(f"Control plane bootstrap skipped: {exc}")
        router = None

    try:
        from swarm_os.repositories.file_snapshot_repository import (
            FileSnapshotRepository,
        )

        snapshot_repo = FileSnapshotRepository()
        log.info("FileSnapshotRepository ready")
    except Exception as exc:
        log.warning(f"FileSnapshotRepository unavailable: {exc}")
        snapshot_repo = None

    try:
        from swarm_os.services.simulation_service import SimulationService

        simulation_service = SimulationService(snapshot_repo=snapshot_repo)
        log.info("SimulationService ready")
    except Exception as exc:
        log.warning(f"SimulationService unavailable: {exc}")
        simulation_service = None

    runtime = RuntimeGraph(
        orchestrator=orchestrator,
        agent_service=agent_service,
        healing=healing,
        event_store=event_store,
        settings=settings,
        router=router,
        snapshot_repo=snapshot_repo,
        simulation_service=simulation_service,
    )
    app.state.runtime = runtime
    app.state.orchestrator = orchestrator

    import asyncio

    bg_tasks = set()
    # Background indexing removed: it causes a 10-minute Ollama embedding bottleneck on startup.
    # Users should use the explicit `/index` command in the CLI which utilizes the optimized batch indexer.

    try:
        from runtime_v2.services.tool_executor import get_mcp_manager

        async def _mcp_init():
            try:
                await get_mcp_manager()
                log.info("External MCP Tools loaded and initialized")
            except Exception as exc:
                log.warning(f"Failed to initialize MCP Tools on startup: {exc}")

        # Load external MCP servers in the BACKGROUND — spawning npx subprocesses
        # (sqlite/memory/context7) serially can add tens of seconds to startup on
        # a cold npm cache. The API serves immediately; tools appear shortly after.
        t_mcp = asyncio.create_task(_mcp_init())
        bg_tasks.add(t_mcp)
    except Exception as exc:
        log.warning(f"MCP init task failed to start: {exc}")

    # BUG FIX: Start MemoryBridge background daemons so events are processed into Qdrant memories
    if orchestrator and hasattr(orchestrator, "bridge"):
        t1 = asyncio.create_task(orchestrator.bridge.watch_loop(interval_seconds=5.0))
        t2 = asyncio.create_task(
            orchestrator.bridge.start_manager_daemon(interval_seconds=300.0)
        )
        bg_tasks.add(t1)
        bg_tasks.add(t2)
        log.info("Started MemoryBridge daemons (watch_loop and start_manager_daemon)")

    try:
        from swarm_os.services.reflection_loop import run_reflection

        async def _reflection_daemon(
            interval_seconds: float = 600.0, first_delay: float = 120.0
        ):
            # Defer the first distillation (an LLM call) out of the startup
            # window so the backend serves immediately.
            if first_delay > 0:
                await asyncio.sleep(first_delay)
            while True:
                try:
                    await run_reflection()
                except Exception as exc:
                    log.warning(f"Reflection daemon iteration failed: {exc}")
                await asyncio.sleep(interval_seconds)

        t3 = asyncio.create_task(_reflection_daemon())
        bg_tasks.add(t3)
        log.info("Started Reflection daemon (ASPO rule distillation, 10-min interval)")
    except Exception as exc:
        log.warning(f"Reflection daemon unavailable: {exc}")

    # Telegram command center — the Command Center's chat presence + phone push.
    # Disabled (no-op) unless TELEGRAM_BOT_TOKEN is set in .env. Long-polling:
    # outbound-only, zero inbound ports. Registers its shutdown with the app so
    # the client closes cleanly.
    try:
        from swarm_os.services.telegram_center import get_center

        center = get_center()
        center.start()
        app.state.telegram_center = center
    except Exception as exc:
        log.warning(f"Telegram command center unavailable: {exc}")

    # Resume any incomplete chess.com analysis job (a prior 24h run that was
    # interrupted by a restart continues from where it left off).
    try:
        from swarm_os.services.chess_analysis_job import resume_incomplete

        await resume_incomplete()
    except Exception as exc:
        log.warning(f"Chess analysis job resume unavailable: {exc}")

    # Genetic self-improvement daemon — OPT-IN via SWARM_GENETIC_MUTATION=1.
    # The mutation loop is expensive (LLM + sandbox compile/test) and stages every
    # mutation to .data/pending_mutations/ for explicit approval, so it never
    # modifies live code on its own. Off by default to keep the runtime lean.
    try:
        import os as _os

        if _os.environ.get("SWARM_GENETIC_MUTATION", "").strip() == "1":
            from swarm_os.services.genetic_mutation_loop import run_genetic_mutation

            async def _mutation_daemon(
                interval_seconds: float = 3600.0, first_delay: float = 180.0
            ):
                # Defer the first expensive run (LLM + full-repo DangerRoom sandbox
                # copy + compile + pytest) out of the startup window so the backend
                # becomes responsive immediately; the hourly cadence then applies.
                if first_delay > 0:
                    await asyncio.sleep(first_delay)
                while True:
                    try:
                        await run_genetic_mutation()
                    except Exception as exc:
                        log.warning(f"Mutation daemon iteration failed: {exc}")
                    await asyncio.sleep(interval_seconds)

            t5 = asyncio.create_task(_mutation_daemon())
            bg_tasks.add(t5)
            log.info(
                "Started Genetic Mutation daemon (hourly, SWARM_GENETIC_MUTATION=1)"
            )
    except Exception as exc:
        log.warning(f"Genetic mutation daemon unavailable: {exc}")

    # Codebase index self-heal daemon: `semantic_search` is offered to
    # coder/researcher/debugger/code_analyzer, but the `codebase_index` collection
    # was only ever built by the manual CLI `/index` command — a fresh backend
    # always returned "Index not found" for every agent turn until a human
    # remembered to index. Rebuild the index on startup (deferred past the boot
    # window so the API serves immediately; embedding service :8081 is the
    # dedicated nomic-embed slot) whenever the collection is missing or empty.
    try:
        import os as _os2

        if _os2.environ.get("SWARM_CODEBASE_INDEX", "1").strip() == "1":
            from runtime_v2.services.indexer import (
                index_codebase,
                collection_exists,
            )
            import logging as _logging

            _index_log = _logging.getLogger("swarm_os.app.main.codebase_index")

            async def _index_daemon(first_delay: float = 90.0):
                # Defer out of the startup window: first-time indexing embeds the
                # whole repo (hundreds of batches through :8081) — running it
                # immediately would starve the CPU/embedding slot at boot.
                if first_delay > 0:
                    await asyncio.sleep(first_delay)
                try:
                    if collection_exists():
                        _index_log.info(
                            "codebase_index present — skipping startup rebuild"
                        )
                        return
                    root = _os2.getcwd()
                    _index_log.info("Rebuilding codebase index for %s ...", root)
                    files, chunks = await asyncio.to_thread(index_codebase, root)
                    _index_log.info(
                        "Codebase index ready: %s files, %s chunks", files, chunks
                    )
                except Exception as exc:
                    _index_log.warning("Codebase index rebuild failed: %s", exc)

            t_idx = asyncio.create_task(_index_daemon())
            bg_tasks.add(t_idx)
            log.info("Started codebase-index self-heal daemon (SWARM_CODEBASE_INDEX=1)")
    except Exception as exc:
        log.warning(f"Codebase index daemon unavailable: {exc}")

    # Outcome-driven evolution daemon — OPT-IN via SWARM_EVOLUTION=1. The agent
    # loop feeds REAL outcomes (task completion, tool success, efficiency) into
    # outcome_fitness when the same gate is on; this daemon runs evolutionary
    # generations on that grounded fitness (elitism + crossover + mutate) so the
    # kernel's tool-selection policy evolves against real execution, not LLM noise.
    try:
        import os as _os

        if _os.environ.get("SWARM_EVOLUTION", "").strip() == "1":
            from swarm_os.services.evolution_daemon import evolution_daemon

            t6 = asyncio.create_task(evolution_daemon(first_delay=60.0))
            bg_tasks.add(t6)
            log.info("Started Outcome-Driven Evolution daemon (SWARM_EVOLUTION=1)")
    except Exception as exc:
        log.warning(f"Evolution daemon unavailable: {exc}")

    # Autonomous watch-loop (2026 autonomy layer): tails events.jsonl server-side
    # and triggers code repair WITHOUT a human launching the CLI. Reads its budgets
    # from autonomy_policy.json (fail-closed if the policy is missing). Deferred
    # past the boot window so the API serves immediately; registered in bg_tasks
    # so shutdown cancels it cleanly. Gated by SWARM_AUTONOMY=1 (default on when
    # unset) — set to 0 to keep repairs manual-only.
    try:
        import os as _os_autonomy

        if _os_autonomy.environ.get("SWARM_AUTONOMY", "1").strip() == "1":
            from swarm_os.services.watch_loop import WatchLoop
            from organism_console.core.self_repair_engine import SelfRepairEngine

            async def _autonomy_watch(first_delay: float = 60.0):
                if first_delay > 0:
                    await asyncio.sleep(first_delay)
                loop = WatchLoop(SelfRepairEngine(), interval_seconds=30.0)
                loop.start(start_at_end=True)
                log.info(
                    "Started autonomous watch-loop (server-side, SWARM_AUTONOMY=1)"
                )
                while loop.is_running:
                    await asyncio.sleep(30.0)

            t_auto = asyncio.create_task(_autonomy_watch())
            bg_tasks.add(t_auto)
            log.info("Autonomous watch-loop daemon registered")
    except Exception as exc:
        log.warning(f"Autonomous watch-loop unavailable: {exc}")

    # Recurring agent-task scheduler (2026 SOTA). Runs due scheduled goals
    # (e.g. 'summarize my inbox every morning') through the existing agent
    # machinery, hard-blocked by the permission ceiling + fail-closed on unmapped
    # goals (see task_scheduler.py).
    try:
        from swarm_os.services.task_scheduler import TaskSchedulerDaemon

        _sched = TaskSchedulerDaemon(interval_seconds=60.0)
        _sched.start()
        log.info("Recurring task scheduler daemon started")
    except Exception as exc:
        log.warning(f"Task scheduler unavailable: {exc}")

    # Competitive Intelligence monitor — OPT-IN via SWARM_INTEL=1. Runs a weekly
    # full monitor (scan -> digest -> deliver) on a background cadence, exactly
    # once per window (duplicate-run protected), history preserved in data/intel.
    if os.environ.get("SWARM_INTEL", "0").strip() == "1":
        try:
            from swarm_os.services.competitive_intel import intel_daemon

            t_intel = asyncio.create_task(intel_daemon())
            bg_tasks.add(t_intel)
            log.info("Started Competitive Intel daemon (SWARM_INTEL=1, weekly)")
        except Exception as exc:
            log.warning(f"Competitive Intel daemon unavailable: {exc}")

    try:
        from swarm_os.healing.system_probes import run_system_probes

        async def _probe_warmup():
            try:
                await asyncio.to_thread(run_system_probes)
                log.info("Command-center probe cache warmed")
            except Exception as exc:
                log.warning(f"Probe warmup failed: {exc}")

        t4 = asyncio.create_task(_probe_warmup())
        bg_tasks.add(t4)
        log.info("Started system-probe warmup")
    except Exception as exc:
        log.warning(f"Probe warmup unavailable: {exc}")

    log.info("RuntimeGraph mounted on app.state — all routes live")
    yield
    log.info("Swarm OS shutting down")

    # Graceful shutdown of background tasks
    for task in bg_tasks:
        task.cancel()

    if bg_tasks:
        try:
            await asyncio.gather(*bg_tasks, return_exceptions=True)
        except Exception as e:
            log.warning(f"Error during background task shutdown: {e}")

    # Close shared httpx clients to release connection pools
    try:
        from swarm_os.core.orchestrator import (
            close_global_client as close_orchestrator_client,
        )

        await close_orchestrator_client()
    except Exception as exc:
        log.warning(f"Error closing orchestrator httpx client: {exc}")

    # Close the Telegram command center (long-poll loop + its httpx client)
    try:
        center = getattr(app.state, "telegram_center", None)
        if center is not None:
            await center.stop()
    except Exception as exc:
        log.warning(f"Error stopping telegram center: {exc}")

    try:
        from swarm_os.services.llm_client import close_global_client as close_llm_client

        await close_llm_client()
    except Exception as exc:
        log.warning(f"Error closing llm_client httpx client: {exc}")

    try:
        from swarm_os.capabilities.lsp_tool import close_all as close_lsp_clients

        await close_lsp_clients()
    except Exception as exc:
        log.warning(f"Error closing LSP clients: {exc}")

    if orchestrator:
        if getattr(orchestrator, "llm", None) is not None:
            try:
                await orchestrator.llm.aclose()
            except Exception as exc:
                log.warning(f"Error closing LlamaClient: {exc}")
        if getattr(orchestrator, "bridge", None) is not None:
            try:
                await orchestrator.bridge.close()
            except Exception as exc:
                log.warning(f"Error closing MemoryBridge: {exc}")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="Swarm OS", lifespan=lifespan)

    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # SECURITY: opt-in loopback API token. If SWARM_API_TOKEN is set (in .env or
    # the environment), every request except health/liveness probes and /docs must
    # carry `Authorization: Bearer <token>`. This neutralizes the unauthenticated
    # writer surface (code execution, agent steps, heal/admin) for any local
    # process or browser page when the swarm is deployed. When unset, the app
    # stays open (local single-user dev default).
    import os as _os

    _api_token = _os.getenv("SWARM_API_TOKEN", "").strip()
    if _api_token:

        @app.middleware("http")
        async def _api_token_guard(request, call_next):
            from starlette.responses import JSONResponse

            path = request.url.path
            if path in ("/health", "/readyz", "/", "/docs", "/openapi.json"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            # Constant-time compare (no early-exit on the first differing byte).
            import secrets as _secrets

            if not _secrets.compare_digest(auth, f"Bearer {_api_token}"):
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Unauthorized: missing or invalid SWARM_API_TOKEN"
                    },
                )
            return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        # BUG FIX: allow_origins=["*"] + allow_credentials=True is rejected by browsers
        # (CORS spec prohibits credentialed requests to wildcard origins).
        # Set explicit allowed origins instead.
        allow_origins=[
            "http://localhost:5173",  # Vite dev server
            "http://127.0.0.1:5173",  # Vite dev server via IP
            "http://localhost:3000",  # Next.js / CRA
            "http://localhost:8080",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from swarm_os.api.routes import router as api_router
    from swarm_os.api.agents import router as agents_router
    from swarm_os.upwork.routes import router as upwork_router
    from swarm_os.api.swarm_stream import router as swarm_stream_router
    from swarm_os.api.admin import router as admin_router
    from swarm_os.api.control import router as control_router
    from swarm_os.api.chess_trainer import router as chess_trainer_router

    app.include_router(api_router)
    app.include_router(agents_router)
    app.include_router(upwork_router)
    app.include_router(swarm_stream_router)
    app.include_router(control_router)
    app.include_router(chess_trainer_router)
    # BUG FIX: admin_router was imported and stored but never mounted.
    # All /admin/* endpoints were silently unreachable.
    app.include_router(admin_router)

    @app.get("/")
    def read_root():
        return {"message": "Swarm OS API"}

    @app.get("/health")
    def health_check():
        return {
            "status": "ok",
            "overall": "healthy",
            "health_score": 100,
        }

    return app


app = create_app()
