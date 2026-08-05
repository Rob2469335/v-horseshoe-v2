# swarm_os/api/agents.py
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(tags=["agents"])

class AgentCreatePayload(BaseModel):
    agent_id: str
    config: Dict[str, Any] | None = None

class AgentStepPayload(BaseModel):
    prompt: str
    tools: List[str] | None = None
    history: List[Dict[str, Any]] | None = None
    focus_file: str | None = None
    delegation_chain: List[str] | None = None

class ToolCallPayload(BaseModel):
    payload: Dict[str, Any] | None = None

class ModelOverridePayload(BaseModel):
    model_name: str
    backend: str

@router.get("/models/cloud")
async def get_cloud_models():
    from runtime_v2.services.fallback_manager import get_live_fallbacks
    fallbacks = await get_live_fallbacks()
    return {"models": fallbacks}

@router.post("/agents/{agent_id}/model")
def override_agent_model(agent_id: str, payload: ModelOverridePayload):
    from runtime_v2.services.model_registry import AGENT_MODELS, save_overrides
    AGENT_MODELS[agent_id] = (payload.model_name, payload.backend)
    save_overrides()
    return {"status": "ok", "agent_id": agent_id, "model": payload.model_name}

@router.get("/agents/models")
def get_agent_models():
    from runtime_v2.services.model_registry import AGENT_MODELS
    return {k: {"model": v[0], "backend": v[1]} for k, v in AGENT_MODELS.items()}

import logging
logger = logging.getLogger(__name__)

def get_agent_service(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None or not hasattr(runtime, "agents") or runtime.agents is None:
        raise HTTPException(status_code=503, detail="Agent service unavailable")
    return runtime.agents

@router.get("/agents")
def list_agents(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is not None and hasattr(runtime, "agents") and runtime.agents is not None:
        if hasattr(runtime.agents, "list_agents"):
            return runtime.agents.list_agents()
    
    # Safe fallback response if service fails
    return [
        {
            "id": "coordinator",
            "role": "coordinator",
            "description": "Supreme orchestrator. Analyzes intent, delegates to specialists, synthesizes final response. (Fallback Mode)",
            "model_role": "reasoning",
            "config": {}
        },
        {
            "id": "planner",
            "role": "planner",
            "description": "Decomposes complex tasks into ordered execution steps with dependencies and success criteria. (Fallback Mode)",
            "model_role": "reasoning",
            "config": {}
        },
        {
            "id": "executor",
            "role": "executor",
            "description": "Executes plan steps autonomously using tools. Reads files, runs searches, writes code, patches files. (Fallback Mode)",
            "model_role": "fast",
            "config": {}
        },
        {
            "id": "tool-runner",
            "role": "tool-runner",
            "description": "Specialized agent for executing capability and tool calls. (Fallback Mode)",
            "model_role": "fast",
            "config": {}
        },
        {
            "id": "reviewer",
            "role": "reviewer",
            "description": "Audits code and proposals, finds bugs and design flaws. (Fallback Mode)",
            "model_role": "reasoning",
            "config": {}
        },
        {
            "id": "coder",
            "role": "coder",
            "description": "Code-writing specialist focusing on high-quality modifications. (Fallback Mode)",
            "model_role": "fast",
            "config": {}
        },
        {
            "id": "researcher",
            "role": "researcher",
            "description": "Gathers context via web and codebase search. (Fallback Mode)",
            "model_role": "fast",
            "config": {}
        },
        {
            "id": "debugger",
            "role": "debugger",
            "description": "Diagnoses failures and routes fixes. (Fallback Mode)",
            "model_role": "coding",
            "config": {}
        },
        {
            "id": "tool-maker",
            "role": "tool-maker",
            "description": "Creates custom MCP servers in Python. (Fallback Mode)",
            "model_role": "coding",
            "config": {}
        },
        {
            "id": "code_analyzer",
            "role": "code_analyzer",
            "description": "Systematically finds bugs and proposes improvements. (Fallback Mode)",
            "model_role": "reasoning",
            "config": {}
        }
    ]

@router.post("/agents")
def create_agent(payload: AgentCreatePayload, request: Request):
    try:
        service = get_agent_service(request)
        service.register_agent(payload.agent_id, payload.config)
        return {"status": "created", "agent_id": payload.agent_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Agent registration failed")
        raise HTTPException(status_code=400, detail="Agent registration failed")

@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, service=Depends(get_agent_service)):
    try:
        return service.get_agent(agent_id)
    except KeyError as exc:
        logger.warning("Unknown agent requested: %s", agent_id)
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")

@router.post("/agents/{agent_id}/step")
async def step_agent(agent_id: str, payload: AgentStepPayload, service=Depends(get_agent_service)):
    try:
        import asyncio
        chunks = []
        # BUG FIX: Add timeout to prevent this endpoint from holding connections open indefinitely.
        # Long-running agents can block this connection for minutes with no response to the client.
        # Use the streaming endpoint for real-time feedback; this endpoint is for quick steps only.
        try:
            async with asyncio.timeout(300):
                async for chunk in service.step_agent_stream(agent_id, payload.prompt, payload.history or []):
                    chunks.append(chunk)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Agent step timed out after 300s — use the /stream endpoint for long-running tasks")
        return chunks
    except HTTPException:
        raise
    except KeyError as exc:
        log.warning("Agent step failed: unknown agent %s", agent_id)
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")

@router.post("/agents/{agent_id}/step/stream")
async def step_agent_stream(agent_id: str, payload: AgentStepPayload, request: Request):
    """
    Streaming endpoint consumed by the CLI's stream_prompt().
    Emits newline-delimited JSON chunks.
    """
    runtime = getattr(request.app.state, "runtime", None)

    async def generate() -> AsyncGenerator[str, None]:
        try:
            if runtime is None or runtime.agents is None:
                yield json.dumps({"content": "Agent service unavailable"}) + "\n"
                yield json.dumps({"type": "final", "done": True}) + "\n"
                return

            async for chunk in runtime.agents.step_agent_stream(
                agent_id,
                payload.prompt,
                payload.history or [],
                payload.delegation_chain,
            ):
                yield json.dumps(chunk) + "\n"

            yield json.dumps({"type": "final", "done": True}) + "\n"

        except Exception as exc:
            # BUG FIX: Log the full exception so errors aren't silently swallowed.
            # Previously the exception was yielded as a string but not logged,
            # making it impossible to debug production streaming failures.
            logger.exception("step_agent_stream failed for agent_id=%s", agent_id)
            yield json.dumps({"type": "error", "error": "Agent step failed"}) + "\n"
            yield json.dumps({"type": "final", "done": True}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")

@router.post("/agents/{agent_id}/tools/{tool_name}")
async def call_agent_tool(agent_id: str, tool_name: str, payload: ToolCallPayload, service=Depends(get_agent_service)):
    try:
        return await service.run_tool(agent_id, tool_name, payload.payload)
    except KeyError as exc:
        logger.warning("Agent tool call on unknown agent: %s", agent_id)
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'")

@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, service=Depends(get_agent_service)):
    try:
        service.remove_agent(agent_id)
        return {"status": "deleted", "agent_id": agent_id}
    except Exception as exc:
        logger.exception("Agent delete failed for %s", agent_id)
        raise HTTPException(status_code=400, detail="Agent delete failed")
