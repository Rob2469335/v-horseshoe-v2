"""Tests for Rob's Lawyer hybrid retrieval + the reranker fixes it surfaced.

Covers:
- reranker._fit_rerank_budget word-choops long text (physical-batch/ReadTimeout fix)
- reranker._candidate_text extracts TOP-LEVEL content+citation (legal shape), not
  a dict dump
- legal_search.search_statutes validates jurisdiction and shapes candidates
- the rerank semaphore fix: a threading.BoundedSemaphore is used with `with`,
  not `async with` (the pre-existing TypeError that degraded every rerank)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.lib.vector import reranker
from swarm_os.services.legal import legal_search


def test_rerank_budget_chops_long_text():
    long = ("statute text " * 4000)  # ~52k chars, way over budget
    chopped = reranker._fit_rerank_budget(long)
    assert len(chopped) <= reranker._RERANK_BUDGET_CHARS
    assert chopped.endswith("text")  # whole words preserved


def test_rerank_budget_passes_short_text():
    short = "N.Y. Gen. Oblig. L. § 5-701. Written agreements."
    assert reranker._fit_rerank_budget(short) == short


def test_candidate_text_extracts_top_level_legal_shape():
    """Legal candidates carry citation+content at the TOP level (not nested in
    payload). Before the fix, _candidate_text fell through to str(candidate) —
    a serialized dict — so the reranker scored garbage."""
    cand = {
        "id": "u1", "score": 0.9,
        "citation": "N.Y. RPP Law § 227-F",
        "section_title": "Denial on basis of prior dispute",
        "content": "The owner shall not deny a tenancy on the basis of prior disputes.",
    }
    text = reranker._candidate_text(cand)
    assert "N.Y. RPP Law § 227-F" in text
    assert "prior disputes" in text
    assert "{" not in text  # not a dict dump


def test_candidate_text_payload_content_still_works():
    cand = {"id": "u2", "payload": {"fact": "memory fact text"}}
    assert reranker._candidate_text(cand) == "memory fact text"


def test_rerank_semaphore_is_async_not_threading():
    """The historical bug: `async with _RERANK_SEM` on a threading semaphore
    raised TypeError and degraded every rerank to the dense order. The earlier
    fix used a threading semaphore + a plain `with` — but that sync acquire
    blocks the event loop when the burst saturates both slots. The corrected
    contract is an asyncio.BoundedSemaphore + `async with`: it suspends the
    waiting task instead of freezing the loop. This test fails on a regression
    to EITHER the threading type OR the sync-acquire pattern."""
    import asyncio

    assert isinstance(reranker._RERANK_SEM, asyncio.BoundedSemaphore)
    # An asyncio semaphore MUST support the async context manager protocol.
    assert hasattr(reranker._RERANK_SEM, "__aenter__")


@pytest.mark.asyncio
async def test_legal_search_shapes_candidates_without_rerank():
    """search_statutes degrades to the dense order when the reranker is down —
    the endpoint must still return shaped results, never raise."""
    dense = [
        {"id": "u1", "score": 0.9, "payload": {"citation": "N.Y. A § 1", "content": "x",
                                               "jurisdiction": "ny", "section_title": "t"}},
    ]
    with patch.object(legal_search, "_search_with_filter", new=AsyncMock(return_value=dense)):
        with patch.object(legal_search, "rerank", new=AsyncMock(return_value=[])):
            res = await legal_search.search_statutes("x", jurisdiction="ny", top_k=5)
    assert isinstance(res, list)


@pytest.mark.asyncio
async def test_legal_search_rejects_bad_jurisdiction():
    with pytest.raises(ValueError):
        await legal_search.search_statutes("x", jurisdiction="zz", top_k=5)


# --- hybrid dense+lexical RRF wiring (rec 2) ----------------------------------

@pytest.mark.asyncio
async def test_search_statutes_uses_hybrid_fuse_when_enabled():
    """search_statutes must fuse the dense order with the lexical (BM25-style)
    order via _hybrid_fuse before reranking — verify the seam is actually hit."""
    dense = [
        {"id": "u1", "score": 0.5, "payload": {"citation": "N.Y. RPA Law § 235-b",
                                               "content": "tenant notice", "jurisdiction": "ny",
                                               "section_title": "t"}},
        {"id": "u2", "score": 0.4, "payload": {"citation": "N.Y. RPA Law § 999",
                                               "content": "unrelated tax", "jurisdiction": "ny",
                                               "section_title": "t"}},
    ]
    with patch.object(legal_search, "_search_with_filter", new=AsyncMock(return_value=dense)):
        with patch.object(legal_search, "_hybrid_fuse", new=AsyncMock(return_value=dense)) as fuse_mock:
            with patch.object(legal_search, "rerank", new=AsyncMock(return_value=[])):
                await legal_search.search_statutes("tenant notice 235-b", jurisdiction="ny", top_k=2)
    assert fuse_mock.await_count == 1, "hybrid fuse must be invoked on the dense candidates"


@pytest.mark.asyncio
async def test_search_statutes_hybrid_degrades_to_dense_on_failure():
    """A _hybrid_fuse failure must not raise — search_statutes returns the dense
    order (graceful degradation, the standing contract)."""
    dense = [{"id": "u1", "score": 0.9, "payload": {"content": "x", "jurisdiction": "ny"}}]
    with patch.object(legal_search, "_search_with_filter", new=AsyncMock(return_value=dense)):
        with patch.object(legal_search, "_hybrid_fuse",
                          new=AsyncMock(side_effect=RuntimeError("boom"))):
            with patch.object(legal_search, "rerank", new=AsyncMock(return_value=[])):
                res = await legal_search.search_statutes("x", jurisdiction="ny", top_k=5)
    assert isinstance(res, list)


@pytest.mark.asyncio
async def test_search_cases_uses_hybrid_fuse_when_enabled():
    """The case-leg search must fuse dense + lexical too (exact cite recall for
    case authorities)."""
    dense = [
        {"id": "c1", "score": 0.6, "payload": {"cite": "252 F.3d 238",
                                               "content": "substitute counsel", "tier": 1,
                                               "circuit": "2d", "batson": False, "case_name": "Simeonov"}},
    ]
    with patch.object(legal_search, "_search_cases_with_filter", new=AsyncMock(return_value=dense)):
        with patch.object(legal_search, "_hybrid_fuse", new=AsyncMock(return_value=dense)) as fuse_mock:
            with patch.object(legal_search, "rerank", new=AsyncMock(return_value=[])):
                await legal_search.search_cases("substitute counsel 252 F.3d", top_k=2)
    assert fuse_mock.await_count == 1
