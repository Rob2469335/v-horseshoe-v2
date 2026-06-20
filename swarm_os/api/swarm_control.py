# swarm_os/api/swarm_control.py

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from swarm_os.core.orchestrator_v10 import orchestrator, Patch

router = APIRouter()

class PatchRequest(BaseModel):
    id: str
    reason: str
    diff: str
    confidence: Optional[float] = 0.9

@router.post("/swarm/v10/run")
async def run_swarm_patch(request: PatchRequest, background_tasks: BackgroundTasks):
    """
    Triggers a Swarm V10 patch cycle.
    Runs in background to allow SSE stream to capture events.
    """
    patch = Patch(id=request.id, reason=request.reason, diff=request.diff)
    
    # Run in background so the request returns immediately
    background_tasks.add_task(orchestrator.run_cycle, patch, request.confidence)
    
    return {"status": "started", "patch_id": request.id}
