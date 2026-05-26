# swarm_os/api/features.py
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/features", tags=["features"])


class QueryRequest(BaseModel):
    query:      str
    collection: str = "chat_archive"
    top_k:      int = 5


@router.post("/search")
async def semantic_search(req: QueryRequest):
    """Query Qdrant via the memory pipeline and return reranked results."""
    try:
        from ..lib.vector.qdrant_store import search
        from ..lib.vector.reranker import rerank
        from ..core.settings import get_settings

        s          = get_settings()
        top_k_qdrant = getattr(s, "qdrant_retrieve_top_k", 20)
        reranker_on  = getattr(s, "reranker_enabled", True)

        candidates = await search(req.collection, req.query, top_k=top_k_qdrant)
        if reranker_on and candidates:
            results = await rerank(req.query, candidates, top_k=req.top_k)
        else:
            results = candidates[:req.top_k]
        return {"results": results}
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Vector search not yet configured. lib/vector modules are empty stubs."
        )
    except Exception as e:
        log.exception("semantic_search failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat-search")
async def chat_search_status():
    return {"status": "stub", "message": "chat_search handler not yet implemented"}


@router.get("/upwork")
async def upwork_status():
    return {"status": "stub", "message": "upwork_analyzer handler not yet implemented"}


@router.get("/vscode")
async def vscode_status():
    return {"status": "stub", "message": "vscode_automation handler not yet implemented"}
