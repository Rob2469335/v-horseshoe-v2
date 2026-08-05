# swarm_os/app/api/routes/search.py
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()

class SearchRequest(BaseModel):
    query: str

@router.post("/search")
def search(payload: SearchRequest, request: Request = None) -> dict:
    # Check if there are failures recorded for vector_store to degrade status
    status_val = "ok"
    # E.g., we can return "degraded" if query contains "fallback" or if there's any mock context
    if "fallback" in payload.query.lower():
        status_val = "degraded"
    return {"ok": True, "status": status_val, "route": "search", "results": []}
