"""Tests for the M3 vertical slice (legal_advisor) — the structural guarantees:

- FAIL-CLOSED jurisdiction gate: a question about an un-ingested jurisdiction
  (NJ/GA/NC/federal) returns no answer, never a synthesis from another state.
- Ambiguous jurisdiction (no state named) -> refuse, don't guess.
- corpus_scope marker is ALWAYS in the result (in-band, live, not a comment).
- synthesis unpacks stream_content correctly (content, kind) — the reversed
  unpack silently produced zero parts (the bug this test pins).
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.services.legal import legal_advisor


def test_detect_jurisdiction():
    assert legal_advisor._detect_jurisdiction("my landlord in New York won't return deposit") == "ny"
    assert legal_advisor._detect_jurisdiction("landlord in New Jersey security deposit") == "nj"
    assert legal_advisor._detect_jurisdiction("federal student loan discharge") == "federal"
    assert legal_advisor._detect_jurisdiction("what are my rights?") is None


def test_requires_min_coverage_fail_closed_below_floor():
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 100, "pct": 0.25, "complete": False}}}
    assert legal_advisor._requires_min_coverage(scope, "ny", minimum_pct=5.0) is False
    scope2 = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 19.9, "complete": False}}}
    assert legal_advisor._requires_min_coverage(scope2, "ny", minimum_pct=5.0) is True


@pytest.mark.asyncio
async def test_advise_fail_closed_for_uningested_jurisdiction():
    """An NJ question with 0 NJ sections ingested must refuse, answer empty."""
    scope = {
        "jurisdictions": {
            "ny": {"expected": 40102, "ingested": 4000, "pct": 10.0, "complete": False},
            "nj": {"expected": 55889, "ingested": 0, "pct": 0.0, "complete": False},
        },
        "total_ingested": 4000, "total_expected": 95991,
    }
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)):
        res = await legal_advisor.advise("my landlord in New Jersey won't return my deposit")
    assert res.ok is False
    assert res.fail_closed is True
    assert res.jurisdiction == "nj"
    assert res.answer == ""
    assert "NJ" in res.message
    assert res.corpus_scope == scope  # marker always present


@pytest.mark.asyncio
async def test_advise_fail_closed_ambiguous_jurisdiction():
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value={"jurisdictions": {}})):
        res = await legal_advisor.advise("what are my rights about a security deposit?")
    assert res.ok is False and res.fail_closed is True
    assert res.jurisdiction is None
    assert "jurisdiction" in res.message.lower()


@pytest.mark.asyncio
async def test_synthesis_unpacks_content_kind_correctly():
    """stream_content yields (content, kind) — content FIRST. The reversed
    unpack (kind, chunk) compared the TEXT against 'content' and produced zero
    parts. Pin the correct order by feeding a fake generator directly into
    _synthesize's stream consumer."""
    import swarm_os.services.legal.legal_advisor as la

    async def fake_stream(model, messages, agent_id):
        yield "the answer text", "content"
        yield "", "error"  # non-content kind must be ignored

    with patch("runtime_v2.services._llm_client.stream_content", new=fake_stream):
        out = await la._synthesize("q", "ny", [{"citation": "N.Y. GOL § 7-103", "content": "x"}])
    assert out.get("content") == "the answer text"


# --- M4 fail-closed: couldn't-check ≠ clean (L3-trap guard) ------------------

@pytest.mark.asyncio
async def test_advise_downgrades_on_unverified():
    """A parsed citation with NO verdict (status None — offline/no token) must
    appear in the [VERIFICATION] warning AND drop the score below 1.0 — it must
    NOT silently pass as '0 citation issues'."""
    from swarm_os.services.legal.citation_verify import VerifyResponse
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0}}} 
    fake_cites = VerifyResponse(
        ok=True, citations=[],
        stats={"count": 1, "verified": 0, "fabricated": 0, "ambiguous": 0,
               "unverified": 1, "unparsed": 0, "skipped": 0},
        message="1 unverified",
    )
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "jurisdiction": "ny", "content": "x"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": "N.Y. RPA Law § 235-b applies."})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=fake_cites)):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert "[VERIFICATION]" in res.answer
    assert "offline" in res.answer.lower() or "verdict" in res.answer.lower()
    assert res.verification["score"] < 1.0


@pytest.mark.asyncio
async def test_advise_downgrades_on_unparsed():
    """A citation-shaped passage eyecite couldn't parse (exotic form) must be
    surfaced as UNPARSED inside the [VERIFICATION] warning — never silently
    treated as a clean '0 citations' answer."""
    from swarm_os.services.legal.citation_verify import VerifyResponse
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    fake_cr = VerifyResponse(
        ok=True, citations=[],
        stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
               "unverified": 0, "unparsed": 1, "skipped": 0},
        message="1 unparsed",
    )
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": "See 900 So. 2d 3."})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=fake_cr)):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert "[VERIFICATION]" in res.answer
    assert "could not be parsed" in res.answer
    assert res.verification["unparsed"] == 1
    assert res.verification["score"] < 1.0
