from __future__ import annotations

import os
import time
from typing import Any

_QUERY_CACHE = {}
_CACHE_TTL = 300  # 5 minutes

async def search(collection: str, query: str, top_k: int = 5) -> list[Any]:
    """
    Qdrant-backed search helper used by MCP qdrant_recall.

    Requires:
      - qdrant-client installed
      - QDRANT_URL set
      - optional QDRANT_API_KEY set
    """
    cache_key = f"{collection}:{query}:{top_k}"
    if cache_key in _QUERY_CACHE:
        cached_result, timestamp = _QUERY_CACHE[cache_key]
        if time.time() - timestamp < _CACHE_TTL:
            return cached_result
            
    try:
        from qdrant_client import AsyncQdrantClient
    except Exception:
        return []

    qdrant_url = os.getenv("QDRANT_URL")
    if not qdrant_url:
        return []

    api_key = os.getenv("QDRANT_API_KEY")
    client = AsyncQdrantClient(url=qdrant_url, api_key=api_key)

    try:
        response = await client.query_points(
            collection_name=collection,
            query_text=query,
            limit=top_k,
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
    except Exception:
        return []

