# swarm_os/api/schemas.py - Complete API Schema Registry
from __future__ import annotations

from pydantic import BaseModel
from typing import Any, Dict, List, Optional

# --- Existing Core Orchestration Schemas ---
class GenerateRequest(BaseModel):
    model: str
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
    capability: str  # "chat_search", "upwork_analyzer", "vscode_automation"
    payload: Dict[str, Any]  # Request model data (e.g., {"query": "..."} for chat_search)
    cache_key: Optional[str] = None  # Optional cache key for result caching

class ToolExecuteResponse(BaseModel):
    """Response from capability tool execution."""
    status: str  # "success", "executed", "rejected", "failed", "error"
    capability: str
    data: Dict[str, Any]  # Full response data from handler
    message: Optional[str] = None
    command: Optional[str] = None  # For vscode_automation
    exit_code: Optional[int] = None  # For vscode_automation
    stdout: Optional[str] = None  # For vscode_automation
    stderr: Optional[str] = None  # For vscode_automation

class ToolListResponse(BaseModel):
    """Response listing available capabilities."""
    capabilities: List[str]
    count: int

class CacheStatusResponse(BaseModel):
    """Response showing cache status."""
    cache_size: int
    cached_keys: List[str]
