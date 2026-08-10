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


@pytest.mark.asyncio
async def test_verify_citations_200_with_matching_shape_is_verified():
    """A 200 whose normalized citation canonically equals the cite we sent is a
    genuine verified-clean result (e.g. real 400 U.S. 74 -> normalized 400 U.S. 74)."""
    with patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={
                   "status": 200,
                   "normalized_citations": ["400 U.S. 74"],
                   "clusters": [{"case_name": "Dutton v. Evans", "citations": [{"volume": "400", "reporter": "U.S.", "page": "74"}]}],
               })):
        res = await verify_citations("Bush v. Gore, 400 U.S. 74 (2000)")
    assert res.stats["shape_mismatch"] == 0
    assert res.stats["verified"] == 1
    assert res.stats["unverified"] == 0


@pytest.mark.asyncio
async def test_verify_citations_200_with_altered_shape_is_mismatch():
    """A 200 is NOT 'correct' — it means 'exists'. The M4 fail-open trap: a
    page-altered cite (400 U.S. 79 vs real 400 U.S. 74) makes the lookup return
    200 (same cluster) but normalized to the REAL page (74). That mismatch must
    be surfaced as shape_mismatch / downgrade, NOT verified=clean."""
    with patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={
                   "status": 200,
                   "normalized_citations": ["400 U.S. 74"],
                   "clusters": [{"case_name": "Dutton v. Evans", "citations": [{"volume": "400", "reporter": "U.S.", "page": "74"}]}],
               })):
        res = await verify_citations("Bush v. Gore, 400 U.S. 79 (2000)")
    assert res.stats["shape_mismatch"] == 1
    assert res.stats["verified"] == 0
    assert res.stats["fabricated"] == 0
    assert any(c.shape_mismatch and not c.verified for c in res.citations)


@pytest.mark.asyncio
async def test_verify_citations_200_empty_normalized_not_mismatch():
    """A 200 with no normalized_citations data has no shape to compare — treat
    as the old behaviour (verified; there's no counter-evidence). Do NOT invent
    a mismatch we have no evidence for."""
    with patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={
                   "status": 200,
                   "normalized_citations": [],
                   "clusters": [{"case_name": "Some Case"}],
               })):
        res = await verify_citations("Obergefell v. Hodges, 576 U.S. 644 (2015)")
    assert res.stats["shape_mismatch"] == 0
    assert res.stats["verified"] == 1


# --- M4 statutory-alignment seam ---------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("N.Y. RPA Law § 235-b", ["235-b"]),
    ("N.J.S.A. 46:8-19", ["46:8-19"]),
    ("42 U.S.C. § 1983", ["42-1983"]),
    ("N.Y. CPL Law § 200.50", ["200.50"]),
    ("N.Y. FCT Law § 581-202", ["581-202"]),
    ("N.Y. EDN Law § 3014-A", ["3014-A"]),
    ("Mass. Gen. Laws ch. 1, § 2", ["2"]),
])
def test_extract_statute_sections_captures_eyecite_breaks(text, expected):
    """These forms are exactly the ones eyecite M3 mis-parsed (§235-b -> §235,
    46:8-19 missed); the deterministic extractor must survive them."""
    assert extract_statute_sections(text) == expected


def test_extract_statute_sections_keeps_usc_title_in_key():
    """M4 statutory-alignment defense: the U.S.C. Title must stay in the section
    key. '18 U.S.C. § 1983' and '42 U.S.C. § 1983' are DIFFERENT laws — if the
    alignment seam strips the Title, a hallucinated Title (18 vs 42) falsely
    flags as aligned. Both must extract to distinct title-qualified keys."""
    assert extract_statute_sections("18 U.S.C. § 1983") == ["18-1983"]
    assert extract_statute_sections("42 U.S.C. § 1983") == ["42-1983"]
    assert extract_statute_sections("18 U.S.C. § 1983") != extract_statute_sections("42 U.S.C. § 1983")


def test_align_citations_wrong_title_is_unaligned():
    """A cited statute whose Title differs from the retrieved corpus section is
    UNALIGNED (the statutory-fabrication signal), never aligned just because the
    section number matches."""
    res = align_citations(
        "The claim arises under 18 U.S.C. § 1983.",
        retrieved_sections=["42 U.S.C. § 1983"],
    )
    assert res["count"] == 1
    assert res["aligned"] == []
    unaligned = [u["section"] for u in res["unaligned"]]
    assert unaligned == ["18-1983"]
    # The matching Title aligns normally.
    ok = align_citations("The claim arises under 42 U.S.C. § 1983.",
                         retrieved_sections=["42 U.S.C. § 1983"])
    assert ok["unaligned"] == []
    assert [a["normalized"] for a in ok["aligned"]] == ["42-1983"]


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


def test_case_citation_key_series_space_normalization():
    """The CourtListener canonical form spaces the series ('Ohio St. 3d 57')
    while the passage form does not ('Ohio St.3d 57'). Both must canonicalize
    to the SAME key — otherwise a real series citation gets a dropped canonical
    key and a false shape_mismatch (the 2026-08-09 live-probe regression)."""
    assert case_citation_key("142 Ohio St. 3d 57") == "142|ohiost3d|57"
    assert case_citation_key("142 Ohio St.3d 57") == "142|ohiost3d|57"
    # Series stays IN the key: 84 So.3d 661 vs 84 So.2d 661 still differ (Cat3).
    assert case_citation_key("84 So. 3d 661") == "84|so3d|661"
    assert case_citation_key("84 So. 3d 661") != case_citation_key("84 So. 2d 661")


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
async def test_unparsed_not_overcounted_for_parsed_non_case_citations():
    """`unparsed` is 'citation-SHAPED but eyecite could NOT parse AT ALL'. A span
    eyecite DID parse — as a non-case kind (FullLawCitation / IdCitation /
    SupraCitation) — must NOT be counted unparsed. The old formula subtracted
    only FullCaseCitation kinds, so a parsed statute was double-counted: 2 shapes
    (case-shape + statute-supp-shape) minus 1 parsed case = 1 unparsed even when
    the statute was lifted fine. Patching the parser to return exactly one parsed
    statute must yield unparsed = shapes - 1, never shapes."""
    from swarm_os.services.legal.citation_verify import count_citation_shapes

    blob = "See K.S.A. 2012 Supp. 47-501(b)(1)(E) and Bush v. Gore, 531 U.S. 98 (2000)."
    shapes = count_citation_shapes(blob)
    assert shapes >= 2
    with patch("swarm_os.services.legal.citation_verify._resolve_to_full",
               return_value=(["K.S.A. 2012 Supp. 47-501(b)(1)(E)", "531 U.S. 98"],
                             ["FullLawCitation", "FullCaseCitation"])):
        res = await verify_citations(blob)
    assert res.stats["unparsed"] == shapes - 2, (
        "the two successfully-parsed citations (statute + case) must be subtracted "
        "from the shape count; got unparsed=%s for shapes=%s" % (res.stats["unparsed"], shapes)
    )


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
