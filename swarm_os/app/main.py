from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv
load_dotenv(override=True)

import os
import ssl
try:
    ssl._create_default_https_context = ssl._create_unverified_context
    ssl.create_default_context = ssl._create_unverified_context # Patch for aiohttp/httpx
    os.environ["LITELLM_VERIFY_SSL"] = "False"
    os.environ["CURL_CA_BUNDLE"] = ""
    os.environ["REQUESTS_CA_BUNDLE"] = ""
except Exception:
    pass


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

    @property
    def agent_runtime(self):
        svc = self.agent_service
        if svc and hasattr(svc, "runtimes"):
            return next(iter(svc.runtimes.values()), svc)
        return svc

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
        from swarm_os.foundation.events.event_store import EventStore
        event_store = EventStore()
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
        )
        log.info("AgentServiceV2 ready")
    except Exception as exc:
        log.warning(f"AgentService unavailable: {exc}")
        agent_service = None

    try:
        from swarm_os.services.control_plane.bootstrap import bootstrap_control_plane, get_router
        bootstrap_control_plane()
        router = get_router()
        log.info("Control plane bootstrapped")
    except Exception as exc:
        log.warning(f"Control plane bootstrap skipped: {exc}")
        router = None

    try:
        from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
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
    import sys
    async def _bg_index():
        if "pytest" in sys.modules:
            log.info("Test environment detected, skipping background indexing")
            return
        import os, time
        marker_path = r"C:\Users\rober\Projects\v-horseshoe-v2\.last_indexed"
        codebase_path = r"C:\Users\rober\Projects\v-horseshoe-v2\swarm_os"
        try:
            marker_time = os.path.getmtime(marker_path) if os.path.exists(marker_path) else 0
            newest_file_time = max(
                (os.path.getmtime(os.path.join(root, f))
                 for root, dirs, files in os.walk(codebase_path)
                 for f in files if f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".toml"))),
                default=0
            )
            if marker_time >= newest_file_time:
                log.info("Codebase unchanged since last index, skipping background indexing")
                return
        except Exception as exc:
            log.warning(f"Index freshness check failed, proceeding with indexing: {exc}")
        try:
            from swarm_os.lib.vector.code_indexer import index_project
            n = await asyncio.get_running_loop().run_in_executor(
                None, index_project, codebase_path
            )
            log.info(f"Background indexing complete: {n} chunks")
            with open(marker_path, "w") as f:
                f.write(str(time.time()))
        except Exception as exc:
            log.warning(f"Background indexing failed: {exc}")
    asyncio.create_task(_bg_index())

    log.info("RuntimeGraph mounted on app.state — all routes live")
    yield
    log.info("Swarm OS shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(title="Swarm OS", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from swarm_os.api.routes import router as api_router
    from swarm_os.api.agents import router as agents_router
    from swarm_os.upwork.routes import router as upwork_router
    from swarm_os.api.swarm_stream import router as swarm_stream_router

    app.include_router(api_router)
    app.include_router(agents_router)
    app.include_router(upwork_router)
    app.include_router(swarm_stream_router)

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

    @app.get("/status")
    async def status_endpoint(request: Request):
        runtime = getattr(request.app.state, "runtime", None)
        try:
            if runtime is not None:
                event_store = getattr(runtime, "event_store", None)
                all_events = event_store.read_all() if event_store else []
                orchestrator = getattr(runtime, "orchestrator", None)
                ollama_ok = await orchestrator.ollama.is_reachable() if orchestrator and hasattr(orchestrator, "ollama") else False
                models = await orchestrator.ollama.list_models() if orchestrator and hasattr(orchestrator, "ollama") else []
                from runtime_v2.services.fallback_manager import get_fallback_stats
                return {
                    "status": "ok" if ollama_ok else "degraded",
                    "ready": ollama_ok,
                    "ollama_reachable": ollama_ok,
                    "event_count": len(all_events),
                    "installed_model_count": len(models),
                    "installed_models": models,
                    "fallback_pool": get_fallback_stats()
                }
        except Exception as e:
            pass
        
        # Fallback to direct Ollama query if runtime state fails
        try:
            from swarm_os.infra.ollama import OllamaClient
            client = OllamaClient(base_url="http://127.0.0.1:11434")
            models = await client.list_models()
            return {
                "status": "ok",
                "ready": True,
                "ollama_reachable": True,
                "event_count": 0,
                "installed_model_count": len(models),
                "installed_models": models
            }
        except Exception:
            return {
                "status": "degraded",
                "ready": False,
                "ollama_reachable": False,
                "event_count": 0,
                "installed_model_count": 0,
                "installed_models": []
            }

    return app


app = create_app()

