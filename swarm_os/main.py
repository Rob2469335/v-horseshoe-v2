# swarm_os/app/main.py
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from ..core.logging import setup_logging
from ..core.settings import get_settings
from ..api.routes import router as core_router
from ..api.admin import router as admin_router
from ..api.dashboard import router as dashboard_router
from ..api.explorer import router as explorer_router
from ..api.features import router as features_router
from ..api.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    s.events_dir.mkdir(parents=True, exist_ok=True)
    s.snapshots_dir.mkdir(parents=True, exist_ok=True)
    yield


def create_app() -> FastAPI:
    setup_logging()
    s = get_settings()

    app = FastAPI(
        title       = s.app_name,
        version     = "2.0.0",
        description = "v-Horseshoe Enterprise v2 — Swarm OS API",
        lifespan    = lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins  = ["*"],
        allow_methods  = ["*"],
        allow_headers  = ["*"],
    )

    # ── API routers ───────────────────────────────────────────────────────────
    app.include_router(core_router,      prefix="/api")
    app.include_router(admin_router,     prefix="/api")
    app.include_router(dashboard_router, prefix="/api")
    app.include_router(explorer_router,  prefix="/api")
    app.include_router(features_router,  prefix="/api")
    app.include_router(health_router,    prefix="/api")

    # ── Static files ──────────────────────────────────────────────────────────
    static_dir = Path(__file__).parent.parent / "app" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── Frontend shell ────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    async def root():
        index = Path(__file__).parent.parent / "app" / "templates" / "index.html"
        if index.exists():
            return index.read_text(encoding="utf-8")
        return HTMLResponse(
            "<h2 style='font-family:monospace;padding:2rem'>"
            f"{s.app_name} v2 is running. "
            "<a href='/api/status'>status</a> | "
            "<a href='/docs'>api docs</a></h2>"
        )

    return app


app = create_app()
