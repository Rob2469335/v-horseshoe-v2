"""
bootstrap.py  —  v-horseshoe-v2 entry point
Run with: uvicorn bootstrap:app --reload --port 8100
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from config.settings import (
    APP_TITLE, APP_VERSION, HOST, PORT,
    QDRANT_ENABLED, RERANKER_ENABLED,
    MCP_PLAYWRIGHT_ENABLED, MCP_FILESYSTEM_ENABLED,
    BLUEPRINT_HOT_RELOAD, SSE_DEBUG,
    ensure_dirs,
)

logging.basicConfig(
    level=logging.DEBUG if SSE_DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("bootstrap")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown sequence."""
    # ── Startup ──────────────────────────────────────────────────────────────
    log.info(f"Starting {APP_TITLE} v{APP_VERSION}")
    ensure_dirs()

    if QDRANT_ENABLED:
        from lib.vector.qdrant_store import init_collections
        await init_collections()
        log.info("Qdrant collections ready")

    if RERANKER_ENABLED:
        log.info("BGE reranker enabled — will load on first use")

    if MCP_PLAYWRIGHT_ENABLED:
        log.info("Playwright MCP enabled")

    if MCP_FILESYSTEM_ENABLED:
        log.info("Filesystem MCP enabled")

    if BLUEPRINT_HOT_RELOAD:
        from core.runtime.task_queue import load_blueprints
        await load_blueprints()
        log.info("Blueprints loaded")

    log.info(f"{APP_TITLE} ready on http://{HOST}:{PORT}")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    log.info(f"{APP_TITLE} shutting down")


app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    lifespan=lifespan,
)

# ── Static files ──────────────────────────────────────────────────────────────
static_dir = Path(__file__).parent / "app" / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── API routes ────────────────────────────────────────────────────────────────
from api.routes.health import router as health_router
from api.routes.blueprints import router as blueprint_router
from api.routes.features import router as features_router

app.include_router(health_router,    prefix="/api")
app.include_router(blueprint_router, prefix="/api/blueprints")
app.include_router(features_router,  prefix="/api/features")

# ── Frontend shell ────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent / "app" / "templates" / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return HTMLResponse(
        "<h2 style='font-family:monospace;padding:2rem'>"
        f"{APP_TITLE} is running. UI coming soon.</h2>"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bootstrap:app", host=HOST, port=PORT, reload=True)
