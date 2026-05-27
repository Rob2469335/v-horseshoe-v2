from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, HTTPException
from swarm_os.api.schemas import (
    AssignRequest, AssignResponse, GenerateRequest, GenerateResponse,
    StatusResponse, ToolExecuteRequest, ToolExecuteResponse,
    ToolListResponse, CacheStatusResponse,
)
from swarm_os.core.settings import get_settings
from swarm_os.services.status import get_status
from swarm_os.services.orchestrator import Orchestrator
from swarm_os.api.health import router as health_router
from swarm_os.api.admin import router as admin_router
from swarm_os.api.dashboard import router as dashboard_router
from swarm_os.api.explorer import router as explorer_router

log = logging.getLogger(__name__)
router = APIRouter()
router.include_router(health_router)
router.include_router(admin_router, prefix="/api")
router.include_router(dashboard_router, prefix="/api")
router.include_router(explorer_router, prefix="/api")

_orch = None
def get_orchestrator():
    global _orch
    if _orch is None:
        _orch = Orchestrator()
    return _orch

_rt = None
def get_runtime():
    global _rt
    if _rt is None:
        from swarm_os.agent_runtime import AgentRuntime
        _rt = AgentRuntime()
    return _rt

@router.get("/readyz")
def readyz():
    return {"ready": True}

@router.get("/status", response_model=StatusResponse)
def status():
    s = get_settings()
    st = get_status(s.events_dir)
    return StatusResponse(
        ready=st.ready, events_path=str(st.events_path),
        event_count=st.event_count, ollama_base_url=s.ollama_base_url,
        environment=s.environment, ollama_reachable=get_orchestrator().ollama.is_reachable(),
    )

@router.get("/events")
def events():
    all_ev = get_orchestrator().events.read_all()
    return {"count": len(all_ev), "events": all_ev[-50:]}

@router.get("/tools", response_model=ToolListResponse)
def list_tools(runtime=Depends(get_runtime)):
    tools = runtime.list_tools()
    return ToolListResponse(capabilities=tools, count=len(tools))

@router.get("/tools/cache", response_model=CacheStatusResponse)
def cache_status(runtime=Depends(get_runtime)):
    return CacheStatusResponse(cache_size=runtime.get_tool_cache_size(), cached_keys=[])

@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(payload: ToolExecuteRequest, runtime=Depends(get_runtime)):
    try:
        from swarm_os.capabilities.models import (
            ChatSearchRequest, UpworkAnalysisRequest, VSCodeAutomationRequest)
        cap = payload.capability.lower().strip()
        if cap == "chat_search":
            req = ChatSearchRequest(**payload.payload)
        elif cap == "upwork_analyzer":
            req = UpworkAnalysisRequest(**payload.payload)
        elif cap == "vscode_automation":
            req = VSCodeAutomationRequest(**payload.payload)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown capability: {cap}")
        result = await runtime.call_tool(cap, req, cache_key=payload.cache_key)
        return ToolExecuteResponse(
            status=result.status, capability=cap,
            data=result.model_dump() if hasattr(result, "model_dump") else str(result),
            message=getattr(result, "message", None),
            command=getattr(result, "command", None),
            exit_code=getattr(result, "exit_code", None),
            stdout=getattr(result, "stdout", None),
            stderr=getattr(result, "stderr", None),
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Tool execution failed")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate", response_model=GenerateResponse)
def generate(payload: GenerateRequest):
    try:
        result = get_orchestrator().generate(model=payload.model, prompt=payload.prompt)
        return GenerateResponse(response=result, model=payload.model)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.post("/assign", response_model=AssignResponse)
def assign(payload: AssignRequest):
    from swarm_os.domain.models import SwarmJob, SwarmNode
    from swarm_os.domain.policies import score_node
    node = SwarmNode(**payload.node.model_dump())
    job  = SwarmJob(**payload.job.model_dump())
    accepted = get_orchestrator().assign_job(node, job)
    return AssignResponse(accepted=accepted, node_id=node.node_id,
                          job_id=job.job_id, score=score_node(node, job))

@router.get("/swarm-stats")
def swarm_stats():
    try:
        orch = get_orchestrator()
        pop = orch.memory.get_population() if hasattr(orch, "memory") else []
        if not pop:
            return {"status": "idling", "population_size": 0,
                    "best_fitness": 0.0, "best_agent_id": "none", "active_generation": 0}
        top = sorted(pop, key=lambda x: getattr(x, "fitness", 0.0), reverse=True)[0]
        return {"population_size": len(pop),
                "best_fitness": round(getattr(top, "fitness", 0.0), 4),
                "best_agent_id": getattr(top, "id", "unknown"),
                "active_generation": getattr(top, "generation", 0)}
    except Exception:
        return {"status": "idling", "population_size": 0,
                "best_fitness": 0.0, "best_agent_id": "none", "active_generation": 0}
