# swarm_os/app/main.py

from __future__ import annotations

from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from infrastructure.cache.cache_provider import get_cache_provider
from infrastructure.runtime.background_jobs import BackgroundJobRunner, register_default_jobs
from infrastructure.vector.qdrant_collections import ensure_collections

from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.health import backend_health, refresh_backend_health

from swarm_os.repositories.file_snapshot_repository import FileSnapshotRepository
from swarm_os.services.simulation_service import SimulationService


@dataclass
class RuntimeGraph:
    orchestrator: Orchestrator
    cache: Any
    runner: BackgroundJobRunner
    snapshot_repo: FileSnapshotRepository
    simulation_service: SimulationService

    def start(self) -> None:
        try:
            ensure_collections()
        except Exception as exc:
            print("[startup] Qdrant init skipped:", exc)

        try:
            register_default_jobs(self.runner)
            self.runner.start()
        except Exception as exc:
            print("[startup] background jobs skipped:", exc)

    def stop(self) -> None:
        try:
            self.runner.stop()
        except Exception:
            pass


def get_runtime(app: FastAPI) -> RuntimeGraph:
    return app.state.runtime


def get_snapshot_repo(app: FastAPI) -> FileSnapshotRepository:
    return app.state.runtime.snapshot_repo


def get_simulation_service(app: FastAPI) -> SimulationService:
    return app.state.runtime.simulation_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    snapshot_repo = FileSnapshotRepository()
    simulation_service = SimulationService(snapshot_repo=snapshot_repo)

    runtime = RuntimeGraph(
        orchestrator=Orchestrator(),
        cache=get_cache_provider(),
        runner=BackgroundJobRunner(),
        snapshot_repo=snapshot_repo,
        simulation_service=simulation_service,
    )

    app.state.runtime = runtime
    app.state.orchestrator = runtime.orchestrator
    runtime.start()
    yield
    runtime.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Swarm OS", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # include core API routes at root and under /api to preserve historical endpoints
    from swarm_os.api.routes import router as core_router
    app.include_router(core_router)
    app.include_router(core_router, prefix="/api")

    # include admin routes (they have their own /admin prefix) under /api/admin
    from swarm_os.api.admin import router as admin_router
    app.include_router(admin_router, prefix="/api")

    return app


app = create_app()
