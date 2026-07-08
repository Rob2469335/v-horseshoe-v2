# swarm_os/api/schemas.py - Complete API Schema Registry
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# --- Existing Core Orchestration Schemas ---
class GenerateRequest(BaseModel):
    model: Optional[str] = None
    prompt: str

class GenerateResponse(BaseModel):
    content: str
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
    vision_configured: bool = False
    vision_runtime_available: bool = False
    vision_tool_exposed: bool = False
    vision_models_configured: List[str] = Field(default_factory=list)
    vision_models_installed: List[str] = Field(default_factory=list)
    installed_model_count: int = 0
    installed_models: List[str] = Field(default_factory=list)
    primary_vision_model: Optional[str] = None

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
    vision_configured: bool = False
    vision_runtime_available: bool = False
    vision_tool_exposed: bool = False
    vision_models_configured: List[str] = Field(default_factory=list)
    vision_models_installed: List[str] = Field(default_factory=list)

class CacheStatusResponse(BaseModel):
    """Response showing cache status."""
    cache_size: int
    cached_keys: List[str]

class LearningOutcomeResponse(BaseModel):
    task: Optional[str] = None
    tool_path: List[str] = Field(default_factory=list)
    result: Optional[str] = None
    confidence: Optional[float] = None
    correction: Optional[str] = None
    approved: Optional[bool] = None
    reason_code: Optional[str] = None

class TimelinePointResponse(BaseModel):
    bucket: str
    event_count: int
    success_count: int = 0
    partial_count: int = 0
    fail_count: int = 0

class TimelineResponse(BaseModel):
    window_minutes: int
    points: List[TimelinePointResponse] = Field(default_factory=list)

class AutoAssignResponse(BaseModel):
    mapping: Dict[str, str]

