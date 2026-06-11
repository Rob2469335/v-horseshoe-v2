from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from swarm_os.core.settings import Settings, get_settings
from swarm_os.events.store import EventStore
from swarm_os.healing.audit_logger import AuditLogger
from swarm_os.healing.controller import HealingController
from swarm_os.healing.executor import HealingExecutor
from swarm_os.healing.failure_detector import FailureDetector
from swarm_os.healing.graph import CausalGraphBuilder
from swarm_os.healing.recovery_policy import RecoveryPolicy
from swarm_os.healing.watchdog import RuntimeWatchdog
from swarm_os.services.orchestrator import Orchestrator

_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


@dataclass(slots=True)
class RuntimeGraph:
    settings: Settings
    event_store: EventStore
    orchestrator: Orchestrator
    healing: HealingController
    causal_graph_builder: CausalGraphBuilder

    def start(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
        self.settings.events_dir.mkdir(parents=True, exist_ok=True)

    def stop(self) -> None:
        return None


def build_runtime(settings: Settings | None = None) -> RuntimeGraph:
    resolved = settings or get_settings()
    event_store = EventStore(resolved.events_dir)
    policy = RecoveryPolicy()
    healing = HealingController(
        detector=FailureDetector(),
        policy=policy,
        executor=HealingExecutor(policy=policy, simulate=True),
        audit_logger=AuditLogger(resolved.data_dir / "audit" / "healing_log.jsonl"),
        event_store=event_store,
        watchdog=RuntimeWatchdog(
            cooldown_seconds=policy.cooldown_seconds,
            max_attempts=policy.max_attempts_per_window,
            instability_threshold=policy.instability_threshold,
        ),
    )
    return RuntimeGraph(
        settings=resolved,
        event_store=event_store,
        orchestrator=build_orchestrator(resolved, event_store),
        healing=healing,
        causal_graph_builder=CausalGraphBuilder(),
    )


def get_runtime(app: FastAPI) -> RuntimeGraph:
    return app.state.runtime


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    runtime = build_runtime()
    app.state.runtime = runtime
    app.state.orchestrator = runtime.orchestrator
    runtime.start()
    try:
        yield
    finally:
        runtime.stop()


def create_app() -> FastAPI:
    from swarm_os.api.routes import router
    
    app = FastAPI(title="Swarm OS", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()

__all__ = ["RuntimeGraph", "app", "build_runtime", "create_app", "get_runtime"]


