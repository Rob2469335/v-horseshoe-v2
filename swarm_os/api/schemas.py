# swarm_os/api/schemas.py - Complete API Schema Registry
from __future__ import annotations

from pydantic import BaseModel
from typing import Any, Dict, List, Optional

# --- Existing Core Orchestration Schemas ---
class GenerateRequest(BaseModel):
    model: Optional[str] = None
    prompt: str

class GenerateResponse(BaseModel):
    response: str
    model: str

class AssignRequest(BaseModel):
    node: Dict[str, Any]
    job: Dict[str, Any]

class AssignResponse(BaseModel):
    accepted: bool
    node_id: str
    job_id: str
    score: int

class StatusResponse(BaseModel):
    ready: bool
    events_path: str
    event_count: int
    ollama_base_url: str
    environment: str
    ollama_reachable: bool

# --- New Capability Tool Schemas ---
class ToolExecuteRequest(BaseModel):
    """Request to execute a capability tool."""
    capability: str
    payload: Dict[str, Any]
    cache_key: Optional[str] = None

class ToolExecuteResponse(BaseModel):
    """Response from capability tool execution."""
    status: str
    capability: str
    data: Dict[str, Any]
    message: Optional[str] = None
    command: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None

class ToolListResponse(BaseModel):
    """Response listing available capabilities."""
    capabilities: List[str]
    count: int

class CacheStatusResponse(BaseModel):
    """Response showing cache status."""
    cache_size: int
    cached_keys: List[str]
