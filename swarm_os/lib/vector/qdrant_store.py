"""
qdrant_store.py - Dense-vector search over Qdrant collections.

Embeds the query with the local nomic-embed service (:8081) and searches the
target collection by vector. Previously this module used Qdrant's
`query_text` (text-search), which only works if the collection has a text-based
query model configured — the live collections are 768-dim dense-vector stores,
so text-search silently returned nothing and the /features/search endpoint fell
through to a misleading 503.
"""
from __future__ import annotations

import os
import time
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EMBED_URL = "http://127.0.0.1:8081/v1"
EMBED_DIM = 768  # nomic-embed-text dimension
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")

_QUERY_CACHE: dict = {}
_CACHE_TTL = 300  # 5 minutes

_embed_client: httpx.AsyncClient | None = None


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None or _embed_client.is_closed:
        _embed_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _embed_client


async def _embed(text: str) -> list[float]:
    """Embed a query via the local nomic-embed service (:8081)."""
    try:
        resp = await _get_embed_client().post(
            f"{EMBED_URL}/embeddings",
            headers={"Authorization": "Bearer llama"},
            json={"input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as exc:
        logger.warning("Embed failed for query (%s)", exc)
        return [0.0] * EMBED_DIM


async def search(collection: str, query: str, top_k: int = 5) -> list[Any]:
    """
    Qdrant dense-vector search used by MCP qdrant_recall and /features/search.

    Requires qdrant-client and a running :8081 embedding server. On any failure
    returns [] so callers degrade gracefully (never raise).
    """
    cache_key = f"{collection}:{query}:{top_k}"
    if cache_key in _QUERY_CACHE:
        cached_result, timestamp = _QUERY_CACHE[cache_key]
        if time.time() - timestamp < _CACHE_TTL:
            return cached_result

    vector = await _embed(query)
    if not any(vector):
        return []

    try:
        from qdrant_client import AsyncQdrantClient
    except Exception:
        return []

    client = AsyncQdrantClient(url=QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY"))
    try:
        response = await client.query_points(
            collection_name=collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
        points = getattr(response, "points", response)
        results = []
        for point in points or []:
            results.append({
                "id": getattr(point, "id", None),
                "score": getattr(point, "score", None),
                "payload": getattr(point, "payload", None),
            })

        _QUERY_CACHE[cache_key] = (results, time.time())
        return results
    except Exception as exc:
        logger.warning("Qdrant search failed on %s (%s)", collection, exc)
        return []
