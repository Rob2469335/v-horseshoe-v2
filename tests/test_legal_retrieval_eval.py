"""Tests for the MLEB-style retrieval eval (scripts/legal_retrieval_eval.py).

Evidence: the GTE embedder + BGE reranker have no legal-domain numbers, so the
recall@K/MRR eval decides whether retrieval is silently leaking recall. Tests
pin the metric computation (recall@K + MRR) with the search legs mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from legal_retrieval_eval import evaluate_item, run_eval, _normalize_cite  # noqa: E402


def test_normalize_cite_case_key():
    assert _normalize_cite("507 U.S. 725") == "507|us|725"
    assert _normalize_cite("252 F.3d 238") == "252|f3d|238"


@pytest.mark.asyncio
async def test_evaluate_item_recall_when_expected_surfaced():
    """If the expected authority is in the top-k, recall@K=1.0 and MRR>0."""
    fake_results = [
        {"citation": "507 U.S. 725", "content": "plain error standard"},
        {"citation": "476 U.S. 79", "content": "batson"},
    ]
    with patch(
        "legal_retrieval_eval.search_cases", new=AsyncMock(return_value=fake_results)
    ):
        res = await evaluate_item(
            {
                "question": "plain error",
                "corpus": "cases",
                "expected_cites": ["507 U.S. 725"],
            },
            top_k=2,
        )
    assert res["recall@k"] == 1.0
    assert res["mrr"] == 1.0  # expected cite is rank 1


@pytest.mark.asyncio
async def test_evaluate_item_recall_when_expected_missing():
    """If the expected authority is NOT surfaced, recall@K=0 — the leak signal."""
    fake_results = [{"citation": "476 U.S. 79", "content": "batson"}]
    with patch(
        "legal_retrieval_eval.search_cases", new=AsyncMock(return_value=fake_results)
    ):
        res = await evaluate_item(
            {
                "question": "plain error",
                "corpus": "cases",
                "expected_cites": ["507 U.S. 725"],
            },
            top_k=2,
        )
    assert res["recall@k"] == 0.0
    assert res["mrr"] == 0.0


@pytest.mark.asyncio
async def test_evaluate_item_statutes_uses_search_statutes():
    fake_results = [{"citation": "N.Y. GOL Law § 7-103", "content": "deposit"}]
    with patch(
        "legal_retrieval_eval.search_statutes", new=AsyncMock(return_value=fake_results)
    ):
        res = await evaluate_item(
            {
                "question": "security deposit",
                "jurisdiction": "ny",
                "corpus": "statutes",
                "expected_cites": ["N.Y. GOL Law § 7-103"],
            },
            top_k=3,
        )
    assert res["recall@k"] == 1.0


@pytest.mark.asyncio
async def test_evaluate_item_keyword_scoring_for_topic_question():
    """A topic-described question (no exact cite) scores by keyword presence in
    the top result's content — the dense/context retrieval test."""
    fake_results = [
        {
            "citation": "N.Y. RPA Law § 235-b",
            "content": "the warranty of habitability requires...",
        }
    ]
    with patch(
        "legal_retrieval_eval.search_statutes", new=AsyncMock(return_value=fake_results)
    ):
        res = await evaluate_item(
            {
                "question": "uninhabitable apartment",
                "jurisdiction": "ny",
                "corpus": "statutes",
                "expected_cites": [],
                "expected_keywords": ["habitability"],
            },
            top_k=3,
        )
    assert res["recall@k"] == 1.0
    assert res["mrr"] == 1.0


@pytest.mark.asyncio
async def test_run_eval_aggregates_recall_and_mrr():
    """run_eval must aggregate recall@K + MRR across the golden set (2 items:
    1 hit at rank1, 1 miss -> mean recall 0.5, mean mrr 0.5)."""
    results = [
        {"citation": "507 U.S. 725", "content": "plain error"},
        {"citation": "252 F.3d 238", "content": "substitute counsel"},
    ]
    with patch(
        "legal_retrieval_eval.search_cases", new=AsyncMock(return_value=results)
    ):
        report = await run_eval(
            [
                {
                    "question": "plain error",
                    "corpus": "cases",
                    "expected_cites": ["507 U.S. 725"],
                },
                {
                    "question": "batson",
                    "corpus": "cases",
                    "expected_cites": ["999 F.3d 1"],
                },
            ],
            top_k=2,
        )
    assert report["n"] == 2
    assert report["mean_recall@k"] == 0.5
    assert report["mean_mrr"] == 0.5
