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


@pytest.mark.asyncio
async def test_advise_downgrades_on_shape_mismatch():
    """A 200 is 'exists', not 'correct'. A page-altered cite that the lookup
    resolves to a cluster but normalizes to a DIFFERENT shape must downgrade the
    answer (shape_mismatch -> warning + score below 1.0), closing the fail-open
    where a token-enabled 200 used to pass a fabricated-alteration as clean."""
    from swarm_os.services.legal.citation_verify import VerifyResponse
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    fake_cr = VerifyResponse(
        ok=True, citations=[],
        stats={"count": 1, "verified": 0, "fabricated": 0, "ambiguous": 0,
               "shape_mismatch": 1, "unverified": 0, "unparsed": 0, "skipped": 0},
        message="1 shape-mismatch",
    )
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": "See 400 U.S. 79."})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=fake_cr)):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert "[VERIFICATION]" in res.answer
    assert "not as cited" in res.answer
    assert res.verification["shape_mismatch"] == 1
    assert res.verification["score"] < 1.0


# --- #8 score denominator regression: negative score on unverified+unparsed ---

@pytest.mark.asyncio
async def test_advise_score_not_negative_when_unverified_and_unparsed_coexist():
    """A REAL multi-failure document that trips BOTH the unverified path (a case
    citation whose lookup yields no verdict) AND the unparsed path (a
    citation-shaped passage eyecite cannot parse) must produce a score in [0, 1]
    — never negative. The old denominator `max(1, count, unverified, unparsed)`
    undercounted: unverified is a SUBSET of count, so max() added nothing there,
    while unparsed is genuinely EXTRA (those passages never entered count). With
    count=1/unverified=1/unparsed=1 the numerator (2) exceeded checked (1) and
    the score went to -1.0. The denominator must be the real total examined:
    count + unparsed.

    Built from a real document through the real verify_citations path (only the
    external _lookup_one network leg is stubbed, the established seam) — not a
    hand-built VerifyResponse:
      - "Obergefell v. Hodges, 576 U.S. 644 (2015)"  -> FullCaseCitation -> lookup
        returns status None -> unverified=1 (no token/outage, not fabricated)
      - "900 So. 7d 694"  -> citation-SHAPED (Cat3 miss form) but eyecite cannot
        parse it -> unparsed=1 (the L3-trap guard)
    """
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    real_doc = (
        "Obergefell v. Hodges, 576 U.S. 644 (2015) controls here, "
        "and the rule in 900 So. 7d 694 also applies."
    )
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": real_doc})), \
         patch("swarm_os.services.legal.citation_verify._lookup_one",
               new=AsyncMock(return_value={"status": None, "error_message": "request failed"})):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert res.verification["unverified"] == 1
    assert res.verification["unparsed"] == 1
    assert "[VERIFICATION]" in res.answer
    assert "could not be externally verified" in res.answer
    assert "could not be parsed" in res.answer
    assert 0.0 <= res.verification["score"] <= 1.0


@pytest.mark.asyncio
async def test_advise_score_downgrades_on_unaligned_statute():
    """A cited statute NOT present in the retrieved corpus is the statutory-
    fabrication signal and MUST drop the verification score below 1.0 (fail-
    closed contract: 'advise() drops the score below 1.0 on ANY of
    fabricated/unaligned/unverified/unparsed'). The old flow computed the score
    BEFORE the alignment seam and never recomputed it, so a fabricated statute
    left score=1.0 whenever no case citation was present to trip it."""
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    # Answer cites N.Y. RPA Law § 999-C, but the retrieved corpus only has 235-b.
    fabricated_statute = "The lease claim arises under N.Y. RPA Law § 999-C."
    from swarm_os.services.legal.citation_verify import VerifyResponse
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": fabricated_statute})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=VerifyResponse(
                   ok=True, citations=[], message="0 citations parsed",
                   stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
                          "unverified": 0, "unparsed": 0, "skipped": 0}))):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert res.verification["unaligned"] == 1
    assert "[VERIFICATION]" in res.answer
    assert "unaligned" in res.answer
    assert res.verification["score"] < 1.0, (
        "a fabricated statute (unaligned) must downgrade the verification score, "
        "not leave it at 1.0"
    )


@pytest.mark.asyncio
async def test_advise_score_stays_1_0_when_all_aligned():
    """Control for the unaligned test: a cited statute that IS in the retrieved
    corpus must NOT downgrade the score — the alignment seam must not penalize
    legitimately-grounded citations."""
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    grounded_statute = "The lease claim arises under N.Y. RPA Law § 235-b."
    from swarm_os.services.legal.citation_verify import VerifyResponse
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": grounded_statute})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=VerifyResponse(
                   ok=True, citations=[], message="0 citations parsed",
                   stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
                          "unverified": 0, "unparsed": 0, "skipped": 0}))):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert res.verification["unaligned"] == 0
    assert res.verification["score"] == 1.0


@pytest.mark.asyncio
async def test_advise_score_never_negative_when_multiple_unaligned():
    """A score can never go NEGATIVE: the M4 recompute put `unaligned` in the
    penalties numerator but not in the `checked` denominator, so with no case
    citations (count=0) and no unparseable shapes (unparsed=0) but TWO cited
    statutes absent from the corpus, penalties=2 / checked=1 produced score
    -1.0. Every numerator term must have a denominator slot."""
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    fabricated_statutes = "The lease claim arises under N.Y. RPA Law § 999-C and N.Y. RPA Law § 888-D."
    from swarm_os.services.legal.citation_verify import VerifyResponse
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": fabricated_statutes})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=VerifyResponse(
                   ok=True, citations=[], message="0 citations parsed",
                   stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
                          "unverified": 0, "unparsed": 0, "skipped": 0}))):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert res.verification["unaligned"] == 2
    assert res.verification["score"] is not None
    assert 0.0 <= res.verification["score"] < 1.0, (
        "two unaligned statutes with zero case citations must downgrade the score "
        "but never send it negative: got "
        f"{res.verification['score']}"
    )


# --- M6 case-law leg: advise() retrieves + verifies case authority -----------

@pytest.mark.asyncio
async def test_advise_retrieves_case_law_and_reports_case_citations():
    """advise() must retrieve case authority alongside statutes and surface it in
    the result message — the case-law leg closes the statutes-only gap (the
    advisor previously never consulted the curated legal_cases corpus)."""
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    from swarm_os.services.legal.citation_verify import VerifyResponse
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch("swarm_os.services.legal.legal_search.search_cases",
               new=AsyncMock(return_value=[{"citation": "252 F.3d 238", "section_title": "United States v. Simeonov",
                                            "court": "2d Cir.", "circuit": "2d", "year": 2001, "tier": 1, "content": "y"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": "N.Y. RPA Law § 235-b and 252 F.3d 238 both apply."})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=VerifyResponse(
                   ok=True, citations=[], message="0 parsed",
                   stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
                          "unverified": 0, "unparsed": 0, "skipped": 0}))):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert "case authority" in res.message
    # The case citation is aligned to the retrieved case corpus.
    assert res.verification["case_alignment"]["count"] == 1
    assert "252 F.3d 238" in res.verification["case_alignment"]["aligned"]


@pytest.mark.asyncio
async def test_advise_downgrades_on_case_citation_outside_manifest():
    """M6: a case citation NOT present in the curated manifest (out-of-corpus /
    fabricated) must downgrade the verification score and appear in the
    [VERIFICATION] warning — closing the case-law fabrication hole."""
    scope = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000, "pct": 20.0, "complete": False}}}
    from swarm_os.services.legal.citation_verify import VerifyResponse
    fabricated_case = "The rule from 999 F.3d 123 controls here."
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch("swarm_os.services.legal.legal_search.search_cases",
               new=AsyncMock(return_value=[{"citation": "252 F.3d 238", "section_title": "United States v. Simeonov",
                                            "court": "2d Cir.", "circuit": "2d", "year": 2001, "tier": 1, "content": "y"}])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": fabricated_case})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=VerifyResponse(
                   ok=True, citations=[], message="0 parsed",
                   stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
                          "unverified": 0, "unparsed": 0, "skipped": 0}))):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert res.ok is True
    assert len(res.verification["case_alignment"]["unaligned"]) == 1
    assert "[VERIFICATION]" in res.answer
    assert "case citation(s) are not present in the curated case-law manifest" in res.answer
    assert res.verification["score"] < 1.0


# --- IRAC-structured synthesis (M7) ------------------------------------------

def test_split_issues_compound_question():
    from swarm_os.services.legal.legal_advisor import split_issues
    issues = split_issues("what notice must a landlord give, and what deposit interest is owed?")
    assert len(issues) == 2
    assert "notice" in issues[0]
    assert "deposit" in issues[1]


def test_split_issues_single_question_untouched():
    from swarm_os.services.legal.legal_advisor import split_issues
    issues = split_issues("what is the statute of limitations for a NY eviction?")
    assert len(issues) == 1
    assert issues[0] == "what is the statute of limitations for a NY eviction?"


def test_irac_sections_parses_headed_answer():
    from swarm_os.services.legal.legal_advisor import _irac_sections
    txt = (
        "Issue: the landlord's deposit deduction\n"
        "Rule: N.Y. RPA Law § 235-b governs deposits\n"
        "Application: here the deduction was unreasonable (N.Y. RPA Law § 235-b)\n"
        "Conclusion: the tenant is entitled to return"
    )
    irac = _irac_sections(txt)
    assert "rule" in irac and "application" in irac and "conclusion" in irac
    assert "235-b" in irac["application"]


def test_irac_sections_untreated_text_becomes_application():
    from swarm_os.services.legal.legal_advisor import _irac_sections
    txt = "the tenant is entitled to the return of the deposit under N.Y. RPA Law § 235-b."
    irac = _irac_sections(txt)
    assert "application" in irac
    assert "235-b" in irac["application"]


def test_application_grounding_flags_ungrounded_citation():
    """The IRAC post-check: an Application citing a section NOT in the retrieved
    corpus is ungrounded — the 'verifier accepts only traceable paths' rule."""
    from swarm_os.services.legal.legal_advisor import _application_grounding
    g = _application_grounding(
        "the deposit rule in N.Y. RPA Law § 235-b and the fabricated N.Y. RPA Law § 999-C apply",
        retrieved_citations=["N.Y. RPA Law § 235-b"],
        case_citations=[],
    )
    assert g["count"] == 2
    assert "235-b" in g["grounded"]
    assert "999-C" in g["ungrounded"]


@pytest.mark.asyncio
async def test_synthesize_irac_returns_grounding_postcheck():
    """synthesize_irac must return the IRAC sections AND run the deterministic
    grounding post-check on the Application, driving the real stream consumer
    seam (patched stream_content) + real _application_grounding."""
    import swarm_os.services.legal.legal_advisor as la

    async def fake_stream(model, messages, agent_id):
        yield ("Issue: deposit deduction\n"
               "Rule: RPA § 235-b\n"
               "Application: the deduction was bad (N.Y. RPA Law § 235-b)\n"
               "Conclusion: return it"), "content"

    with patch("runtime_v2.services._llm_client.stream_content", new=fake_stream):
        out = await la.synthesize_irac(
            "deposit question", "ny",
            [{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}],
            [],
        )
    assert out["irac"]["application"]
    assert "235-b" in out["irac"]["application"]
    assert out["grounding"]["grounded"], "cited retrieved section must be grounded"
    assert not out["grounding"]["ungrounded"]


# --- Standard-of-review conditioning (rec 8) ---------------------------------

def test_detect_standard_of_review():
    from swarm_os.services.legal.legal_advisor import detect_standard_of_review
    assert detect_standard_of_review("what standard of review applies to the loss calculation — de novo?") == "de_novo"
    assert detect_standard_of_review("the restitution award is reviewed for abuse of discretion") == "abuse_of_discretion"
    assert detect_standard_of_review("clear error standard") == "clear_error"
    assert detect_standard_of_review("plain error review of the unpreserved objection") == "plain_error"
    assert detect_standard_of_review("substantial evidence supports the finding") == "substantial_evidence"
    assert detect_standard_of_review("what notice must a landlord give?") is None


def test_standard_conditioning_emits_block_for_known_standard():
    from swarm_os.services.legal.legal_advisor import _standard_conditioning
    blk = _standard_conditioning("is the restitution award an abuse of discretion?")
    assert "ABUSE-OF-DISCRETION" in blk
    assert "do not substitute your own judgment" in blk


def test_standard_conditioning_empty_when_no_standard():
    from swarm_os.services.legal.legal_advisor import _standard_conditioning
    assert _standard_conditioning("what is the eviction notice requirement?") == ""


@pytest.mark.asyncio
async def test_synthesize_injects_standard_conditioning():
    """The standard-of-review block must be present in the synthesis system
    prompt when the question names a standard — capture the messages the LLM
    would receive via the patched stream seam."""
    import swarm_os.services.legal.legal_advisor as la
    captured: dict = {}

    async def fake_stream(model, messages, agent_id):
        captured["system"] = messages[0]["content"]
        yield "the answer", "content"

    with patch("runtime_v2.services._llm_client.stream_content", new=fake_stream):
        await la._synthesize(
            "is the award an abuse of discretion?",
            "ny",
            [{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}],
            [],
        )
    assert "ABUSE-OF-DISCRETION" in captured["system"]


# --- statute currency / as-of (rec 10) ---------------------------------------

@pytest.mark.asyncio
async def test_corpus_scope_reports_snapshot():
    """corpus_scope must report which OpenUSLaw snapshot each jurisdiction's
    ingested sections came from — the 'as of WHAT law' answer."""
    from swarm_os.services.legal.citation_verify import VerifyResponse
    scope_payload = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000,
                                              "pct": 20.0, "complete": False, "snapshot": "v2026.07"}}}
    # corpus_scope is real (scrolls Qdrant) — patch it, but test that the
    # snapshot flows into the answer's [LAW AS OF] line via advise().
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope_payload)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch("swarm_os.services.legal.legal_search.search_cases",
               new=AsyncMock(return_value=[])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": "The rule applies."})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=VerifyResponse(
                   ok=True, citations=[], message="0 parsed",
                   stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
                          "unverified": 0, "unparsed": 0, "skipped": 0}))):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert "[LAW AS OF]" in res.answer
    assert "v2026.07" in res.answer


@pytest.mark.asyncio
async def test_corpus_scope_flags_missing_snapshot():
    """A jurisdiction with no snapshot (pre-currency ingest) must be flagged in
    the answer, never silently passed as current law."""
    from swarm_os.services.legal.citation_verify import VerifyResponse
    scope_payload = {"jurisdictions": {"ny": {"expected": 40102, "ingested": 8000,
                                              "pct": 20.0, "complete": False, "snapshot": ""}}}
    with patch.object(legal_advisor, "corpus_scope", new=AsyncMock(return_value=scope_payload)), \
         patch("swarm_os.services.legal.legal_search.search_statutes",
               new=AsyncMock(return_value=[{"citation": "N.Y. RPA Law § 235-b", "section_title": "t", "content": "x"}])), \
         patch("swarm_os.services.legal.legal_search.search_cases",
               new=AsyncMock(return_value=[])), \
         patch.object(legal_advisor, "_synthesize",
                      new=AsyncMock(return_value={"content": "The rule applies."})), \
         patch("swarm_os.services.legal.citation_verify.verify_citations",
               new=AsyncMock(return_value=VerifyResponse(
                   ok=True, citations=[], message="0 parsed",
                   stats={"count": 0, "verified": 0, "fabricated": 0, "ambiguous": 0,
                          "unverified": 0, "unparsed": 0, "skipped": 0}))):
        res = await legal_advisor.advise("my landlord in New York won't return deposit")
    assert "[LAW AS OF]" in res.answer
    assert "predates snapshot tracking" in res.answer
