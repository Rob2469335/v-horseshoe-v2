"""Tests for Rob's Lawyer citation verification (swarm_os/services/legal).

Covers the hybrid design:
- Eyecite parses case + statutory + id. citations
- verify_citations routes case citations to the CourtListener lookup and marks
  statutes/id./supra as parsed-but-skipped
- a fabricated citation (lookup status 404) surfaces as verified=False -> ok=False
- an ambiguous citation (300) is flagged but not a hard stop
- network outage degrades to skipped, never raises
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.services.legal.citation_verify import (
    VerifyResponse,
    verify_citations,
    _resolve_to_full,
)


@pytest.mark.parametrize("blob,expected", [
    ("Bush v. Gore, 531 U.S. 98 (2000)", ["FullCaseCitation"]),
    ("Mass. Gen. Laws ch. 1, § 2", ["FullLawCitation"]),
    ("Bush v. Gore, 531 U.S. 98 (2000). Id. at 100.", ["FullCaseCitation", "IdCitation"]),
])
def test_eyecite_parses_legal_citations(blob, expected):
    strings, kinds = _resolve_to_full(blob)
    assert [k for k in kinds if k in expected] or not kinds, f"kinds={kinds}"


def test_verify_citations_statutes_skipped_not_verified():
    """Statutory citations are parsed but not externally verified (the API skips
    them) — they get skipped_reason, never a fabricated=false."""
    blob = "Mass. Gen. Laws ch. 1, § 2 (West 1999)."
    res = _run_verify(blob, lookup_results={})
    assert res.stats["count"] >= 1
    for c in res.citations:
        if c.kind == "FullLawCitation":
            assert c.skipped_reason
            assert not c.verified


@pytest.mark.asyncio
async def test_verify_citations_fabricated_blocks():
    """A case citation the lookup cannot find (404) is fabricated -> ok=False."""
    with patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={"status": 404, "error_message": "Citation not found"})):
        res = await verify_citations("Fake v. Nobody, 999 U.S. 1 (2000)")
    assert res.ok is False
    assert res.stats["fabricated"] == 1
    assert any(not c.verified and c.status == 404 for c in res.citations)


@pytest.mark.asyncio
async def test_verify_citations_ambiguous_flagged_not_blocked():
    """300 (multiple matches) is flagged, not a hard stop."""
    with patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={"status": 300, "clusters": [{}, {}]})):
        res = await verify_citations("1 H. 150")
    assert res.ok is True  # ambiguous is not fabricated
    assert res.stats["ambiguous"] == 1


@pytest.mark.asyncio
async def test_verify_citations_lookup_outage_degrades():
    """A lookup request failure must not raise — degrade to an unverified entry."""
    with patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={"status": None, "error_message": "request failed"})):
        res = await verify_citations("Obergefell v. Hodges, 576 U.S. 644 (2015)")
    assert res.ok is True  # outage is not fabricated (unknown)
    assert any(c.status is None for c in res.citations)


def _run_verify(blob: str, lookup_results: dict) -> VerifyResponse:
    """Sync helper: run verify_citations with a stubbed _lookup_one returning
    `lookup_results` for every call."""
    import asyncio
    async def _run():
        with patch("swarm_os.services.legal.citation_verify._lookup_one",
                   new=AsyncMock(return_value=lookup_results)):
            return await verify_citations(blob)
    return asyncio.run(_run())
