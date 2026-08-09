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
