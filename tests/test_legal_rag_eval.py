"""Tests for the Rob's Lawyer RAG eval harness metrics (MAR + faithfulness).

MAR (Misleading Answer Rate) is LegalCiteBench's headline measure: of the
answers shipped with a confident citation, what fraction carry a misleading
one. Faithfulness is the reference-free claim-support check (ALCE-style).
These pin the deterministic metric functions (no LLM, no network).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from legal_rag_eval import mar_from_verification, faithfulness, evaluate_answer  # noqa: E402


def test_mar_zero_when_clean():
    v = {"checked": True, "count": 2, "fabricated": 0, "unaligned": 0,
         "shape_mismatch": 0, "unverified": 0, "unparsed": 0,
         "case_alignment": {"unaligned": []}}
    assert mar_from_verification(v) == 0.0


def test_mar_penalizes_fabrication():
    v = {"checked": True, "count": 2, "fabricated": 1, "unaligned": 0,
         "shape_mismatch": 0, "unverified": 0, "unparsed": 0,
         "case_alignment": {"unaligned": []}}
    assert mar_from_verification(v) == 0.5


def test_mar_penalizes_case_alignment_unaligned():
    v = {"checked": True, "count": 1, "fabricated": 0, "unaligned": 0,
         "shape_mismatch": 0, "unverified": 0, "unparsed": 0,
         "case_alignment": {"unaligned": [{"cite": "999 F.3d 123"}]}}
    assert mar_from_verification(v) == 1.0


def test_mar_zero_when_nothing_shipped():
    # Zero citations shipped -> nothing misleading was shipped -> MAR 0.
    v = {"checked": True, "count": 0, "fabricated": 0, "unaligned": 0,
         "shape_mismatch": 0, "unverified": 0, "unparsed": 0,
         "case_alignment": {"unaligned": []}}
    assert mar_from_verification(v) == 0.0


def test_mar_zero_when_check_did_not_run():
    assert mar_from_verification({}) == 0.0
    assert mar_from_verification({"checked": False, "score": None}) == 0.0


def test_faithfulness_supported_claim():
    f = faithfulness(
        ["The tenant is entitled to notice under N.Y. RPA Law § 235-b."],
        ["N.Y. RPA Law § 235-b tenant notice provision"],
    )
    assert f["rate"] == 1.0
    assert len(f["unsupported"]) == 0


def test_faithfulness_unsupported_claim():
    f = faithfulness(
        ["The defendant confessed on live television."],
        ["tenant notice provision N.Y. RPA Law"],
    )
    assert f["rate"] == 0.0
    assert len(f["unsupported"]) == 1


def test_faithfulness_partial():
    # First claim shares notice/eviction with the retrieved chunk (rate > 0);
    # the cheese claim shares nothing.
    f = faithfulness(
        ["notice is required for an eviction", "the moon is made of cheese"],
        ["the eviction notice requirement for a tenant"],
    )
    assert len(f["supported"]) == 1
    assert len(f["unsupported"]) == 1
    assert f["rate"] == 0.5


import pytest  # noqa: E402
from unittest.mock import patch  # noqa: E402


@pytest.mark.asyncio
async def test_evaluate_answer_runs_full_seam():
    """evaluate_answer drives the REAL verify_citations seam (only the external
    network leg stubbed) and folds its stats into MAR + faithfulness. The
    answer legitimately trips BOTH signals: unverified (no verdict) AND
    out-of-manifest (576 U.S. 644 absent from the curated case corpus) — MAR
    must count every penalty against the single shipped citation."""
    with patch("legal_rag_eval.verify_citations") as mock_vc:
        mock_vc.return_value.stats = {
            "count": 1, "verified": 0, "fabricated": 0, "ambiguous": 0,
            "shape_mismatch": 0, "unverified": 1, "unparsed": 0, "skipped": 0,
        }
        res = await evaluate_answer("q", "The answer cites 576 U.S. 644.",
                                    ["576 U.S. 644 Obergefell"])
    assert res["verify"]["unverified"] == 1
    # 576 U.S. 644 is not in the curated case manifest -> also case_unaligned.
    assert len(res["verify"]["case_alignment"]["unaligned"]) == 1
    # MAR = (unverified 1 + case_unaligned 1) / 1 shipped citation = 2.0 —
    # the metric counts EVERY misleading signal, it does not cap at 1.0.
    assert res["mar"] == 2.0
