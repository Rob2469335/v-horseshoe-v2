"""Legal assistant routes for Rob's Lawyer.

Endpoints:
- POST /legal/verify-citations — parse a text blob with Eyecite and verify each
  case citation against CourtListener's Citation Lookup & Verification API.
- GET  /legal/health — status of the verification stack (eyecite, token, URL).

The verification endpoint is the primary safety mechanism: a fabricated citation
(404) blocks the response; an ambiguous one (300) is flagged. The page surfaces
per-citation status.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from swarm_os.services.legal.citation_verify import verify_citations, verify_health
from swarm_os.services.legal.legal_search import search_statutes, search_cases, JURISDICTIONS, TIERS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/legal", tags=["legal"])


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="Natural-language legal question or terms")
    jurisdiction: str | None = Field(None, description=f"Filter to one of {JURISDICTIONS}; None = all scoped")
    top_k: int = Field(8, ge=1, le=50, description="Number of results to return after reranking")


class SearchResponse(BaseModel):
    ok: bool
    results: list[dict[str, Any]]
    message: str = ""
    degraded: bool = False


@router.post("/search", response_model=SearchResponse)
async def legal_search(req: SearchRequest) -> SearchResponse:
    """Hybrid statute search over the ingested legal_statutes corpus. Dense
    vector search (optionally jurisdiction-filtered) then cross-encoder rerank.
    Degrades gracefully — an embed/reranker outage returns what it can."""
    try:
        results = await search_statutes(req.query, req.jurisdiction, req.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        log.exception("legal search failed")
        raise HTTPException(status_code=500, detail="Legal search failed")
    return SearchResponse(
        ok=True,
        results=results,
        message=f"{len(results)} statute section(s) found.",
    )


class CaseSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="Natural-language legal question or case-law terms")
    tier: int | None = Field(None, description=f"Manifest tier filter: {TIERS} (1 controlling / 2 backbone / 3 context / 4 Batson)")
    circuit: str | None = Field(None, description='Circuit filter, e.g. "2d" or "scotus"')
    batson: bool | None = Field(None, description="Filter to the Batson-authority subset (True/False)")
    top_k: int = Field(8, ge=1, le=50, description="Number of results to return after reranking")


@router.post("/cases/search", response_model=SearchResponse)
async def legal_case_search(req: CaseSearchRequest) -> SearchResponse:
    """Hybrid case-law search over the ingested `legal_cases` corpus (the
    curated manifest from 110 F.4th 455 + Batson authorities). Dense vector
    search with optional tier/circuit/batson filter then cross-encoder rerank.
    Degrades gracefully — an embed/reranker outage returns what it can."""
    try:
        results = await search_cases(req.query, req.tier, req.circuit, req.batson, req.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        log.exception("legal case search failed")
        raise HTTPException(status_code=500, detail="Legal case search failed")
    return SearchResponse(
        ok=True,
        results=results,
        message=f"{len(results)} case section(s) found.",
    )


class VerifyCitationsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=60000, description="Text blob to parse + verify citations in")


class VerifyCitationsResponse(BaseModel):
    ok: bool
    citations: list[dict[str, Any]]
    stats: dict[str, Any]
    message: str = ""


@router.post("/verify-citations", response_model=VerifyCitationsResponse)
async def verify_citations_endpoint(req: VerifyCitationsRequest) -> VerifyCitationsResponse:
    """Parse `req.text` with Eyecite and verify each case citation against
    CourtListener. Fabricated citations are reported with verified=false (404)
    so the caller can block/downgrade."""
    try:
        async with asyncio.timeout(120.0):
            res = await verify_citations(req.text)
    except Exception:
        log.exception("verify-citations failed")
        raise HTTPException(status_code=500, detail="Citation verification failed")
    return VerifyCitationsResponse(
        ok=res.ok,
        citations=[c.__dict__ for c in res.citations],
        stats=res.stats,
        message=res.message,
    )


@router.get("/health")
async def legal_health() -> dict[str, Any]:
    return await verify_health()


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000, description="Legal question in plain English")


@router.post("/ask")
async def legal_ask(req: AskRequest) -> dict[str, Any]:
    """M3 vertical slice: intake → research → synthesis, with the corpus-scope
    marker IN the response and fail-closed jurisdiction gating. The answer is
    structurally non-final: `corpus_scope` always reflects live ingestion state,
    so a partial-jurisdiction answer can never be mistaken for complete."""
    from swarm_os.services.legal.legal_advisor import advise
    try:
        res = await advise(req.question)
    except Exception:
        log.exception("legal ask failed")
        raise HTTPException(status_code=500, detail="Legal advisor failed")
    return {
        "ok": res.ok,
        "fail_closed": res.fail_closed,
        "jurisdiction": res.jurisdiction,
        "answer": res.answer,
        "citations": res.citations,
        "verification": res.verification,
        "corpus_scope": res.corpus_scope,   # ← the in-band, live marker
        "message": res.message,
    }


@router.get("/corpus-scope")
async def legal_corpus_scope() -> dict[str, Any]:
    """Live ingestion state per jurisdiction — the structural marker source."""
    from swarm_os.services.legal.legal_advisor import corpus_scope
    return await corpus_scope()


class BriefCheckRequest(BaseModel):
    argument: str = Field(..., min_length=1, max_length=60000,
                          description="The Argument section text of a brief/motion to check")
    retrieved_statutes: list[str] = Field(default_factory=list,
                                          description="Statute citations retrieved for this matter")
    retrieved_cases: list[str] = Field(default_factory=list,
                                       description="Case citations retrieved for this matter")
    source_by_cite: dict[str, str] = Field(default_factory=dict,
                                           description="{citation_key: source_content} for the fidelity pass")
    word_count: int | None = Field(default=None,
                                   description="Exact word count (for FRAP 32) if the caller counted it")


@router.post("/brief/check")
async def legal_brief_check(req: BriefCheckRequest) -> dict[str, Any]:
    """Post-generation brief checker (the conjunctive-deliverable guard the
    Harvey Legal Agent Benchmark shows models miss ~80% of the time). Parses the
    Argument, flags assertions lacking a citation, aligns every citation against
    the retrieved corpora (M4 statutes + M6 cases), runs the LegalCiteTrust
    fidelity pass (each citation supports its sentence), and checks FRAP 32
    type-volume + certificate. `ok: false` means fix before filing."""
    from swarm_os.services.legal.brief_draft import (
        check_brief, render_check, check_frap32, check_fidelity,
    )
    try:
        check = check_brief(req.argument, req.retrieved_statutes, req.retrieved_cases)
        fidelity = check_fidelity(req.argument, req.source_by_cite)
        frap32 = check_frap32({"text": req.argument, "word_count": req.word_count} if req.word_count else req.argument)
    except Exception:
        log.exception("brief check failed")
        raise HTTPException(status_code=500, detail="Brief check failed")
    overall_ok = check["ok"] and fidelity["rate"] == 1.0 and frap32["ok"]
    return {
        "ok": overall_ok,
        "assertions": check["assertions"],
        "uncited_assertions": check["uncited_assertions"],
        "uncited_count": check["uncited_count"],
        "unaligned_statutes": check["unaligned_statutes"],
        "unaligned_cases": check["unaligned_cases"],
        "fidelity": fidelity,
        "frap32": frap32,
        "summary": render_check(check) + "\n" + _render_fidelity(fidelity) + "\n" + _render_frap32(frap32),
    }


def _render_fidelity(fidelity: dict) -> str:
    if not fidelity.get("checked"):
        return "Fidelity: no citable assertions to check."
    return (f"Fidelity: {len(fidelity['unsupporting'])} unsupported citation(s) "
            f"({fidelity['rate']:.0%} supporting)")


def _render_frap32(f: dict) -> str:
    return (f"FRAP 32: {f['words']}/{f['limit']} words"
            f" ({'OVER' if f['over'] else 'OK'}), certificate "
            f"{'present' if f['has_certificate'] else 'MISSING'}")



class BriefSkeletonRequest(BaseModel):
    issues: list[str] = Field(default_factory=list,
                              description="The discrete issues the Argument will address")


@router.post("/brief/skeleton")
async def legal_brief_skeleton(req: BriefSkeletonRequest) -> dict[str, Any]:
    """The 2d Cir. brief skeleton (FRAP 28 + L.R. 28.1) as a machine-checkable
    outline — structure-first drafting so the conjunctive deliverable isn't
    missed (the benchmark-identified failure mode)."""
    from swarm_os.services.legal.brief_draft import draft_skeleton
    return {"sections": draft_skeleton(req.issues)}


class DeepResearchRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          description="The legal question to research")
    jurisdiction: str | None = Field(default=None,
                                     description="Optional jurisdiction override (ny/nj/ga/nc/federal)")
    web: bool = Field(default=True,
                      description="Enable live web research (LII/Oyez/GovInfo/CourtListener via web_fetch)")
    max_fetches: int = Field(default=3, ge=0, le=5,
                             description="Max authoritative URLs to deep-fetch")


@router.post("/deep-research")
async def legal_deep_research(req: DeepResearchRequest) -> dict[str, Any]:
    """The AI criminal-defense attorney deep-research mode: persona-conditioned
    multi-source research (local statute+case corpora + live web: LII/Oyez/
    GovInfo/CourtListener + the citation-graph authorities) with temporal
    grounding and the fail-closed citation-verification seam. `web=false`
    forces corpus-only (offline-safe)."""
    from swarm_os.services.legal.deep_research import deep_research
    try:
        res = await deep_research(req.question, jurisdiction=req.jurisdiction,
                                  web=req.web, max_fetches=req.max_fetches)
    except Exception:
        log.exception("deep research failed")
        raise HTTPException(status_code=500, detail="Deep research failed")
    return {
        "ok": res.ok,
        "jurisdiction": res.jurisdiction,
        "answer": res.answer,
        "issue": res.issue,
        "sources": res.sources,
        "web_sources": res.web_sources,
        "verification": res.verification,
        "corpus_scope": res.corpus_scope,
        "law_as_of": res.law_as_of,
        "message": res.message,
    }


class CitatorRequest(BaseModel):
    refresh: bool = Field(default=False,
                          description="Re-poll authorities already in state")
    max_authorities: int | None = Field(default=None,
                                        description="Cap how many authorities to poll this run")


@router.post("/citator")
async def legal_citator(req: CitatorRequest) -> dict[str, Any]:
    """The 'Still Good Law?' forward-citing monitor (the Shepard's/KeyCite alert
    replacement). Polls the manifest authorities' forward-citing cases via
    CourtListener `/opinions-cited/`, classifies treatment with the existing
    taxonomy, and returns adverse-treatment ALERTS (the candor obligation)."""
    from swarm_os.services.legal.citator import poll_authority, render_citator_report
    from swarm_os.services.legal.case_corpus import CASE_MANIFEST
    try:
        async with asyncio.timeout(120.0):
            report = await poll_authority(CASE_MANIFEST, max_authorities=req.max_authorities,
                                          refresh=req.refresh)
    except Exception:
        log.exception("citator poll failed")
        raise HTTPException(status_code=500, detail="Citator poll failed")
    return {
        "ok": report.ok,
        "alerts": report.alerts,
        "authorities": report.authorities,
        "message": report.message,
        "markdown": render_citator_report(report),
    }


class DocketRequest(BaseModel):
    docket_number: str = Field(..., min_length=1, description="The RECAP docket number")


@router.post("/docket")
async def legal_docket(req: DocketRequest) -> dict[str, Any]:
    """The RECAP docket + FRAP deadline ledger: pulls the docket's entries and
    computes FRAP 4(b)/31(a) deadlines with the weekday rule."""
    from swarm_os.services.legal.docket import fetch_docket, render_docket_ledger
    try:
        async with asyncio.timeout(120.0):
            ledger = await fetch_docket(req.docket_number)
    except Exception:
        log.exception("docket fetch failed")
        raise HTTPException(status_code=500, detail="Docket fetch failed")
    return {
        "docket_number": ledger.docket_number,
        "case_name": ledger.case_name,
        "error": ledger.error,
        "triggers": [{"kind": t.kind, "date": t.date.isoformat() if t.date else None}
                     for t in ledger.triggers],
        "deadlines": [{"label": d.label, "due": d.due.isoformat() if d.due else None,
                       "days_remaining": d.days_remaining, "rule": d.rule, "trigger": d.trigger}
                      for d in ledger.deadlines],
        "markdown": render_docket_ledger(ledger),
    }


class MootRequest(BaseModel):
    judges: list[str] = Field(default_factory=list,
                              description="Panel judge names (e.g. ['Walker', 'Raggi'])")
    issues: list[dict[str, str]] = Field(default_factory=list,
                                         description="[{issue, outline}] the argument's issues")
    argument_by_issue: dict[str, str] = Field(default_factory=dict,
                                              description="issue -> counsel's argument text")
    authorities: list[str] = Field(default_factory=list,
                                   description="Authorities relied on")
    fetch_profiles: bool = Field(default=True,
                                 description="Fetch judge profiles from CourtListener")


@router.post("/moot")
async def legal_moot(req: MootRequest) -> dict[str, Any]:
    """The AI moot-court oral-argument prep: per-judge profiles from their prior
    opinions + a simulated bench that questions each issue from that judge's
    recorded concerns."""
    from swarm_os.services.legal.moot import run_bench, render_bench
    try:
        session = await run_bench(req.judges, req.issues, req.argument_by_issue,
                                  req.authorities, fetch_profiles=req.fetch_profiles)
    except Exception:
        log.exception("moot bench failed")
        raise HTTPException(status_code=500, detail="Moot bench failed")
    return {
        "ok": session.ok,
        "judges": [{"name": p.name, "topics": p.topics, "error": p.error}
                   for p in session.judges],
        "questions": [{"judge": q.judge, "issue": q.issue, "question": q.question}
                      for q in session.questions],
        "message": session.message,
        "markdown": render_bench(session),
    }


# ---------------------------------------------------------------------------
# Trial analysis — the criminal-defense record layer (your own transcript)
# ---------------------------------------------------------------------------
@router.get("/trial/overview")
async def legal_trial_overview() -> dict[str, Any]:
    """High-level structure of the defendant's own trial record (all ingested
    transcript days): days, pages, passage counts."""
    from swarm_os.services.legal.trial_advisor import _load_indices, trial_overview
    try:
        indices = _load_indices()
        if not indices:
            return {"ok": False, "error": "No trial transcripts found in data/legal/transcripts. "
                    "Add your corrected .txt transcript files there first."}
        return {"ok": True, **trial_overview(indices)}
    except Exception:
        log.exception("trial overview failed")
        raise HTTPException(status_code=500, detail="Trial overview failed")


@router.get("/trial/attorneys")
async def legal_trial_attorneys() -> dict[str, Any]:
    """Per-attorney profiles across the trial: objections, examinations, key
    statements, page range — each page-cited from the record."""
    from swarm_os.services.legal.trial_advisor import (
        _load_indices, build_attorney_profiles,
    )
    try:
        indices = _load_indices()
        profiles = build_attorney_profiles(indices)
        return {
            "ok": True,
            "attorneys": [
                {
                    "key": p.key,
                    "name": p.name,
                    "represents": p.represents,
                    "word_count": p.word_count,
                    "page_range": p.page_range,
                    "objections": p.objections[:80],
                    "objection_count": len(p.objections),
                    "examinations": p.examinations[:60],
                    "examination_count": len(p.examinations),
                    "key_statements": p.key_statements[:30],
                }
                for p in profiles.values()
            ],
        }
    except Exception:
        log.exception("trial attorneys failed")
        raise HTTPException(status_code=500, detail="Trial attorney profiles failed")


@router.get("/trial/errors")
async def legal_trial_errors() -> dict[str, Any]:
    """Record patterns a criminal-defense / post-conviction review would
    investigate (preserved errors, evidence/chain-of-custody, confrontation,
    jury selection) — page-cited, framed as questions for qualified counsel."""
    from swarm_os.services.legal.trial_advisor import (
        _load_indices, build_error_flags, build_key_events, build_phone_evidence_events,
    )
    try:
        indices = _load_indices()
        return {
            "ok": True,
            "flags": build_error_flags(indices),
            "key_events": build_key_events(indices),
            "phone_evidence_events": build_phone_evidence_events(indices),
            "disclaimer": (
                "This tool reports WHAT the record shows and WHERE. It does not "
                "conclude that counsel was ineffective or that the government "
                "tampered with evidence — those are legal conclusions for a "
                "qualified attorney. The patterns below are the shapes a federal "
                "criminal-defense / §2255 review would investigate."
            ),
        }
    except Exception:
        log.exception("trial errors failed")
        raise HTTPException(status_code=500, detail="Trial error analysis failed")


class TrialSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500,
                       description="Search text over the trial record")
    limit: int = Field(20, ge=1, le=100)


@router.post("/trial/search")
async def legal_trial_search(req: TrialSearchRequest) -> dict[str, Any]:
    """Page-cited search over the defendant's own trial transcript (all days)."""
    from swarm_os.services.legal.trial_advisor import _load_indices, search_record
    try:
        indices = _load_indices()
        return {"ok": True, "hits": search_record(indices, req.query, req.limit)}
    except Exception:
        log.exception("trial search failed")
        raise HTTPException(status_code=500, detail="Trial search failed")


class TrialSpeakerRequest(BaseModel):
    speaker: str = Field(..., min_length=2, max_length=100,
                         description="Attorney speaker prefix, e.g. 'MR. DINNERSTEIN'")
    limit: int = Field(25, ge=1, le=100)


@router.post("/trial/speaker")
async def legal_trial_speaker(req: TrialSpeakerRequest) -> dict[str, Any]:
    """Every passage spoken by one attorney, page-cited across all days."""
    from swarm_os.services.legal.trial_advisor import _load_indices, speaker_summary
    try:
        indices = _load_indices()
        return {"ok": True, "passages": speaker_summary(indices, req.speaker, req.limit)}
    except Exception:
        log.exception("trial speaker failed")
        raise HTTPException(status_code=500, detail="Trial speaker lookup failed")
