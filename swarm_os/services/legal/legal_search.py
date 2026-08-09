"""Hybrid legal retrieval for Rob's Lawyer.

Milestone 2: search the `legal_statutes` collection (populated by corpus_ingest)
using the repo's proven hybrid pattern:
  1. DENSE search — embed the query via :8081, query Qdrant by vector with an
     optional jurisdiction filter (ny/nj/ga/nc/federal).
  2. RERANK — pass the coarse candidates through the :8082 cross-encoder.

Degradation is graceful end-to-end (mirrors qdrant_store/reranker): a reranker
outage returns the dense ordering; an embed failure returns []. The endpoint
never raises for a degraded dependency — only for genuinely malformed input.
"""
from __future__ import annotations

import logging
from typing import Any

from swarm_os.lib.vector.qdrant_store import search as dense_search
from swarm_os.lib.vector.reranker import rerank

log = logging.getLogger(__name__)

COLLECTION = "legal_statutes"
JURISDICTIONS = ("ny", "nj", "ga", "nc", "federal")


async def search_statutes(query: str, jurisdiction: str | None = None,
                          top_k: int = 8) -> list[dict[str, Any]]:
    """Hybrid statute search.

    - `jurisdiction` filters to one of ny/nj/ga/nc/federal (None = all scoped).
    - `top_k` is the final reranked count (dense pulls a wider net first).
    Returns a list of {id, score, rerank_score, payload} dicts, best first.
    Never raises — degraded deps return [] or the dense order.
    """
    if not query or not query.strip():
        return []
    if jurisdiction is not None and jurisdiction not in JURISDICTIONS:
        raise ValueError(f"jurisdiction must be one of {JURISDICTIONS}, got {jurisdiction!r}")

    dense_net = max(top_k * 4, 16)  # pull a wider net than the final count
    dense = await _search_with_filter(query, jurisdiction, dense_net)
    if not dense:
        return []

    candidates = [_payload_result(p) for p in dense]
    return await rerank(query, candidates, top_k=top_k)


async def _search_with_filter(query: str, jurisdiction: str | None, top_k: int) -> list[dict]:
    """Dense search over legal_statutes with an optional jurisdiction filter.

    The repo's qdrant_store.search doesn't accept a filter, so we implement the
    filtered variant here (same embed + query_points shape, adding the payload
    filter when a jurisdiction is requested)."""
    if not jurisdiction:
        return await dense_search(COLLECTION, query, top_k)

    try:
        from qdrant_client import AsyncQdrantClient
    except Exception as exc:
        log.warning("qdrant_client unavailable: %s", exc)
        return []

    import os
    from swarm_os.lib.vector.qdrant_store import _embed, QDRANT_URL

    vector = await _embed(query)
    if not any(vector):
        return []

    client = AsyncQdrantClient(url=QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY"))
    try:
        response = await client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=top_k,
            with_payload=True,
            query_filter={"must": [{"key": "jurisdiction", "match": {"value": jurisdiction}}]},
        )
        points = getattr(response, "points", response)
        return [
            {"id": getattr(p, "id", None), "score": getattr(p, "score", None),
             "payload": getattr(p, "payload", None)}
            for p in (points or [])
        ]
    except Exception as exc:
        log.warning("filtered Qdrant search failed on %s (%s)", COLLECTION, exc)
        return []
    finally:
        await client.close()


def _payload_result(point: dict) -> dict[str, Any]:
    """Shape a dense-search point into a rerank candidate carrying the payload
    through (the reranker's _candidate_text reads content from the payload)."""
    payload = point.get("payload") or {}
    return {
        "id": point.get("id"),
        "score": point.get("score"),
        "citation": payload.get("citation", ""),
        "section_title": payload.get("section_title", ""),
        "content": payload.get("content", ""),
        "jurisdiction": payload.get("jurisdiction", ""),
        "display_path": payload.get("display_path", ""),
        "act_id": payload.get("act_id", ""),
    }
