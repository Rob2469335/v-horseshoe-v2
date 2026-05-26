# lib/vector/reranker.py
"""
Reranking layer using bge-reranker-v2-m3 via Ollama.
Sits between Qdrant retrieval and context assembly.
Pipeline: query -> Qdrant top-20 -> bge-reranker -> top-5 -> Swarm
"""
import logging
import httpx
from config.settings import RERANK_MODEL, OLLAMA_BASE_URL

log = logging.getLogger("reranker")


async def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """
    Score each candidate against the query using bge-reranker.
    Falls back to original Qdrant order if reranker is unavailable.
    """
    if not candidates:
        return []

    texts = [c["payload"].get("text", "") for c in candidates]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/embed",
                json={
                    "model": RERANK_MODEL,
                    "input": [f"query: {query}"] + [f"passage: {t}" for t in texts],
                },
            )
            resp.raise_for_status()
            embeddings = resp.json()["embeddings"]

        # Cosine similarity between query embedding and each passage
        import math
        query_vec = embeddings[0]
        scored = []
        for i, candidate in enumerate(candidates):
            passage_vec = embeddings[i + 1]
            dot = sum(a * b for a, b in zip(query_vec, passage_vec))
            mag_q = math.sqrt(sum(x**2 for x in query_vec))
            mag_p = math.sqrt(sum(x**2 for x in passage_vec))
            score = dot / (mag_q * mag_p) if mag_q and mag_p else 0.0
            scored.append({**candidate, "rerank_score": score})

        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        log.debug(f"Reranked {len(candidates)} candidates -> top {top_k}")
        return scored[:top_k]

    except Exception as e:
        log.warning(f"Reranker unavailable ({e}), using Qdrant order")
        return candidates[:top_k]
