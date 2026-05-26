from __future__ import annotations

import os
from typing import Any

async def search(collection: str, query: str, top_k: int = 5) -> list[Any]:
    """
    Qdrant-backed search helper used by MCP qdrant_recall.

    Requires:
      - qdrant-client installed
      - QDRANT_URL set
      - optional QDRANT_API_KEY set
    """
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
        return results
    except Exception:
        return []
