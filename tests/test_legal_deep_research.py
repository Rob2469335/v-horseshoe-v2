"""Tests for the deep-research AI criminal-defense mode
(swarm_os/services/legal/deep_research.py).

Research-grounded: LegalSearch-R1 (2605.25920) corpus+web > RAG-only for
temporal consistency; "When Does Persona Prompting Actually Help?" (2605.29420)
legal-domain finding — persona gains are small and hurt clarity, so the persona
is a RESTRAINED expertise-role, not a costume. These tests pin:
  - the persona content (expertise + issue-spot + grounding + honesty)
  - authoritative-domain URL preference
  - temporal grounding (law-as-of threading)
  - the offline fail-closed path (no web tools => corpus-only, never raises)
  - the verification contract on the returned answer
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from swarm_os.services.legal.deep_research import (
    _is_authoritative,
    _pick_fetch_urls,
    _law_as_of,
    _web_research,
    _PERSONA,
    _ISSUE_CHECKLIST,
)


def test_persona_is_restrained_expertise_not_costume():
    """The persona must carry expertise + analytical discipline + honesty, and
    must NOT claim to be 'the best in the world' (the paper's finding: maximal
    personas reduce legal clarity and add nothing measured)."""
    p = _PERSONA.lower()
    assert "senior federal criminal-defense appellate lawyer" in p
    assert "issue-spot" in p
    assert "cite only the authorities" in p
    assert "not legal advice" in p
    assert "best in the world" not in p, "maximal personas are contraindicated"


def test_issue_checklist_has_review_standards():
    c = _ISSUE_CHECKLIST.lower()
    for required in (
        "standard of review",
        "de novo",
        "abuse of discretion",
        "plain error",
        "preserved",
    ):
        assert required in c, f"checklist missing: {required}"


def test_is_authoritative_domain_preference():
    assert _is_authoritative("https://www.law.cornell.edu/uscode/text/18/922")
    assert _is_authoritative("https://api.oyez.org/cases")
    assert _is_authoritative("https://www.govinfo.gov/content/pkg/USCODE")
    assert _is_authoritative("https://www.courtlistener.com/opinion/1/")
    assert not _is_authoritative("https://example.com/random-blog")


def test_pick_fetch_urls_prefers_authoritative():
    res = [
        {"url": "https://example.com/blog"},
        {"url": "https://www.law.cornell.edu/uscode/text/18"},
        {"url": "https://api.oyez.org/cases/2023"},
        {"url": "https://other.org/x"},
    ]
    picked = _pick_fetch_urls(res, max_urls=3)
    assert picked[0] == "https://www.law.cornell.edu/uscode/text/18", (
        "authoritative legal domains must be fetched first"
    )
    assert picked[1] == "https://api.oyez.org/cases/2023"
    assert len(picked) == 3


def test_pick_fetch_urls_dedupes_and_bounds():
    res = [{"url": "https://a.com"}, {"url": "https://a.com"}, {"url": "https://b.com"}]
    picked = _pick_fetch_urls(res, max_urls=1)
    assert picked == ["https://a.com"]


def test_law_as_of_threads_snapshot():
    scope = {"jurisdictions": {"ny": {"snapshot": "v2026.07"}}}
    assert _law_as_of(scope, "ny") == "v2026.07"
    scope2 = {"jurisdictions": {"ny": {"snapshot": ""}}}
    assert "unknown" in _law_as_of(scope2, "ny")


@pytest.mark.asyncio
async def test_web_research_never_raises_on_outage():
    """Web tools unavailable (import failure or handler error) must degrade to
    an empty source list, never raise."""
    import swarm_os.lib.mcp.web_search as ws

    with (
        patch.object(
            ws,
            "web_search_handler",
            new=AsyncMock(return_value={"ok": False, "error": "no key"}),
        ),
        patch.object(
            ws, "web_fetch_handler", new=AsyncMock(return_value={"ok": False})
        ),
    ):
        res = await _web_research("test question", max_fetches=2)
    assert res["ok"] is False
    assert res["web_sources"] == []


@pytest.mark.asyncio
async def test_web_research_fetches_authoritative_urls():
    import swarm_os.lib.mcp.web_search as ws

    search = {
        "ok": True,
        "results": [
            {"url": "https://www.law.cornell.edu/uscode/text/18/922", "title": "LII"},
            {"url": "https://example.com/blog", "title": "blog"},
        ],
    }
    fetched = {
        "ok": True,
        "title": "LII page",
        "content": "the actual statute text here",
    }

    async def fake_fetch(params, trace_hook=None):
        return dict(fetched, url=params["url"])

    with (
        patch.object(ws, "web_search_handler", new=AsyncMock(return_value=search)),
        patch.object(ws, "web_fetch_handler", new=fake_fetch),
    ):
        res = await _web_research("firearm possession", max_fetches=2)
    assert res["ok"] is True
    assert len(res["web_sources"]) == 2
    # Authoritative source flagged.
    assert res["web_sources"][0]["authoritative"] is True
    assert "law.cornell.edu" in res["web_sources"][0]["url"]


@pytest.mark.asyncio
async def test_deep_research_fail_closed_jurisdiction_gate():
    """A question with no detectable jurisdiction must refuse (never synthesize
    a guess)."""
    from swarm_os.services.legal.deep_research import deep_research
    import swarm_os.services.legal.legal_advisor as la

    with patch.object(
        la, "corpus_scope", new=AsyncMock(return_value={"jurisdictions": {}})
    ):
        res = await deep_research("what are my rights?", web=False)
    assert res.ok is False
    assert "jurisdiction" in res.message.lower()


@pytest.mark.asyncio
async def test_deep_research_offline_uses_corpus_and_verifies():
    """Offline (web=False) deep research must run over the local corpora and
    return a verified answer — the fail-closed offline path."""
    from swarm_os.services.legal.deep_research import deep_research
    from swarm_os.services.legal.citation_verify import VerifyResponse
    import swarm_os.services.legal.legal_advisor as la

    scope = {
        "jurisdictions": {
            "ny": {
                "expected": 40102,
                "ingested": 8000,
                "pct": 20.0,
                "complete": False,
                "snapshot": "v2026.07",
            }
        }
    }
    with (
        patch.object(la, "corpus_scope", new=AsyncMock(return_value=scope)),
        patch(
            "swarm_os.services.legal.legal_search.search_statutes",
            new=AsyncMock(
                return_value=[
                    {
                        "citation": "N.Y. RPA Law § 235-b",
                        "section_title": "t",
                        "jurisdiction": "ny",
                        "content": "x",
                    }
                ]
            ),
        ),
        patch(
            "swarm_os.services.legal.legal_search.search_cases",
            new=AsyncMock(
                return_value=[
                    {
                        "citation": "252 F.3d 238",
                        "section_title": "Simeonov",
                        "court": "2d Cir.",
                        "circuit": "2d",
                        "year": 2001,
                        "tier": 1,
                        "content": "y",
                    }
                ]
            ),
        ),
        patch(
            "swarm_os.services.legal.deep_research._persona_synthesize",
            new=AsyncMock(return_value="The rule applies under N.Y. RPA Law § 235-b."),
        ),
        patch(
            "swarm_os.services.legal.citation_verify.verify_citations",
            new=AsyncMock(
                return_value=VerifyResponse(
                    ok=True,
                    citations=[],
                    message="0 parsed",
                    stats={
                        "count": 0,
                        "verified": 0,
                        "fabricated": 0,
                        "ambiguous": 0,
                        "unverified": 0,
                        "unparsed": 0,
                        "skipped": 0,
                    },
                )
            ),
        ),
    ):
        res = await deep_research(
            "my landlord won't return deposit", jurisdiction="ny", web=False
        )
    assert res.ok is True
    assert res.law_as_of == "v2026.07"
    assert res.verification["checked"] is True
    assert res.message.startswith("Deep research over")
    assert "v2026.07" in res.message  # temporal grounding surfaced
