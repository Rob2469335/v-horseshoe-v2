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

# Cases (case_corpus.py): a curated manifest of the authorities the operator's
# appeal actually turned on, chunked into legal_cases. Same hybrid retrieval,
# but the payload filters are tier/circuit/batson instead of jurisdiction.
COLLECTION_CASES = "legal_cases"
TIERS = (1, 2, 3, 4)


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


async def search_cases(query: str, tier: int | None = None,
                       circuit: str | None = None, batson: bool | None = None,
                       top_k: int = 8) -> list[dict[str, Any]]:
    """Hybrid case-law search over the `legal_cases` collection.

    - `tier` filters to a manifest tier (1 controlling / 2 backbone / 3 context /
      4 Batson); `circuit` filters to a circuit string ("2d"/"scotus"/...);
      `batson` filters the Batson-authority subset (True/False).
    - `top_k` is the final reranked count (dense pulls a wider net first).
    Returns the reranker's result shape ({id, score, rerank_score, citation,
    section_title, content, court, circuit, year, issues, tier, batson,
    chunk_index, chunk_count}). Never raises — degraded deps return []/dense order.
    """
    if not query or not query.strip():
        return []
    if tier is not None and tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")

    qfilter = None
    must: list[dict] = []
    if tier is not None:
        must.append({"key": "tier", "match": {"value": tier}})
    if circuit:
        must.append({"key": "circuit", "match": {"value": circuit}})
    if batson is not None:
        must.append({"key": "batson", "match": {"value": batson}})
    if must:
        qfilter = {"must": must}

    dense_net = max(top_k * 4, 16)
    dense = await _search_cases_with_filter(query, qfilter, dense_net)
    if not dense:
        return []
    candidates = [_case_result(p) for p in dense]
    return await rerank(query, candidates, top_k=top_k)


def _case_result(point: dict) -> dict[str, Any]:
    """Shape a legal_cases dense point into a rerank candidate. The reranker's
    _candidate_text reads citation/section_title/content at the TOP level (its
    legal-retrieval branch), so map the case payload onto that shape."""
    payload = point.get("payload") or {}
    return {
        "id": point.get("id"),
        "score": point.get("score"),
        "citation": payload.get("cite", ""),
        "section_title": payload.get("case_name", ""),
        "content": payload.get("content", ""),
        "court": payload.get("court", ""),
        "circuit": payload.get("circuit", ""),
        "year": payload.get("year", 0),
        "issues": list(payload.get("issues") or []),
        "tier": payload.get("tier", 0),
        "batson": bool(payload.get("batson")),
        "chunk_index": payload.get("chunk_index", 0),
        "chunk_count": payload.get("chunk_count", 0),
    }


async def _search_cases_with_filter(query: str, qfilter: dict | None, top_k: int) -> list[dict]:
    """Dense search over legal_cases with an optional payload filter (same
    embed + query_points shape as _search_with_filter)."""
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
            collection_name=COLLECTION_CASES,
            query=vector,
            limit=top_k,
            with_payload=True,
            query_filter=qfilter,
        )
        points = getattr(response, "points", response)
        return [
            {"id": getattr(p, "id", None), "score": getattr(p, "score", None),
             "payload": getattr(p, "payload", None)}
            for p in (points or [])
        ]
    except Exception as exc:
        log.warning("filtered Qdrant search failed on %s (%s)", COLLECTION_CASES, exc)
        return []
    finally:
        await client.close()


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
