import os
import logging
from typing import Any, Dict, Optional
import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class SubagentRequest(BaseModel):
    agent_id: str
    prompt: str
    history: Optional[list] = None

class SubagentHandler:
    """
    Spawns and executes a subagent asynchronously using the Swarm OS backend REST API.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.backend_url = os.environ.get("ZENITH_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

    async def execute(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            agent_id = payload.get("agent_id", "executor")
            prompt = payload.get("prompt", "")
            history = payload.get("history", [])
        else:
            agent_id = getattr(payload, "agent_id", "executor")
            prompt = getattr(payload, "prompt", "")
            history = getattr(payload, "history", []) or []

        if not prompt:
            return {"status": "error", "message": "Prompt is required for subagent execution"}

        logger.info(f"Spawning subagent '{agent_id}' to execute subtask...")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                url = f"{self.backend_url}/agents/{agent_id}/step"
                resp = await client.post(
                    url,
                    json={
                        "prompt": prompt,
                        "history": history
                    }
                )
                if resp.status_code != 200:
                    return {
                        "status": "error",
                        "message": f"Subagent execution failed with HTTP status {resp.status_code}: {resp.text}"
                    }
                
                chunks = resp.json()
                final_content = ""
                for chunk in chunks:
                    if isinstance(chunk, dict):
                        final_content += chunk.get("content", "") or chunk.get("thinking", "")
                
                return {
                    "status": "success",
                    "agent_id": agent_id,
                    "prompt": prompt,
                    "content": final_content.strip()
                }
        except Exception as e:
            logger.error(f"Failed to execute subagent: {e}")
            return {"status": "error", "message": f"Subagent execution crashed: {e}"}
