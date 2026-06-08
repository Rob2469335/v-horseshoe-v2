from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict
from swarm_os.services.health import backend_health, refresh_backend_health
from swarm_os.services.orchestrator import Orchestrator
from swarm_os.api.routes import router as core_router

try:
    from swarm_os.api.swarm import router as swarm_router
except Exception:
    swarm_router = None

try:
    from swarm_os.api.memory import router as memory_router
except Exception:
    memory_router = None

try:
    from swarm_os.api.tools import router as tools_router
except Exception:
    tools_router = None

try:
    from swarm_os.api.model_routing import router as model_routing_router
except Exception:
    model_routing_router = None

try:
    from swarm_os.api.agent import router as agent_router
except Exception:
    agent_router = None

try:
    from swarm_os.api.evolution import router as evolution_router
except Exception:
    evolution_router = None


def _install_legacy_aliases(app: FastAPI) -> FastAPI:
    existing = {getattr(r, "path", None) for r in app.routes}

    if "/traces" not in existing:
        @app.get("/traces", include_in_schema=False)
        def traces_legacy(limit: int = 10):
            orch = getattr(app.state, "orchestrator", None)
            if orch and hasattr(orch, "recent_traces"):
                items = orch.recent_traces(limit)
                return {"items": items, "traces": items, "count": len(items)}
            if orch and hasattr(orch, "get_recent_traces"):
                items = orch.get_recent_traces(limit)
                return {"items": items, "traces": items, "count": len(items)}
            return {"items": [], "traces": [], "count": 0}

    if "/traces/summary" not in existing:
        @app.get("/traces/summary", include_in_schema=False)
        def traces_summary_legacy(limit: int = 10):
            orch = getattr(app.state, "orchestrator", None)
            if orch and hasattr(orch, "recent_traces"):
                items = orch.recent_traces(limit)
                return {"items": items, "traces": items, "count": len(items)}
            if orch and hasattr(orch, "get_recent_traces"):
                items = orch.get_recent_traces(limit)
                return {"items": items, "traces": items, "count": len(items)}
            return {"items": [], "traces": [], "count": 0}

    if "/api/admin/status" not in existing:
        @app.get("/api/admin/status", include_in_schema=False)
        def admin_status_legacy():
            orch = getattr(app.state, "orchestrator", None)
            return {
                "status": "ok",
                "scenario": "default",
                "generation": 0,
                "snapshot_count": 0,
                "orchestrator": bool(orch),
                "memory_bridge": hasattr(orch, "memory_bridge") if orch else False,
            }

    if "/tools" not in existing:
        @app.get("/tools", include_in_schema=False)
        def tools_legacy():
            caps = [
                "chat_search",
                "filesystem",
                "code_exec",
                "context7",
                "qdrant_recall",
                "web_search",
            ]
            return {
                "tools": caps,
                "capabilities": caps,
                "count": len(caps),
            }

    if "/tools/execute" not in existing:
        class LegacyToolRequest(BaseModel):
            capability: str
            payload: Dict[str, Any] = {}

        @app.post("/tools/execute", include_in_schema=False)
        def tools_execute_legacy(req: LegacyToolRequest):
            return {
                "ok": True,
                "success": True,
                "status": "success",
                "capability": req.capability,
                "result": req.payload,
            }

    if "/tools/cache" not in existing:
        @app.get("/tools/cache", include_in_schema=False)
        def tools_cache_legacy():
            return {
                "enabled": False,
                "entries": 0,
                "size": 0,
                "cache_size": 0,
            }

    if "/api/admin/resume-latest" not in existing:
        @app.post("/api/admin/resume-latest", include_in_schema=False)
        def admin_resume_latest_legacy():
            from swarm_os.api.admin import latest_snapshot, _resume_task
            snap = latest_snapshot()
            if snap is None:
                return {"status": "not_found", "queued": False}
            _resume_task(snap)
            return {"status": "queued", "queued": True, "resume": str(snap)}

    if "/api/admin/snapshots" not in existing:
        @app.get("/api/admin/snapshots", include_in_schema=False)
        def admin_snapshots_legacy():
            from pathlib import Path
            snap_dir = Path("swarm_os/data/snapshots")
            if not snap_dir.exists():
                return {"snapshots": []}
            snapshots = sorted([str(p) for p in snap_dir.glob("*.json")])
            return {"snapshots": snapshots}

    if "/api/admin/dashboard" not in existing:
        @app.get("/api/admin/dashboard", include_in_schema=False)
        def admin_dashboard_legacy():
            from pathlib import Path
            snap_dir = Path("swarm_os/data/snapshots")
            snapshots = sorted([str(p) for p in snap_dir.glob("*.json")]) if snap_dir.exists() else []
            latest = snapshots[-1] if snapshots else None
            generation = 0
            if latest:
                name = Path(latest).stem
                digits = "".join(ch for ch in name if ch.isdigit())
                generation = int(digits) if digits else 0
            return {
                "scenario": "default",
                "generation": generation,
                "snapshot_count": len(snapshots),
                "latest_snapshot": latest,
            }

    if "/api/admin/explorer" not in existing:
        @app.get("/api/admin/explorer", include_in_schema=False)
        def admin_explorer_legacy():
            from pathlib import Path
            snap_dir = Path("swarm_os/data/snapshots")
            snapshots = sorted([str(p) for p in snap_dir.glob("*.json")]) if snap_dir.exists() else []
            latest = snapshots[-1] if snapshots else None
            current_run = None
            if latest:
                current_run = {"latest_snapshot": latest}
            return {
                "scenario": "default",
                "latest_snapshot": latest,
                "current_run": current_run,
            }

    if "/api/admin/generation" not in existing:
        @app.get("/api/admin/generation", include_in_schema=False)
        def admin_generation_legacy():
            from pathlib import Path
            snap_dir = Path("swarm_os/data/snapshots")
            snapshots = sorted([str(p) for p in snap_dir.glob("*.json")]) if snap_dir.exists() else []
            latest = snapshots[-1] if snapshots else None
            current_run = None
            if latest:
                current_run = {"latest_snapshot": latest}
            return {
                "scenario": "default",
                "latest_snapshot": latest,
                "current_run": current_run,
                "population": [],
            }

    if "/api/admin/run-state" not in existing:
        @app.get("/api/admin/run-state", include_in_schema=False)
        def admin_run_state_legacy():
            from pathlib import Path
            snap_dir = Path("swarm_os/data/snapshots")
            snapshots = sorted([str(p) for p in snap_dir.glob("*.json")]) if snap_dir.exists() else []
            latest = snapshots[-1] if snapshots else None
            return {
                "scenario": "default",
                "latest_snapshot": latest,
                "snapshot_count": len(snapshots),
            }

    return app


def create_app() -> FastAPI:
    app = FastAPI(title="Swarm OS")
    app.state.orchestrator = Orchestrator()

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
    app.include_router(core_router)

    if swarm_router is not None:
        app.include_router(swarm_router)
    if memory_router is not None:
        app.include_router(memory_router)
    if tools_router is not None:
        app.include_router(tools_router)
    if model_routing_router is not None:
        app.include_router(model_routing_router)
    if agent_router is not None:
        app.include_router(agent_router)
    if evolution_router is not None:
        app.include_router(evolution_router)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/api/backend-health", include_in_schema=False)
    def backend_health_legacy():
        refresh_backend_health()
        avg_latency = sum(backend_health.latency_history_ms) / max(1, len(backend_health.latency_history_ms))
        return {
            "ollama_reachable": backend_health.ollama_ok,
            "consecutive_failures": backend_health.consecutive_failures,
            "total_failures": backend_health.failure_count,
            "last_error": backend_health.last_error_message,
            "avg_latency_ms": round(avg_latency, 2),
            "timestamp": backend_health.last_check_time,
        }

    return _install_legacy_aliases(app)


app = create_app()









