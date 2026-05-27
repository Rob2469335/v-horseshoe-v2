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

    # Ensure data dirs exist
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
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    app_state.clear()
    log.info("v-Horseshoe v2 shutdown complete")


# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="v-Horseshoe Enterprise v2",
        version="2.0.0",
        description="Swarm OS — AI Evolution Platform",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routes ────────────────────────────────────────────────────────────
    app.include_router(router)

    # Try to include optional admin/dashboard routers
    try:
        from swarm_os.api.admin import router as admin_router
        app.include_router(admin_router, prefix="/api")
    except Exception as e:
        log.warning("admin router skipped: %s", e)

    try:
        from swarm_os.api.dashboard import router as dash_router
        app.include_router(dash_router, prefix="/api")
    except Exception as e:
        log.warning("dashboard router skipped: %s", e)

    try:
        from swarm_os.api.explorer import router as exp_router
        app.include_router(exp_router, prefix="/api")
    except Exception as e:
        log.warning("explorer router skipped: %s", e)

    # ── Static files ──────────────────────────────────────────────────────────
    static_dir = Path(__file__).parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── Frontend ──────────────────────────────────────────────────────────────
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
