# swarm_os/app/main.py
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from swarm_os.services.orchestrator import Orchestrator
from swarm_os.services.worker import SwarmWorker
from swarm_os.api.routes import router

log = logging.getLogger(__name__)

# ── Shared app state ──────────────────────────────────────────────────────────
app_state: dict = {}

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: boot orchestrator + background worker. Shutdown: clean up."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    for d in ["data", "data/events", "data/snapshots", "logs"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    orch = Orchestrator()
    app_state["orchestrator"] = orch
    app.state.orchestrator = orch

    worker = SwarmWorker(orch)
    app_state["worker"] = worker
    worker_task = asyncio.create_task(worker.run_loop())
    app_state["worker_task"] = worker_task
    log.info("v-Horseshoe v2 online — SwarmWorker started")

    yield

    worker.is_running = False
    if "worker_task" in app_state:
        app_state["worker_task"].cancel()
        try:
            await app_state["worker_task"]
        except asyncio.CancelledError:
            pass
    app_state.clear()
    log.info("v-Horseshoe v2 shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="v-Horseshoe Enterprise v2",
        version="2.0.0",
        description="Swarm OS — AI Evolution Platform",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:4173",
            "http://localhost:4173",
            "http://127.0.0.1:5173",
            "http://localhost:5173"
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    gui_file = Path(__file__).parent / "templates" / "index.html"

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def root():
        if gui_file.exists():
            return HTMLResponse(gui_file.read_text(encoding="utf-8"))
        return HTMLResponse(
            "<h2 style='font-family:monospace;padding:2rem'>"
            "v-Horseshoe v2 is running. "
            "<a href='/docs'>API docs</a> | "
            "<a href='/health'>Health</a></h2>"
        )

    return app


app = create_app()

