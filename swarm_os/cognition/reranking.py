# lib/vector/reranker.py
"""
Reranking layer using qllama-bge-reranker-v2-m3-latest via llama-server (Port 8082).
Sits between Qdrant retrieval and context assembly.
Pipeline: query -> Qdrant top-20 -> llama-server reranker -> top-5 -> Swarm
"""
import logging
import httpx

log = logging.getLogger("reranker")

RERANK_URL = "http://127.0.0.1:8082"
RERANK_MODEL = "qllama-bge-reranker-v2-m3-latest.gguf"

async def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """
    Score each candidate against the query using the dedicated llama-server reranker API.
    Falls back to original Qdrant order if reranker is unavailable.
    """
    if not candidates:
        return []

    texts = [c["payload"].get("text", "") for c in candidates]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{RERANK_URL}/v1/rerank",
                headers={"Authorization": "Bearer llama"},
                json={
                    "model": RERANK_MODEL,
                    "query": query,
                    "documents": texts,
                    "top_n": top_k
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])

        scored = []
        for res in results:
            idx = res.get("index")
            if idx is not None and idx < len(candidates):
                candidate = candidates[idx]
                scored.append({**candidate, "rerank_score": res.get("relevance_score", 0.0)})

        log.debug(f"Reranked {len(candidates)} candidates -> top {len(scored)}")
        return scored

    except Exception as e:
        log.warning(f"Reranker unavailable ({e}), using Qdrant order")
        return candidates[:top_k]

