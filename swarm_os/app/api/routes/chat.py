# swarm_os/app/api/routes/chat.py
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

@router.post("/chat")
def chat(payload: ChatRequest, request: Request = None) -> dict:
    if not payload.message:
        return {
            "ok": False,
            "repair": {
                "status": "triggered",
                "component": "chat_model",
                "action": "retry_request"
            }
        }
    return {"ok": True, "route": "chat", "response": f"echo: {payload.message}"}
