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

# The reranker (like the embedder) rejects requests whose combined token count
# exceeds llama.cpp's physical batch (`-b 8192`), and on CPU a huge single doc
# is so slow it ReadTimeouts. Word-choop every document to a budget BEFORE
# sending (same class of fix as the embedder's _fit_budget). Keep the budget
# modest: the cross-encoder only needs the citation + title + lead paragraph to
# score well — the dense search already found the doc on full text.
_RERANK_BUDGET_CHARS = 3500

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _client


def _fit_rerank_budget(text: str) -> str:
    """Word-chopping budget (mirrors the embedder's _fit_budget): never send the
    reranker a document that could overflow its physical batch or stall CPU
    inference. Preserves whole words up to the budget. A naive slice is avoided —
    the caller prepends citation+title, so the semantic lead survives."""
    if not text or len(text) <= _RERANK_BUDGET_CHARS:
        return text
    out: list[str] = []
    n = 0
    for w in text.split():
        if n + len(w) + 1 > _RERANK_BUDGET_CHARS:
            break
        out.append(w)
        n += len(w) + 1
    return " ".join(out)


def _candidate_text(candidate: dict) -> str:
    """Extract the searchable text from a Qdrant candidate result.

    Supports both the raw point shape from qdrant_store.search ({id, score,
    payload}) and the richer shapes (payload.fact / content / top-level content
    + citation from the legal retrieval layer)."""
    payload = candidate.get("payload") or {}
    for key in ("fact", "content", "text", "task", "pattern", "correction"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # Legal retrieval candidates carry citation + content at the TOP level
    # (not nested in payload) — prefer a citation-prefixed lead over a dump of
    # the whole dict.
    if isinstance(candidate.get("content"), str) and candidate["content"].strip():
        citation = candidate.get("citation", "") or ""
        title = candidate.get("section_title", "") or ""
        return f"{citation} — {title}\n{candidate['content']}"
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
        # Word-choop every candidate so a long statute section can't overflow
        # the reranker's physical batch (500) or stall CPU inference (ReadTimeout).
        texts = [_fit_rerank_budget(_candidate_text(c)) for c in candidates]
        # _RERANK_SEM is a threading.BoundedSemaphore (the HTTP client is
        # threadsafe/sync-compatible) — must use `with`, not `async with` (the
        # latter raised TypeError and degraded every rerank to the dense order).
        with _RERANK_SEM:
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
