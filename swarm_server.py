from __future__ import annotations

from fastapi import APIRouter
from swarm_os.core.settings import get_settings
from swarm_os.api.schemas import GenerateRequest, AssignRequest
from swarm_os.domain.models import SwarmJob, SwarmNode
from swarm_os.services.events import write_event
from pathlib import Path
from swarm_os.services.status import get_status

# Remove: orch = Orchestrator()  # This was creating an orphaned instance!
# The Orchestrator is now created in FastAPI lifespan and accessed via dependency injection

router = APIRouter()

@router.get('/health')
def health() -> dict[str, bool]:
    return {'ok': True}

@router.get('/readyz')
def readyz() -> dict[str, bool]:
    return {'ready': True}

@router.get('/status')
def status() -> dict[str, object]:
    s = get_settings()
    st = get_status(s.events_dir)
    return {
        'ready': st.ready,
        'events_path': str(st.events_path),
        'event_count': st.event_count,
        'ollama_base_url': s.ollama_base_url,
    }

# Note: /generate and /assign endpoints moved to swarm_os/api/routes.py
# They now use the Orchestrator from app.state via dependency injection
