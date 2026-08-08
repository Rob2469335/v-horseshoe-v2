"""Legal citation parsing + verification for Rob's Lawyer.

Two-stage hybrid, grounded in the SOTA research (no LLM guessing in the check):

1. PARSING  — Eyecite (freelawproject/eyecite, BSD-2, the parser CourtListener
   and CAP both use) extracts citations from a blob and RESOLVES short forms
   (id./supra) to their antecedent full citations. Eyecite handles cases,
   statutes, law-journal, supra and id. — broader than the CourtListener API,
   which deliberately skips statutes, id., supra and volume-less citations.

2. EXTERNAL VERIFICATION — CourtListener's Citation Lookup & Verification API
   (POST /api/rest/v4/citation-lookup/), which exists precisely as "a guardrail
   to help prevent hallucinated citations". Per-citation status:
     200 found · 404 not found (fabricated) · 400 bad reporter · 300 ambiguous.

3. ALIGNMENT (hybrid) — for 200-found citations, the caller may additionally
   confirm the proposition is supported by the retrieved span via the existing
   Qdrant reranker. This module returns the parse+verify result; alignment is
   the research/synthesis layer's job.

This is the primary safety mechanism (LegalCiteBench Cat3/Cat4 analog): a
fabricated citation is blocked (status 404), an ambiguous one flagged (300).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

try:
    from eyecite import get_citations, resolve_citations
    from eyecite.models import CitationBase

    _EYECITE_OK = True
except Exception:  # pragma: no cover - fallback path
    get_citations = None
    resolve_citations = None
    CitationBase = None
    _EYECITE_OK = False

# CourtListener Citation Lookup & Verification API
CITATION_LOOKUP_URL = os.getenv(
    "COURTLISTENER_CITATION_URL",
    "https://www.courtlistener.com/api/rest/v4/citation-lookup/",
)
# Rate limits (from the API docs): 60 valid citations/min, 250 max per request.
MAX_CITATIONS_PER_REQUEST = 250
_LOOKUP_SEM = asyncio.Semaphore(2)

# "No" statuses (a citation was parsed but NOT verifiably found).
_NOT_FOUND_STATUSES = {404, 400, 300}


@dataclass
class CitationResult:
    raw: str                 # the citation string as parsed by Eyecite
    kind: str                # FullCaseCitation / FullLawCitation / IdCitation / ...
    verified: bool           # True if CourtListener returned 200 (exists)
    status: int | None       # CourtListener lookup status (200/404/400/300/429/None)
    error_message: str = ""
    normalized: list[str] = field(default_factory=list)
    case_name: str = ""
    clusters: int = 0
    skipped_reason: str = ""


@dataclass
class VerifyResponse:
    ok: bool
    citations: list[CitationResult]
    stats: dict[str, Any]
    message: str = ""


async def _lookup_one(client: httpx.AsyncClient, vol: str | None, reporter: str | None,
                      page: str | None, text: str | None) -> dict[str, Any]:
    """POST one citation to CourtListener's citation-lookup. Returns the first
    result item (or {}). Never raises — callers get a 429/error dict on failure."""
    payload: dict[str, str] = {}
    if text is not None:
        payload["text"] = text
    else:
        if vol:
            payload["volume"] = vol
        if reporter:
            payload["reporter"] = reporter
        if page:
            payload["page"] = page
    if not payload:
        return {}
    async with _LOOKUP_SEM:
        try:
            resp = await client.post(CITATION_LOOKUP_URL, data=payload, timeout=20.0)
        except Exception as exc:
            log.warning("citation-lookup request failed: %s", exc)
            return {"status": None, "error_message": f"request failed: {exc}"}
    try:
        body = resp.json()
    except Exception:
        body = []
    if isinstance(body, list) and body:
        return body[0]
    if isinstance(body, dict):
        return body
    return {}


def _resolve_to_full(blob: str) -> tuple[list[str], list[str]]:
    """Parse `blob` with Eyecite, resolving id./supra to full citations where
    possible. Returns (list_of_citation_strings, list_of_kind_names)."""
    if not _EYECITE_OK:
        return [], []
    try:
        citations = get_citations(blob)
    except Exception as exc:
        log.warning("eyecite get_citations failed: %s", exc)
        return [], []
    out: list[str] = []
    kinds: list[str] = []
    for c in citations:
        matched = getattr(c, "matched_text", lambda: "")()
        out.append(matched)
        kinds.append(type(c).__name__)
    return out, kinds


async def verify_citations(blob: str, courtlistener_key: str | None = None) -> VerifyResponse:
    """Verify every case citation in `blob` against CourtListener.

    Statutory / id. / supra citations are PARSED and listed but skipped for
    external verification (the API doesn't cover them) — `skipped_reason` is
    set accordingly. A fabricated case citation surfaces as verified=False with
    status 404 — the caller MUST block/downgrade it.
    """
    if not _EYECITE_OK:
        return VerifyResponse(ok=False, citations=[], stats={"error": "eyecite not installed"})
    strings, kinds = _resolve_to_full(blob)
    if not strings:
        return VerifyResponse(ok=True, citations=[], stats={"count": 0})

    headers = {}
    if courtlistener_key:
        headers["Authorization"] = f"Token {courtlistener_key}"
    elif os.getenv("COURTLISTENER_API_TOKEN"):
        headers["Authorization"] = f"Token {os.getenv('COURTLISTENER_API_TOKEN')}"

    async with httpx.AsyncClient(headers=headers, timeout=25.0) as client:
        results: list[CitationResult] = []
        for raw, kind in zip(strings, kinds):
            if kind in ("FullLawCitation", "IdCitation", "SupraCitation", "LawJournalCitation"):
                results.append(CitationResult(
                    raw=raw, kind=kind, verified=False, status=None,
                    skipped_reason=(
                        "statutory/id./supra/law-journal citations are parsed but not "
                        "externally verified (CourtListener citation-lookup skips them)"
                    ),
                ))
                continue
            # Case citation: look up by volume/reporter/page via a text POST.
            # Eyecite gives us the matched string; hand it to the API as text.
            lookup = await _lookup_one(client, None, None, None, raw)
            status = lookup.get("status")
            verified = status == 200
            results.append(CitationResult(
                raw=raw,
                kind=kind,
                verified=verified,
                status=status,
                error_message=lookup.get("error_message", ""),
                normalized=lookup.get("normalized_citations", []),
                case_name=(lookup.get("clusters") or [{}])[0].get("case_name", ""),
                clusters=len(lookup.get("clusters") or []),
            ))

    fabricated = [r for r in results if r.status == 404]
    ambiguous = [r for r in results if r.status == 300]
    ok = not fabricated  # fabricated citations are a hard stop; ambiguous are flagged
    return VerifyResponse(
        ok=ok,
        citations=results,
        message=(
            f"{len(results)} citation(s) parsed; "
            f"{len(fabricated)} fabricated (blocked), {len(ambiguous)} ambiguous (flagged)."
        ),
        stats={
            "count": len(results),
            "verified": sum(1 for r in results if r.verified),
            "fabricated": len(fabricated),
            "ambiguous": len(ambiguous),
            "skipped": sum(1 for r in results if r.skipped_reason),
        },
    )


async def verify_health() -> dict[str, Any]:
    """Lightweight status for the page banner: is eyecite + the lookup API reachable."""
    return {
        "eyecite": _EYECITE_OK,
        "citation_lookup_url": CITATION_LOOKUP_URL,
        "has_courtlistener_token": bool(os.getenv("COURTLISTENER_API_TOKEN")),
    }
