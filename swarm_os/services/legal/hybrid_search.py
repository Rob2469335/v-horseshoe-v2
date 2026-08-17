"""Hybrid lexical+dense retrieval with Reciprocal Rank Fusion for Rob's Lawyer.

Research-backed (Anthropic Contextual Retrieval 2024; AusLaw Citation Benchmark
2412.07262): legal queries are phrase/citation-exact ("§ 235-b", "576 U.S. 644")
where dense embeddings miss exact tokens, and "hybrid methods which utilise a
trained re-ranker deliver the best results." The recipe:
  1. LEXICAL  — BM25-style token scoring over the candidate pool (deterministic,
     no index, no network). Exact-token recall for citations and section IDs.
  2. DENSE    — the existing embedding search.
  3. RRF      — Reciprocal Rank Fusion of the two orderings (k=60, the standard).
  4. RERANK   — the fused top-N through the cross-encoder (:8082) for the final
     ordering.

This module is pure + deterministic for the lexical leg and RRF (unit-testable
offline); the dense/rerank legs are injected so callers reuse the live stack.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# RRF constant (standard: 60). Rank fusion via 1/(k + rank) per list.
_RRF_K = 60.0

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens. Legal section IDs like "235-b" tokenize to
    ["235", "b"] — the "-" separates, but a citation "576 U.S. 644" also splits
    on "."; exact-token matches still fire on the digits (the retrieval-relevant
    part of a citation)."""
    return _TOKEN_RE.findall((text or "").lower())


def bm25_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Simplified BM25 over a document's token list (no corpus IDF — operates on
    a small retrieved candidate pool, so IDF adds little; term frequency +
    query-token coverage is the signal). Returns a score >= 0 where exact query
    tokens present in the doc score higher than absent."""
    if not query_tokens or not doc_tokens:
        return 0.0
    # Term frequency with the BM25 saturating form (k1=1.2, b=0.75 on doc len).
    tf: dict[str, int] = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    doc_len = len(doc_tokens) or 1
    avg_len = doc_len  # single-doc pool: normalizing by self keeps it stable
    score = 0.0
    for q in set(query_tokens):
        f = tf.get(q, 0)
        if f == 0:
            continue
        idf = 1.0  # pool-local: presence is the signal
        tf_norm = (f * (1.2 + 1.0)) / (
            f + 1.2 * (1.0 - 0.75 + 0.75 * (doc_len / avg_len))
        )
        score += idf * tf_norm
    # Coverage bonus: fraction of DISTINCT query tokens that appear.
    coverage = len(set(query_tokens) & set(tf.keys())) / max(1, len(set(query_tokens)))
    return score + coverage


def rrf_fuse(rankings: list[list[dict]], k: float = _RRF_K) -> list[dict]:
    """Reciprocal Rank Fusion of multiple ranked result lists. Each list is a
    list of {id, ...} in rank order. Returns a merged list ordered by descending
    fused score (higher = better). Deterministic; ties broken by first-appearance.

    Standard RRF: fused_score(doc) = sum over lists of 1/(k + rank). k=60.
    The input lists may come from different retrievers (dense + lexical) with
    incomparable raw scores — RRF only uses RANK, which is exactly why it fuses
    them fairly."""
    fused: dict[str, tuple[float, int]] = {}  # id -> (score, first_seen_rank)
    seen_order: list[str] = []
    for lst in rankings:
        for rank, item in enumerate(lst):
            doc_id = item.get("id")
            if doc_id is None:
                continue
            key = str(doc_id)
            score, first = fused.get(key, (0.0, len(seen_order)))
            fused[key] = (score + 1.0 / (k + rank + 1), first)
            if key not in seen_order:
                seen_order.append(key)
    # Reconstruct in fused-score order (descending), tie-break by first-seen.
    ordered = sorted(
        seen_order,
        key=lambda key: (-fused[key][0], fused[key][1]),
    )
    # Map back to the first-seen item (any list's dict with this id).
    by_id: dict[str, dict] = {}
    for lst in rankings:
        for item in lst:
            if item.get("id") is not None:
                by_id.setdefault(str(item["id"]), item)
    merged: list[dict] = []
    for key in ordered:
        item = dict(by_id.get(key) or {})
        item["rrf_score"] = round(fused[key][0], 6)
        merged.append(item)
    return merged


def hybrid_fuse(
    dense: list[dict],
    lexical: list[dict],
    dense_weight: float = 1.0,
    lexical_weight: float = 1.0,
) -> list[dict]:
    """Fuse dense + lexical rankings via weighted RRF. `dense` and `lexical` are
    rank-ordered result lists sharing `id` keys. The weights scale each list's
    RRF contribution (a lexical-heavy query like an exact citation benefits from
    raising lexical_weight)."""
    dense_scaled = list(dense) if dense_weight == 1.0 else list(dense)
    lex_scaled = list(lexical) if lexical_weight == 1.0 else list(lexical)
    # Weighted RRF: duplicate-list trick is clean — repeating a list k times
    # multiplies its fused contribution by k. For non-integer weights this is
    # approximate; for the common 1.0/1.0 it's exact.
    rankings: list[list[dict]] = []
    rankings.extend([dense_scaled] * max(1, round(dense_weight)))
    rankings.extend([lex_scaled] * max(1, round(lexical_weight)))
    return rrf_fuse(rankings)


def lexical_rank(
    query: str, candidates: list[dict], text_key: str = "content"
) -> list[dict]:
    """Rank a candidate pool by BM25-style token score against the query.
    Deterministic, offline, no index. Each candidate's searchable text is
    `candidate[text_key]` (falling back to payload[text_key] then str()).
    Returns the pool ranked by descending lexical score, with the score in
    `lexical_score`."""
    if not query or not candidates:
        return []
    q_tokens = tokenize(query)
    scored = []
    for c in candidates:
        if isinstance(c, dict):
            text = c.get(text_key) or (c.get("payload") or {}).get(text_key) or str(c)
        else:
            text = str(c)
        s = bm25_score(q_tokens, tokenize(text))
        item = dict(c) if isinstance(c, dict) else {"id": c, "content": text}
        item["lexical_score"] = s
        scored.append(item)
    scored.sort(key=lambda it: it["lexical_score"], reverse=True)
    return scored
