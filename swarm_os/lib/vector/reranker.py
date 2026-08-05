"""
reranker.py - Cross-Encoder reranking of Qdrant candidate results.

Reranks coarse vector-search candidates with the BGE cross-encoder served by
llama.cpp on :8082 (the same model the memory pipeline uses), so the final
ordering reflects true query-document relevance rather than embedding distance
alone. This module was previously an empty stub — which made the
POST /features/search endpoint raise ImportError and return 503
("Vector search not yet configured").
"""
from __future__ import annotations

import asyncio
import logging
import threading

import httpx

logger = logging.getLogger(__name__)

RERANK_URL = "http://127.0.0.1:8082"
RERANK_MODEL = "qllama-bge-reranker-v2-m3-latest.gguf"

# Bound concurrent rerank HTTP calls: the BGE reranker is memory-bandwidth bound
# on the iGPU, and analysis-agent bursts saturated DDR5 (the historical 90/120s
# timeouts). A semaphore keeps the burst bounded while batching preserves
# throughput.
_RERANK_SEM = threading.BoundedSemaphore(2)

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _client


def _candidate_text(candidate: dict) -> str:
    """Extract the searchable text from a Qdrant candidate result.

    Supports both the raw point shape from qdrant_store.search ({id, score,
    payload}) and the richer memory-pipeline shape (payload.fact / content)."""
    payload = candidate.get("payload") or {}
    for key in ("fact", "content", "text", "task", "pattern", "correction"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # Fall back to the whole payload, or the raw candidate string if any.
    if payload:
        return str(payload)
    return str(candidate)


async def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """Rerank coarse candidates by cross-encoder relevance. Returns up to top_k
    results, each re-scored with the reranker's score. Empty/None-tolerant so
    a reranker outage degrades to the original ordering instead of erroring."""
    if not candidates:
        return []
    try:
        texts = [_candidate_text(c) for c in candidates]
        async with _RERANK_SEM:
            resp = await _get_client().post(
                f"{RERANK_URL}/v1/rerank",
                headers={"Authorization": "Bearer llama"},
                json={"model": RERANK_MODEL, "query": query, "documents": texts, "top_n": top_k},
            )
        if resp.status_code != 200:
            logger.warning("Reranker returned %s — returning candidates unchanged", resp.status_code)
            return candidates[:top_k]
        results = resp.json().get("results", [])
        ordered: list[dict] = []
        for res in results:
            idx = res.get("index")
            if idx is None or idx >= len(candidates):
                continue
            item = dict(candidates[idx])
            item["score"] = res.get("relevance_score", item.get("score"))
            item["rerank_score"] = res.get("relevance_score")
            ordered.append(item)
        if not ordered:
            return candidates[:top_k]
        return ordered
    except Exception as exc:
        logger.warning("Rerank failed (%s) — returning candidates unchanged", exc)
        return candidates[:top_k]
