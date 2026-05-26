from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.worker import SwarmWorker
from swarm_os.api.routes import router

# Shared state
app_state = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Orchestrator
    orch = Orchestrator()
    app_state["orchestrator"] = orch
    
    # Initialize and start SwarmWorker as background task
    worker = SwarmWorker(orch)
    app_state["worker"] = worker
    
    # Start the worker loop in background
    worker_task = asyncio.create_task(worker.run_loop())
    app_state["worker_task"] = worker_task
    
    log = logging.getLogger(__name__)
    log.info("SwarmWorker started as background task")
    
    yield
    
    # Cleanup: stop worker
    worker.is_running = False
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    app_state.clear()

def create_app() -> FastAPI:
    import logging
    app = FastAPI(title="v-Horseshoe V2", lifespan=lifespan)
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    app.include_router(router)
    return app
