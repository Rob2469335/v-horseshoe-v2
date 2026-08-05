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

_rerank_client: httpx.AsyncClient | None = None


def _get_rerank_client() -> httpx.AsyncClient:
    global _rerank_client
    if _rerank_client is None:
        _rerank_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
            headers={"Authorization": "Bearer llama"},
        )
    return _rerank_client


async def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """
    Score each candidate against the query using the dedicated llama-server reranker API.
    Falls back to original Qdrant order if reranker is unavailable.
    """
    if not candidates:
        return []

    texts = [c["payload"].get("text", "") for c in candidates]

    try:
        client = _get_rerank_client()
        resp = await client.post(
            f"{RERANK_URL}/v1/rerank",
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

