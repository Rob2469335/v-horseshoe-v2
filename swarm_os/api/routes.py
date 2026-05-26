from __future__ import annotations
import logging
from fastapi import APIRouter, Depends, FastAPI
from swarm_os.api.schemas import (
    AssignRequest, AssignResponse,
    GenerateRequest, GenerateResponse,
    StatusResponse,
    ToolExecuteRequest, ToolExecuteResponse,
    ToolListResponse,
    CacheStatusResponse,
)
from swarm_os.core.settings import get_settings
from swarm_os.services.status import get_status
from swarm_os.services.orchestrator import Orchestrator

log = logging.getLogger(__name__)
router = APIRouter()

def get_orchestrator(app: FastAPI = Depends(lambda: app)) -> Orchestrator:
    """Get Orchestrator from app.state."""
    orch = getattr(app.state, 'orchestrator', None)
    if orch is None:
        raise RuntimeError('Orchestrator not initialized')
    return orch

_runtime_instance = None

def get_runtime():
    """Singleton dependency for AgentRuntime."""
    global _runtime_instance
    if _runtime_instance is None:
        from swarm_os.agent_runtime import AgentRuntime
        _runtime_instance = AgentRuntime()
    return _runtime_instance

@router.get('/health')
def health() -> dict[str, bool]:
    return {'ok': True}

@router.get('/readyz')
def readyz() -> dict[str, bool]:
    return {'ready': True}

@router.get('/status', response_model=StatusResponse)
def status(orch: Orchestrator = Depends(get_orchestrator)) -> StatusResponse:
    s = get_settings()
    st = get_status(s.events_dir)
    return StatusResponse(
        ready=st.ready,
        events_path=str(st.events_path),
        event_count=st.event_count,
        ollama_base_url=s.ollama_base_url,
        environment=s.environment,
        ollama_reachable=orch.ollama.is_reachable(),
    )


@router.get("/tools", response_model=ToolListResponse)
def list_tools(runtime = Depends(get_runtime)) -> ToolListResponse:
    tools = runtime.list_tools()
    return ToolListResponse(capabilities=tools, count=len(tools))

@router.get("/tools/cache", response_model=CacheStatusResponse)
def get_cache_status(runtime = Depends(get_runtime)) -> CacheStatusResponse:
    cache_size = runtime.get_tool_cache_size()
    return CacheStatusResponse(cache_size=cache_size, cached_keys=[])

@router.post("/tools/execute", response_model=ToolExecuteResponse)
async def execute_tool(
    payload: ToolExecuteRequest,
    runtime = Depends(get_runtime)
) -> ToolExecuteResponse:
    try:
        from swarm_os.capabilities.models import (
            ChatSearchRequest, UpworkAnalysisRequest, VSCodeAutomationRequest,
        )
        
        capability = payload.capability.lower().strip()
        
        if capability == "chat_search":
            req_payload = ChatSearchRequest(**payload.payload)
        elif capability == "upwork_analyzer":
            req_payload = UpworkAnalysisRequest(**payload.payload)
        elif capability == "vscode_automation":
            req_payload = VSCodeAutomationRequest(**payload.payload)
        else:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail=f"Unknown capability: {capability}. Available: {runtime.list_tools()}"
            )
        
        result = await runtime.call_tool(capability, req_payload, cache_key=payload.cache_key)
        
        return ToolExecuteResponse(
            status=result.status,
            capability=capability,
            data=result.model_dump() if hasattr(result, "model_dump") else str(result),
            message=getattr(result, "message", None),
            command=getattr(result, "command", None),
            exit_code=getattr(result, "exit_code", None),
            stdout=getattr(result, "stdout", None),
            stderr=getattr(result, "stderr", None),
        )
        
    except Exception as e:
        from fastapi import HTTPException
        log.exception("Tool execution failed")
        raise HTTPException(status_code=500, detail=str(e))


def get_swarm_stats(orch: Orchestrator = Depends(get_orchestrator)):
    population = orch.memory.get_population()
    if not population:
        return {'status': 'idling', 'count': 0}
        
    top_org = sorted(population, key=lambda x: getattr(x, 'fitness', 0.0), reverse=True)[0]
    
    return {
        'population_size': len(population),
        'best_fitness': getattr(top_org, 'fitness', 0.0),
        'best_agent_id': getattr(top_org, 'id', 'unknown'),
        'active_generation': getattr(top_org, 'generation', 0)
    }



