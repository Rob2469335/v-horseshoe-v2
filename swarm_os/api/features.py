# api/routes/features.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class QueryRequest(BaseModel):
    query: str
    collection: str = "chat_archive"
    top_k: int = 5

@router.post("/search")
async def semantic_search(req: QueryRequest):
    """Query Qdrant via the memory pipeline and return reranked results."""
    from lib.vector.qdrant_store import search
    from lib.vector.reranker import rerank
    from config.settings import QDRANT_RETRIEVE_TOP_K, RERANKER_ENABLED

    candidates = await search(req.collection, req.query, top_k=QDRANT_RETRIEVE_TOP_K)
    if RERANKER_ENABLED and candidates:
        results = await rerank(req.query, candidates, top_k=req.top_k)
    else:
        results = candidates[:req.top_k]
    return {"results": results}
