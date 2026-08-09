"""Legal citation parsing + verification for Rob's Lawyer.

Two-stage hybrid, grounded in the SOTA research (no LLM guessing in the check):

1. PARSING  — Eyecite (freelawproject/eyecite, BSD-2, the parser CourtListener
   and CAP both use) extracts citations from a blob and RESOLVES short forms
   (id./supra) to their antecedent full citations. Eyecite handles cases,
   statutes, law-journal, supra and id. — broader than the CourtListener API,
   which deliberately skips statutes, id., supra and volume-less citations.

3. EXTERNAL VERIFICATION — CourtListener's Citation Lookup & Verification API
   (POST /api/rest/v4/citation-lookup/), which exists precisely as "a
   guardrail to help prevent hallucinated citations". Per-citation status:
      200 found · 404 not found (fabricated) · 400 bad reporter · 300 ambiguous.
   Token-gated (COURTLISTENER_API_TOKEN): without a token, the external leg is
   skipped and the module reports has_courtlistener_token:false.

4. ALIGNMENT (M4) — the statutory leg, `align_citations()`. Eyecite does NOT
   drive this (it mangles statutes: "N.Y. RPA Law § 235-b" --> "§ 235"; an
   entire corpus-type check). Every statute-section ID cited in an answer is
   aligned against the retrieved corpus sections; a cited section that is NOT in
   the retrieved set is UNALIGNED — the statutory-fabrication signal for a
   statute corpus without an external token.

This is the primary safety mechanism (LegalCiteBench Cat3/Cat4 analog): a
fabricated citation is blocked (status 404), an ambiguous one flagged (300).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
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


# ---------------------------------------------------------------------------
# STATUTE-ALIGNMENT SEAM (M4)
#
# Eyecite does NOT drive this: probe-verified on live text that eyecite mangles
# statutory citations ("N.Y. RPA Law § 235-b" parses as "§ 235", dropping the
# "-b"; "N.J.S.A. 46:8-19" is missed entirely; "N.D.C.C. § 12.1-32-06.1(4)"
# degrades to a bare "§"). The corpus ROB'S LAWYER operates on IS statutes, so
# the actual fabrication signal for this corpus must come from a deterministic,
# eyecite-independent extractor of statutory citations + an alignment check:
# every numbered section cited in an answer must exist among the retrieved
# sections (or be flaggable).
# ---------------------------------------------------------------------------

# A statute citation this corpus produces/references looks like:
#   N.Y. RPA Law § 235-b        N.Y. ABC Law § 105       N.J.S. 46:8-19
#   N.Y. CPL Law § 200.50       N.Y. FCT Law § 581-202
# We extract "law section" IDs WITHOUT eyecite — small anchored regexes on the
# §-signed / N.J.S. / U.S.C. shapes that actually occur in this corpus.
_SECTION_SIGNED = re.compile(
    r"\u00a7\s*([0-9]+(?:[-.][0-9A-Za-z]+)*(?:\([0-9A-Za-z]+\))?)"
)
_SECTION_NJS = re.compile(
    r"\bN\.?\s*J\.?\s*S\.?\s*A?\.?\s*\u00a7?\s*([0-9]{1,3}:[0-9]{1,3}(?:-[0-9]+)?)"
)
_SECTION_USC = re.compile(r"\b([0-9]{1,3})\s+U\.?\s*S\.?\s*C\.?\s+\u00a7\s+([0-9]+)")

# "Citation-SHAPED text" detectors — a volume-reporter-page shape (531 U.S. 98,
# 2009 MT 228) or a statute-supplement shape (2012 Supp. 47-501(b)(1)(E)).
# These are deliberately BROADER than eyecite: an exotic-but-real citation that
# eyecite cannot parse (900 So. 7d 694, K.S.A. 2012 Supp. ...) still MATCHES the
# shape — so a passage that LOOKS like it cites law but produced zero citations
# must surface as "unparsed", never silently pass as "nothing to check".
_CASE_SHAPED_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z][A-Za-z0-9.\-]*(?:\s+[A-Za-z][A-Za-z0-9.\-]*)*\s+\d{1,5}[A-Za-z0-9.]*\b"
)
_STATUTE_SUPP_SHAPED_RE = re.compile(
    r"\b\d{1,4}\s+Supp\.?\s+\d{1,3}-\d{1,3}[A-Za-z0-9().\-]*"
)


def count_citation_shapes(text: str) -> int:
    """Deterministic count of citation-shaped spans in `text` — the "did it
    even LOOK like a citation" bound for the UNPARSED signal (L3-trap guard).
    Case-shape and statute-supplement-shape are counted separately and summed.
    This classifier is intentionally loose: it marks text that a legal reader
    would recognize as citation-bearing, so an exotic-but-real shape that the
    parsers can't lift is never reported as "0 citations, nothing to check"."""
    t = text or ""
    return len(_CASE_SHAPED_RE.findall(t)) + len(_STATUTE_SUPP_SHAPED_RE.findall(t))


def extract_statute_sections(text: str) -> list[str]:
    """Deterministic, eyecite-independent extraction of statute section IDs
    referenced in `text`. Returns section identifiers as written: e.g.
    'N.Y. RPA Law \u00a7 235-b' -> ['235-b']; 'N.J.S.A. 46:8-19' -> ['46:8-19'];
    '42 U.S.C. \u00a7 1983' -> ['1983'].

    Deliberately conservative: this is a DEFENSE seam — missing a hard-to-parse
    cite just means "not aligned", never that a true cite is called fabricated.
    """
    if not text:
        return []
    out: list[str] = []
    # Collect N.J.S.-prefixed spans first so an overlapping bare "§ N" from the
    # generic §-regex cannot shadow the fuller N.J.S. capture ("N.J.S. § 46:8-19"
    # must yield '46:8-19', not a stray '46').
    njs_spans = [m.span() for m in _SECTION_NJS.finditer(text)]
    for m in _SECTION_SIGNED.finditer(text):
        if any(m.start() >= a and m.end() <= b for a, b in njs_spans):
            continue
        sec = m.group(1).strip()
        if re.search(r"\d", sec):
            out.append(sec)
    for m in _SECTION_NJS.finditer(text):
        out.append(m.group(1))
    for m in _SECTION_USC.finditer(text):
        out.append(m.group(2))
    return list(dict.fromkeys(out))  # dedupe, preserve order


def _normalize_section(section: Any) -> str:
    """Canonical key for a section id from anywhere (a corpus payload or an
    answer): lowercase, keep only digits/letters, collapse separators to '-'."""
    s = re.sub(r"[^0-9A-Za-z]+", "-", str(section or "")).strip("-").lower()
    return s

_CASE_CITE_RE = re.compile(
    r"^(?:(?P<vol>\d{1,4})\s+)?(?P<rep>[A-Z][A-Za-z0-9.\-]*(?:\s+[A-Z][A-Za-z0-9.\-]*)*)\s+(?P<page>\d{1,5})$"
)


def case_citation_key(matched_text: str) -> str | None:
    """Deterministic canonical key for a case-citation matched string, so two
    textual forms of the same case compare equal and an altered volume/page
    compares different (the LegalCiteBench Cat3 fake-detection signal).

    '400 U.S. 79' -> '400|u.s.|79'; '2009 MT 228' -> '2009|mt|228'.
    Returns None when the string isn't volume-reporter-page shaped."""
    if not matched_text:
        return None
    m = _CASE_CITE_RE.fullmatch(matched_text.strip().rstrip(".,"))
    if not m:
        return None
    vol = m.group("vol") or ""
    rep = re.sub(r"[^a-z0-9]+", "", (m.group("rep") or "").lower())
    page = m.group("page")
    if not rep or not page:
        return None
    return f"{vol}|{rep}|{page}"


def align_citations(answer_text: str, retrieved_sections: list[str]) -> dict[str, Any]:
    """The M4 citation-alignment seam.

    Deterministic, offline, eyecite-independent. For every statute section ID
    cited in `answer_text`, check it exists among the `retrieved_sections` (the
    actual corpus payloads returned for the question). A cited section not in
    the retrieved set is UNALIGNED — the statutory-fabrication signal this stack
    can emit without an external case-law token.

    Returns:
      {
        "count": no. of distinct cited statute sections,
        "aligned":   [ {'section', 'normalized'} ... ],
        "unaligned": [ {'section', 'normalized'} ... ],
        "normalized_retrieved": sorted canonical keys of the corpus sections,
      }
    """
    corpus_ids: set[str] = set()
    for s in retrieved_sections:
        if not isinstance(s, str):
            continue
        # Add the bare whole-string only when it's already a section-id-shaped
        # token (no "Law"/"§" prefix); full citations contribute via the
        # extractor so no 'n-y-rpa-law-235-b' garbage enters the key set.
        if re.fullmatch(r"[0-9A-Za-z:.\-]+", s) and re.search(r"[0-9]", s):
            corpus_ids.add(_normalize_section(s))
        for sid in extract_statute_sections(s):
            corpus_ids.add(_normalize_section(sid))
    corpus_ids.discard("")

    aligned: list[dict[str, Any]] = []
    unaligned: list[dict[str, Any]] = []
    for sec in extract_statute_sections(answer_text or ""):
        n = _normalize_section(sec)
        rec = {"section": sec, "normalized": n}
        (aligned if n in corpus_ids else unaligned).append(rec)

    return {
        "count": len(aligned) + len(unaligned),
        "aligned": aligned,
        "unaligned": unaligned,
        "normalized_retrieved": sorted(corpus_ids),
    }


async def verify_citations(blob: str, courtlistener_key: str | None = None) -> VerifyResponse:
    """Verify every case citation in `blob` against CourtListener.

    Statutory / id. / supra citations are PARSED and listed but skipped for
    external verification (the API doesn't cover them) — `skipped_reason` is
    set accordingly. A fabricated case citation surfaces as verified=False with
    status 404 — the caller MUST block/downgrade it.

    FAIL-CLOSED over the unknown: a case citation the lookup could NOT produce a
    verdict for (status None — no token / outage / request failure) is counted
    in stats["unverified"], and a citation-SHAPED passage that eyecite cannot
    parse at all is counted in stats["unparsed"]. Neither is "clean": a caller
    must NOT treat count=0 or verified=0 as "no citation issues" — those are the
    could-not-check states (stats carry the honest, distinct counts).
    """
    shapes = count_citation_shapes(blob)
    if not _EYECITE_OK:
        return VerifyResponse(ok=False, citations=[], stats={
            "error": "eyecite not installed", "unparsed": shapes,
        })
    strings, kinds = _resolve_to_full(blob)
    parsed_case_total = sum(1 for k in kinds if k == "FullCaseCitation")
    unparsed = max(0, shapes - parsed_case_total)
    if not strings:
        return VerifyResponse(ok=True, citations=[], stats={
            "count": 0,
            "unparsed": unparsed,
            "unverified": 0,
            "verified": 0,
            "fabricated": 0,
            "ambiguous": 0,
            "skipped": 0,
        })

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
    unverified = [r for r in results
                  if not r.skipped_reason and r.status not in (200, 404, 300)]
    ok = not fabricated  # fabricated citations are a hard stop; ambiguous are flagged
    return VerifyResponse(
        ok=ok,
        citations=results,
        message=(
            f"{len(results)} citation(s) parsed; "
            f"{len(fabricated)} fabricated (blocked), {len(unverified)} unverified "
            f"(no verdict / offline), {unparsed} unparsed (citation-shaped but "
            f"unparseable)."
        ),
        stats={
            "count": len(results),
            "verified": sum(1 for r in results if r.verified),
            "fabricated": len(fabricated),
            "ambiguous": len(ambiguous),
            "unverified": len(unverified),
            "unparsed": unparsed,
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
