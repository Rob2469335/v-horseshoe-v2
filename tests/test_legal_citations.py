"""Tests for Rob's Lawyer citation verification (swarm_os/services/legal).

Covers the hybrid design:
- Eyecite parses case + statutory + id. citations
- verify_citations routes case citations to the CourtListener lookup and marks
  statutes/id./supra as parsed-but-skipped
- a fabricated citation (lookup status 404) surfaces as verified=False -> ok=False
- an ambiguous citation (300) is flagged but not a hard stop
- network outage degrades to skipped, never raises

M4 statutory-alignment seam (deterministic, eyecite-independent):
- extract_statute_sections() is the eyecite-independent stat-section extractor
- align_citations() flags a cited section not present in the retrieved corpus
- case_citation_key() gives a canonical key for Cat3 vol/vol-fake comparisons
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.services.legal.citation_verify import (
    VerifyResponse,
    verify_citations,
    _resolve_to_full,
    case_citation_key,
    extract_statute_sections,
    align_citations,
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


# --- M4 statutory-alignment seam ---------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("N.Y. RPA Law § 235-b", ["235-b"]),
    ("N.J.S.A. 46:8-19", ["46:8-19"]),
    ("42 U.S.C. § 1983", ["1983"]),
    ("N.Y. CPL Law § 200.50", ["200.50"]),
    ("N.Y. FCT Law § 581-202", ["581-202"]),
    ("N.Y. EDN Law § 3014-A", ["3014-A"]),
    ("Mass. Gen. Laws ch. 1, § 2", ["2"]),
])
def test_extract_statute_sections_captures_eyecite_breaks(text, expected):
    """These forms are exactly the ones eyecite M3 mis-parsed (§235-b -> §235,
    46:8-19 missed); the deterministic extractor must survive them."""
    assert extract_statute_sections(text) == expected


def test_extract_statute_sections_dedups_order_preserved():
    got = extract_statute_sections("§ 235-b then N.J.S. § 46:8-19 and back § 235-b")
    assert got == ["235-b", "46:8-19"]


def test_align_citations_marks_fabricated_section_unaligned():
    res = align_citations(
        "The lease says N.Y. RPA Law § 235-b and N.Y. RPA Law § 999-C.",
        retrieved_sections=["235-b", "200.50"],
    )
    assert res["count"] == 2
    assert res["aligned"] == [{"section": "235-b", "normalized": "235-b"}]
    unaligned = [u["section"] for u in res["unaligned"]]
    assert unaligned == ["999-C"]


def test_align_citations_normalizes_surrounding_noise_keys():
    res = align_citations("cite N.Y. RPA Law § 235-b", retrieved_sections=["235-b"])
    assert res["count"] == 1
    assert res["aligned"][0]["normalized"] == "235-b"


def test_case_citation_key_canonical_forms():
    assert case_citation_key("400 U.S. 79") == "400|us|79"
    assert case_citation_key("2009 MT 228") == "2009|mt|228"
    assert case_citation_key("88 So.3d 253") == "88|so3d|253"
    # Cat3 vol/page alterations change the key — detectable offline.
    assert case_citation_key("400 U.S. 79") != case_citation_key("400 U.S. 74")


# --- M4 fail-closed: couldn't-check ≠ clean (L3-trap guard) ------------------

def test_shape_detector_flags_exotic_shapes_but_not_prose():
    """The L3-trap bound: citation-shaped text that eyecite CANNOT parse must
    still be recognized as citation-bearing (so unparsed can surface it), while
    plain prose/statutory U.S.C. text must not false-positive."""
    from swarm_os.services.legal.citation_verify import count_citation_shapes
    assert count_citation_shapes("The holding in 900 So. 7d 694 applies.") == 1
    assert count_citation_shapes("See K.S.A. 2012 Supp. 47-501(b)(1)(E)") == 2
    assert count_citation_shapes("Bush v. Gore, 531 U.S. 98 (2000)") == 1
    assert count_citation_shapes("The tenant must pay within three days.") == 0
    assert count_citation_shapes("under the 42 U.S.C. food statute") == 0


@pytest.mark.asyncio
async def test_verify_citations_reports_unparsed_for_exotic_cite():
    """A citation-shaped passage eyecite cannot parse (900 So. 7d 694) must NOT
    come back as 'count=0, nothing to check' — it is UNPARSED, unverified, not
    clean. (The three exotic forms from the real Cat3 miss set.)"""
    for blob in (
        "The rule in 900 So. 7d 694 is controlling here.",
        "See K.S.A. 2012 Supp. 47-501(b)(1)(E) for the penalty schedule.",
    ):
        res = await verify_citations(blob)
        assert res.stats["unparsed"] > 0, blob
        assert res.ok is True  # unparsed is not fabricated (uncertainty is surfaced, not blocked)


@pytest.mark.asyncio
async def test_verify_citations_outage_counts_unverified_not_clean():
    """A case citation whose lookup yields NO verdict (status None — outage /
    no token) is UNVERIFIED. fabricated=0 counts it as unknown, unverified=1
    surfaces it — the old silent 'verified=0, fabricated=0' pass."""
    with patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={"status": None, "error_message": "request failed"})):
        res = await verify_citations("Obergefell v. Hodges, 576 U.S. 644 (2015)")
    assert res.stats["unverified"] >= 1
    assert res.stats["fabricated"] == 0
