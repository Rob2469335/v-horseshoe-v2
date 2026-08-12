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
                          top_k: int = 8, hybrid: bool = True) -> list[dict[str, Any]]:
    """Hybrid statute search.

    - `jurisdiction` filters to one of ny/nj/ga/nc/federal (None = all scoped).
    - `top_k` is the final reranked count (dense pulls a wider net first).
    - `hybrid` (default True) fuses the dense ordering with a deterministic
      lexical (BM25-style) ordering via RRF before the cross-encoder rerank —
      the SOTA recipe for legal queries (exact-citation recall dense misses).
      On a lexical/rerank outage it degrades to the dense ordering.
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

    if hybrid:
        try:
            dense = await _hybrid_fuse(query, dense, dense_net)
        except Exception as exc:
            log.warning("hybrid fuse failed, using dense order: %s", exc)

    candidates = [_payload_result(p) for p in dense]
    return await rerank(query, candidates, top_k=top_k)


async def _hybrid_fuse(query: str, dense: list[dict], net: int) -> list[dict]:
    """Fuse dense + lexical (BM25-style) orderings via RRF over the candidate
    pool, then return the fused ordering (still pre-rerank). Deterministic
    lexical leg; never raises — on any failure returns the dense order."""
    try:
        from swarm_os.services.legal.hybrid_search import lexical_rank, hybrid_fuse
        lexical = lexical_rank(query, dense, text_key="content")
        return hybrid_fuse(dense, lexical, dense_weight=1.0, lexical_weight=1.0)[:net]
    except Exception as exc:
        log.warning("hybrid fuse failed, using dense order: %s", exc)
        return dense


async def search_cases(query: str, tier: int | None = None,
                       circuit: str | None = None, batson: bool | None = None,
                       top_k: int = 8, hybrid: bool = True,
                       expand: bool = True) -> list[dict[str, Any]]:
    """Hybrid case-law search over the `legal_cases` collection.

    - `tier` filters to a manifest tier (1 controlling / 2 backbone / 3 context /
      4 Batson); `circuit` filters to a circuit string ("2d"/"scotus"/...);
      `batson` filters the Batson-authority subset (True/False).
    - `top_k` is the final reranked count (dense pulls a wider net first).
    - `hybrid` (default True) fuses dense + lexical (BM25-style) orderings via
      RRF before the cross-encoder rerank — exact-citation recall for case cites.
    - `expand` (default True) pulls the ONE-HOP CITATION-GRAPH NEIGHBORS of the
      retrieved cases (cases they cite + cases that cite them, scored by
      in-degree + recency) and merges them after the reranked results — the
      cite-follow authority expansion. On a graph-build failure it degrades to
      the unexpanded results.
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
    if hybrid:
        try:
            dense = await _hybrid_fuse(query, dense, dense_net)
        except Exception as exc:
            log.warning("hybrid fuse failed, using dense order: %s", exc)
    candidates = [_case_result(p) for p in dense]
    results = await rerank(query, candidates, top_k=top_k)
    if expand and results:
        results = await _graph_expand_results(results, top_k)
    return results


async def _graph_expand_results(results: list[dict], top_k: int) -> list[dict]:
    """Cite-follow expansion: merge each retrieved case's one-hop citation-graph
    neighbors (scored by in-degree + recency) into the result list. The expanded
    cases are fetched from Qdrant by their cite keys and appended (marked with
    `graph_expanded: True`). Fail-closed: any graph failure returns the original
    results unchanged (never raises)."""
    try:
        from swarm_os.services.legal.case_graph import (
            build_case_graph, graph_expand, case_citation_key,
        )
        from swarm_os.services.legal.case_corpus import CASE_MANIFEST
        from qdrant_client import AsyncQdrantClient
        import os
    except Exception as exc:
        log.warning("graph expand unavailable: %s", exc)
        return results

    # Seed keys from the retrieved results.
    seed_keys = []
    for r in results:
        k = case_citation_key(str(r.get("citation") or ""))
        if k:
            seed_keys.append(k)
    if not seed_keys:
        return results

    try:
        client = AsyncQdrantClient(url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"))
        # Build the graph from the stored corpus (scroll all legal_cases points).
        # Scroll returns pydantic Record objects, not plain dicts — normalize to
        # {id, payload} (same shape build_case_graph expects).
        offset: Any = None
        chunks: list = []
        while True:
            resp = await client.scroll(COLLECTION_CASES, limit=2000,
                                       with_payload=True, offset=offset)
            for point in resp[0]:
                chunks.append({
                    "id": getattr(point, "id", None),
                    "payload": getattr(point, "payload", None),
                })
            if resp[1] is None:
                break
            offset = resp[1]
        await client.close()
    except Exception as exc:
        log.warning("graph scroll failed: %s", exc)
        return results

    G = build_case_graph(chunks, CASE_MANIFEST)
    # Stamp the authority weight (in-degree) on the base results too — a
    # retrieved case that many manifest authorities cite is stronger authority.
    for r in results:
        k = case_citation_key(str(r.get("citation") or ""))
        r["graph_cited_by_count"] = int(G.in_degree(k)) if (k and k in G) else 0
    expanded_keys = graph_expand(G, seed_keys, depth=1, max_nodes=max(top_k, 4),
                                 include_seeds=False)
    if not expanded_keys:
        return results

    # Fetch the expanded cases' content from the chunks we already scrolled.
    cite_to_chunk: dict[str, dict] = {}
    for point in chunks:
        payload = point.get("payload") or {}
        k = case_citation_key(str(payload.get("cite") or ""))
        if k and payload.get("chunk_index") == 0:
            cite_to_chunk[k] = payload
    seen_cites = {r.get("citation", "") for r in results}
    for key in expanded_keys:
        payload = cite_to_chunk.get(key)
        if not payload or payload.get("cite") in seen_cites:
            continue
        seen_cites.add(payload.get("cite"))
        results.append({
            "id": None, "score": None,
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
            "graph_expanded": True,
            "graph_cited_by_count": int(G.in_degree(key)),
        })
    return results


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
