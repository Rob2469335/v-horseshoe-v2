"""Case-law corpus ingestion for Rob's Lawyer.

Statutes (corpus_ingest.py) tell the advisor what the LAW says. This module
tells it what the COURTS have held — a curated, prioritized manifest of the
cases the operator's own appeal actually turned on, ingested as a `legal_cases`
collection (768-dim, same embed server as statutes).

Source of the manifest, NOT a guess: every entry in CASE_MANIFEST is a case
cited in United States v. Rainford, 110 F.4th 455 (2d Cir. 2024) — the appeal
that decided the operator's conviction, guidelines, and restitution — plus the
Batson authorities the transcript analysis surfaced (Batson itself was NOT
litigated in the appeal, so those are added separately and flagged as such).

Fetch seam: CourtListener v4 opinions endpoint (`?cite=<cite>`), token-gated.
Rate-limit aware: on 429 it reads Retry-After and sleeps if bounded, else
records `throttled` in the resumable state file and stops — a re-run resumes
where the daily quota cut it off (free tier ~125/day, verified live 2026-08-09).
No case is fabricated: if the API cannot produce text for a cite, the case is
recorded `not_found`/`no_text` in state, never ingested with placeholder text.
"""
from __future__ import annotations

import html
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import asyncio
import httpx
import requests

log = logging.getLogger(__name__)

QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
EMBED_URL = os.getenv("EMBED_URL", "http://127.0.0.1:8081/v1")
COLLECTION = "legal_cases"
VECTOR_SIZE = 768  # gte-modernbert (same as legal_statutes / codebase_index)
EMBED_MODEL = "gte-modernbert-base-Q8_0.gguf"

# CourtListener v4 Citation Lookup & Verification API. The opinions endpoint has
# NO cite filter (400 "Unknown filter parameters" on ?cite=) — the verifiable
# path is: POST citation-lookup with text={cite} -> cluster id -> opinions by
# cluster. This mirrors the citation_verify.py seam.
CITATION_LOOKUP_URL = os.getenv(
    "COURTLISTENER_CITATION_URL",
    "https://www.courtlistener.com/api/rest/v4/citation-lookup/",
)
OPINIONS_URL = os.getenv(
    "COURTLISTENER_OPINIONS_URL",
    "https://www.courtlistener.com/api/rest/v4/opinions/",
)

# Per-case chunk budget (chars). Opinions are long; each chunk carries its own
# `cite — case_name` header so retrieval matches on the case + the holding.
# Kept well under the embed server's 4000-char per-text budget (corpus_ingest).
_CASE_CHUNK_CHARS = 3000

# State file: cite -> {"status": done|not_found|no_text|throttled|error, "chunks": N}
STATE_FILE = Path("data/legal/cases_ingest.json")

# Rate limit: max seconds to sleep honoring Retry-After before giving up the run.
_MAX_RETRY_AFTER = 3600

# CourtListener free-tier steady-state budget is ~5 requests/min (verified live
# 2026-08-10: 5 successes then 429 "Expected available in 56 seconds"). Every
# case costs 2 requests (citation-lookup + opinions-by-cluster). Enforcing a
# >=12.5s gap between API requests keeps the run under the budget so it never
# relies on repeated Retry-After sleeps mid-manifest.
_API_MIN_GAP_S = 12.5

# Last API request timestamp (for _pace_api) — module-level so the CLI loop and
# any other caller share one gate.
_LAST_API_TS: float = 0.0


async def _pace_api() -> None:
    """Sleep so at least _API_MIN_GAP_S elapses since the last API request."""
    global _LAST_API_TS
    import time as _time
    elapsed = _time.monotonic() - _LAST_API_TS
    gap = _API_MIN_GAP_S - elapsed
    if gap > 0:
        await asyncio.sleep(gap)
    _LAST_API_TS = _time.monotonic()


# ---------------------------------------------------------------------------
# MANIFEST — every case below is verified-cited in 110 F.4th 455 (or is a
# Batson authority added for the transcript analysis). Fields:
#   cite, name, court, circuit, issues (doctrine tags), tier, batson (bool)
# ---------------------------------------------------------------------------
_CASE = dict[str, Any]

CASE_MANIFEST: list[_CASE] = [
    # -- Tier 1: controlling / directly applied to Locust (2d Cir + SCOTUS) --
    {"cite": "252 F.3d 238", "name": "United States v. Simeonov",
     "court": "2d Cir.", "circuit": "2d", "year": 2001,
     "issues": ["substitute counsel", "abuse of discretion"], "tier": 1},
    {"cite": "669 F.3d 112", "name": "United States v. Hsu",
     "court": "2d Cir.", "circuit": "2d", "year": 2012,
     "issues": ["substitute counsel", "four-factor test"], "tier": 1},
    {"cite": "564 F.3d 142", "name": "United States v. Polouizzi",
     "court": "2d Cir.", "circuit": "2d", "year": 2009,
     "issues": ["waiver", "intentional relinquishment"], "tier": 1},
    {"cite": "507 U.S. 725", "name": "United States v. Olano",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1993,
     "issues": ["waiver", "forfeiture", "plain error"], "tier": 1},
    {"cite": "808 F.3d 585", "name": "United States v. Spruill",
     "court": "2d Cir.", "circuit": "2d", "year": 2015,
     "issues": ["forfeiture", "discretion to correct"], "tier": 1},
    {"cite": "272 F.3d 116", "name": "United States v. John Doe No. 1",
     "court": "2d Cir.", "circuit": "2d", "year": 2001,
     "issues": ["substitute counsel", "right to reject counsel"], "tier": 1},
    {"cite": "190 F.3d 71", "name": "United States v. Shareef",
     "court": "2d Cir.", "circuit": "2d", "year": 1999,
     "issues": ["prosecutorial misconduct", "egregious misconduct"], "tier": 1},
    {"cite": "285 F.3d 183", "name": "United States v. Elias",
     "court": "2d Cir.", "circuit": "2d", "year": 2002,
     "issues": ["prosecutorial misconduct", "closing argument", "prejudice test"], "tier": 1},
    {"cite": "377 F.3d 232", "name": "United States v. Thomas",
     "court": "2d Cir.", "circuit": "2d", "year": 2004,
     "issues": ["prosecutorial misconduct", "substantial prejudice"], "tier": 1},
    {"cite": "777 F.3d 597", "name": "United States v. Cramer",
     "court": "2d Cir.", "circuit": "2d", "year": 2015,
     "issues": ["guidelines de novo review", "clear error"], "tier": 1},
    {"cite": "516 F.3d 122", "name": "United States v. Verkhoglyad",
     "court": "2d Cir.", "circuit": "2d", "year": 2008,
     "issues": ["plain error", "unobjected guidelines error"], "tier": 1},
    {"cite": "508 U.S. 36", "name": "Stinson v. United States",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1993,
     "issues": ["guidelines commentary", "deference"], "tier": 1},
    {"cite": "588 U.S. 558", "name": "Kisor v. Wilkie",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2019,
     "issues": ["agency interpretation", "deference", "commentary"], "tier": 1},
    {"cite": "699 F.3d 710", "name": "United States v. Lacey",
     "court": "2d Cir.", "circuit": "2d", "year": 2012,
     "issues": ["loss", "reasonable estimate"], "tier": 1},
    {"cite": "945 F.3d 687", "name": "United States v. Flores",
     "court": "2d Cir.", "circuit": "2d", "year": 2019,
     "issues": ["specific findings", "meaningful appellate review"], "tier": 1},
    {"cite": "551 F.3d 176", "name": "United States v. Uddin",
     "court": "2d Cir.", "circuit": "2d", "year": 2009,
     "issues": ["loss", "extrapolation", "reasonable estimate", "forfeiture"], "tier": 1},
    {"cite": "980 F.3d 9", "name": "United States v. Moseley",
     "court": "2d Cir.", "circuit": "2d", "year": 2020,
     "issues": ["loss methodology", "not too crude"], "tier": 1},
    {"cite": "252 F.3d 230", "name": "United States v. Carpenter",
     "court": "2d Cir.", "circuit": "2d", "year": 2001,
     "issues": ["minor role", "minimal role", "average participant"], "tier": 1},
    {"cite": "62 F.3d 43", "name": "United States v. Borst",
     "court": "2d Cir.", "circuit": "2d", "year": 1995,
     "issues": ["victim", "vulnerable victim", "controlling"], "tier": 1},
    {"cite": "174 F.3d 47", "name": "United States v. McCall",
     "court": "2d Cir.", "circuit": "2d", "year": 1998,
     "issues": ["vulnerable victim", "nexus", "singled out"], "tier": 1},
    {"cite": "446 F.3d 65", "name": "United States v. Reifler",
     "court": "2d Cir.", "circuit": "2d", "year": 2006,
     "issues": ["MVRA", "co-conspirator victims", "restitution"], "tier": 1},
    {"cite": "671 F.3d 149", "name": "United States v. Archer",
     "court": "2d Cir.", "circuit": "2d", "year": 2011,
     "issues": ["co-conspirators", "not victims", "restitution"], "tier": 1},
    {"cite": "545 F.3d 220", "name": "United States v. Ojeikere",
     "court": "2d Cir.", "circuit": "2d", "year": 2008,
     "issues": ["restitution", "abuse of discretion"], "tier": 1},
    {"cite": "235 F.3d 95", "name": "United States v. Grant",
     "court": "2d Cir.", "circuit": "2d", "year": 2000,
     "issues": ["restitution", "extremely deferential"], "tier": 1},
    {"cite": "728 F.3d 184", "name": "United States v. Gushlak",
     "court": "2d Cir.", "circuit": "2d", "year": 2013,
     "issues": ["restitution", "reasonable approximation", "sound methodology"], "tier": 1},
    {"cite": "639 F.3d 32", "name": "United States v. Treacy",
     "court": "2d Cir.", "circuit": "2d", "year": 2011,
     "issues": ["forfeiture", "government word is not evidence", "reasonable extrapolation"], "tier": 1},
    {"cite": "503 F.3d 103", "name": "United States v. Capoccia",
     "court": "2d Cir.", "circuit": "2d", "year": 2007,
     "issues": ["forfeiture", "preponderance"], "tier": 1},

    # -- Tier 2: sentencing-law backbone (2d Cir + SCOTUS) --
    {"cite": "899 F.3d 135", "name": "United States v. Alston",
     "court": "2d Cir.", "circuit": "2d", "year": 2018,
     "issues": ["false testimony", "due process"], "tier": 2},
    {"cite": "257 F.3d 210", "name": "United States v. Monteleone",
     "court": "2d Cir.", "circuit": "2d", "year": 2001,
     "issues": ["perjury", "false testimony"], "tier": 2},
    {"cite": "541 U.S. 36", "name": "Crawford v. Washington",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2004,
     "issues": ["confrontation clause", "testimonial"], "tier": 2},
    {"cite": "679 F.3d 83", "name": "United States v. Wagner-Dano",
     "court": "2d Cir.", "circuit": "2d", "year": 2012,
     "issues": ["findings", "PSR adoption"], "tier": 2},
    {"cite": "577 F.3d 442", "name": "United States v. Ware",
     "court": "2d Cir.", "circuit": "2d", "year": 2009,
     "issues": ["findings", "PSR adoption insufficient"], "tier": 2},
    {"cite": "963 F.3d 285", "name": "United States v. Mattis",
     "court": "2d Cir.", "circuit": "2d", "year": 2020,
     "issues": ["clear error", "entire evidence"], "tier": 2},
    {"cite": "776 F.3d 67", "name": "United States v. Norman",
     "court": "2d Cir.", "circuit": "2d", "year": 2015,
     "issues": ["clear error", "two permissible views"], "tier": 2},
    {"cite": "317 F.3d 107", "name": "United States v. Thorn",
     "court": "2d Cir.", "circuit": "2d", "year": 2003,
     "issues": ["sentencing facts", "preponderance"], "tier": 2},
    {"cite": "616 F.3d 174", "name": "United States v. Dorvee",
     "court": "2d Cir.", "circuit": "2d", "year": 2010,
     "issues": ["preservation", "distinctness of objection"], "tier": 2},
    {"cite": "592 F.3d 372", "name": "United States v. Rossi",
     "court": "2d Cir.", "circuit": "2d", "year": 2010,
     "issues": ["restitution", "VWPA", "guesswork"], "tier": 2},
    {"cite": "828 F.3d 91", "name": "United States v. Rivernider",
     "court": "2d Cir.", "circuit": "2d", "year": 2016,
     "issues": ["restitution", "not mathematically precise", "preponderance"], "tier": 2},
    {"cite": "481 F.3d 132", "name": "United States v. Milstein",
     "court": "2d Cir.", "circuit": "2d", "year": 2007,
     "issues": ["restitution", "reasonable estimate"], "tier": 2},
    {"cite": "677 F.3d 86", "name": "United States v. Zangari",
     "court": "2d Cir.", "circuit": "2d", "year": 2012,
     "issues": ["MVRA", "actual loss", "gain vs loss"], "tier": 2},
    {"cite": "654 F.3d 310", "name": "United States v. Marino",
     "court": "2d Cir.", "circuit": "2d", "year": 2011,
     "issues": ["MVRA", "causation", "actual loss"], "tier": 2},
    {"cite": "353 F.3d 130", "name": "United States v. Walker",
     "court": "2d Cir.", "circuit": "2d", "year": 2003,
     "issues": ["MVRA", "victim identification", "charged scheme"], "tier": 2},
    {"cite": "326 F.3d 323", "name": "United States v. Catoggio",
     "court": "2d Cir.", "circuit": "2d", "year": 2003,
     "issues": ["MVRA", "identify victims", "actual losses"], "tier": 2},
    {"cite": "634 F.3d 668", "name": "United States v. Paul",
     "court": "2d Cir.", "circuit": "2d", "year": 2011,
     "issues": ["restitution", "loss result of fraud"], "tier": 2},
    {"cite": "347 F.3d 45", "name": "United States v. Lucien",
     "court": "2d Cir.", "circuit": "2d", "year": 2003,
     "issues": ["risk of death or serious injury", "obvious risk"], "tier": 2},
    {"cite": "532 U.S. 234", "name": "Easley v. Cromartie",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2001,
     "issues": ["clear error", "definite and firm conviction"], "tier": 2},
    {"cite": "333 U.S. 364", "name": "United States v. U.S. Gypsum Co.",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1948,
     "issues": ["clear error", "definite and firm conviction"], "tier": 2},
    {"cite": "67 F.4th 520", "name": "United States v. Montague",
     "court": "2d Cir.", "circuit": "2d", "year": 2023,
     "issues": ["plain error", "four prongs"], "tier": 2},
    {"cite": "690 F.3d 70", "name": "United States v. Williams",
     "court": "2d Cir.", "circuit": "2d", "year": 2012,
     "issues": ["prosecutorial misconduct", "flagrant abuse", "vouching"], "tier": 2},

    # -- Tier 3: general backbone + non-2d circuit context --
    {"cite": "74 F.4th 378", "name": "United States v. You",
     "court": "6th Cir.", "circuit": "6th", "year": 2023,
     "issues": ["loss", "intended loss", "commentary deference"], "tier": 3},
    {"cite": "55 F.4th 246", "name": "United States v. Banks",
     "court": "3d Cir.", "circuit": "3d", "year": 2022,
     "issues": ["loss", "ordinary meaning", "no deference"], "tier": 3},
    {"cite": "23 F.4th 347", "name": "United States v. Moses",
     "court": "4th Cir.", "circuit": "4th", "year": 2022,
     "issues": ["guidelines commentary", "reticulated whole"], "tier": 3},
    {"cite": "521 U.S. 203", "name": "Agostini v. Felton",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1997,
     "issues": ["stare decisis", "directly controlling precedent"], "tier": 3},
    {"cite": "490 U.S. 477", "name": "Rodriguez de Quijas v. Shearson/Am. Exp., Inc.",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1989,
     "issues": ["stare decisis", "overruling"], "tier": 3},

    # -- Batson authorities (NOT litigated in the appeal; added for the
    #    transcript analysis surface) --
    {"cite": "476 U.S. 79", "name": "Batson v. Kentucky",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1986,
     "issues": ["batson", "peremptory", "equal protection"], "tier": 4, "batson": True},
    {"cite": "588 U.S. 284", "name": "Flowers v. Mississippi",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2019,
     "issues": ["batson", "peremptory", "pattern of discrimination"], "tier": 4, "batson": True},
    {"cite": "500 U.S. 352", "name": "Hernandez v. New York",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1991,
     "issues": ["batson", "pretext", "neutral explanation"], "tier": 4, "batson": True},
    {"cite": "514 U.S. 765", "name": "Purkett v. Elem",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1995,
     "issues": ["batson", "three-step framework"], "tier": 4, "batson": True},
    {"cite": "499 U.S. 400", "name": "Powers v. Ohio",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 1991,
     "issues": ["batson", "third-party standing"], "tier": 4, "batson": True},
    {"cite": "545 U.S. 162", "name": "Johnson v. California",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2005,
     "issues": ["batson", "prima facie", "inference standard"], "tier": 4, "batson": True},
    {"cite": "545 U.S. 231", "name": "Miller-El v. Dretke",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2005,
     "issues": ["batson", "comparative analysis", "procedures"], "tier": 4, "batson": True},
    {"cite": "552 U.S. 472", "name": "Snyder v. Louisiana",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2008,
     "issues": ["batson", "pretext", "demeanor"], "tier": 4, "batson": True},
    {"cite": "578 U.S. 488", "name": "Foster v. Chatman",
     "court": "U.S. Supreme Court", "circuit": "scotus", "year": 2016,
     "issues": ["batson", "pretext", "systematic exclusion"], "tier": 4, "batson": True},
    {"cite": "528 F.3d 110", "name": "United States v. Thompson",
     "court": "2d Cir.", "circuit": "2d", "year": 2008,
     "issues": ["batson", "peremptory", "second circuit"], "tier": 4, "batson": True},
    {"cite": "82 F.3d 1243", "name": "McCrory v. Henderson",
     "court": "2d Cir.", "circuit": "2d", "year": 1996,
     "issues": ["batson", "peremptory", "second circuit"], "tier": 4, "batson": True},
    {"cite": "352 F.3d 654", "name": "United States v. Brown",
     "court": "2d Cir.", "circuit": "2d", "year": 2003,
     "issues": ["batson", "peremptory", "second circuit"], "tier": 4, "batson": True},
]


def manifest_by_cite() -> dict[str, _CASE]:
    return {c["cite"]: c for c in CASE_MANIFEST}


def _fit_budget(text: str) -> str:
    """Word-chopping budget — never send the embed model a single text that
    could overflow its context (same guard as corpus_ingest)."""
    if not text or len(text) <= _CASE_CHUNK_CHARS:
        return text
    out: list[str] = []
    n = 0
    for w in text.split():
        if n + len(w) + 1 > _CASE_CHUNK_CHARS:
            break
        out.append(w)
        n += len(w) + 1
    return " ".join(out)


# ---------------------------------------------------------------------------
# CHUNKING
# ---------------------------------------------------------------------------
def chunk_case(text: str, chunk_chars: int = _CASE_CHUNK_CHARS) -> list[str]:
    """Split a full opinion into retrieval-sized chunks on paragraph boundaries.

    A chunk is a run of paragraphs totaling up to `chunk_chars`. The embed text
    later prepends the `cite — case_name` header to every chunk (done by the
    caller via _chunk_embed_text), so a retrieval hit carries its case identity.

    Never drops content and never exceeds `chunk_chars`:
      - paragraphs are accumulated up to the budget;
      - a single paragraph LONGER than the budget is word-chopped into
        budget-sized pieces FIRST (real opinions can arrive as one huge line —
        e.g. an HTML-stripped feed — and the old code emitted one mega-chunk
        that _fit_budget then truncated to the first 3000 chars, silently
        losing the rest of the case).

    Empty opinions produce []. Robust against very long single paragraphs."""
    if not text or not text.strip():
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def _flush() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0

    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # Word-choop an oversized paragraph into budget-sized pieces so the
        # FULL opinion is retained (multiple chunks), not the opening only.
        if len(p) > chunk_chars:
            _flush()
            pieces: list[str] = []
            piece: list[str] = []
            n = 0
            for w in p.split():
                if n + len(w) + 1 > chunk_chars:
                    if piece:
                        pieces.append(" ".join(piece))
                        piece, n = [], 0
                piece.append(w)
                n += len(w) + 1
            if piece:
                pieces.append(" ".join(piece))
            chunks.extend(pieces)
            continue
        if cur and cur_len + len(p) + 2 > chunk_chars:
            _flush()
        cur.append(p)
        cur_len += len(p) + 2
    _flush()
    return chunks


def _chunk_embed_text(entry: _CASE, chunk: str) -> str:
    """The text actually embedded for one chunk: case identity header + chunk."""
    return f"{entry['cite']} — {entry['name']}\n{chunk}"


# ---------------------------------------------------------------------------
# COLLECTION
# ---------------------------------------------------------------------------
def ensure_cases_collection() -> None:
    """Create the legal_cases collection if it doesn't exist."""
    existing = requests.get(f"{QDRANT_URL}/collections", timeout=10.0).json()
    names = {c["name"] for c in existing.get("result", {}).get("collections", [])}
    if COLLECTION in names:
        return
    resp = requests.put(
        f"{QDRANT_URL}/collections/{COLLECTION}",
        json={"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}},
        timeout=30.0,
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# COURTLISTENER FETCH (rate-limit aware, resumable)
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def _html_to_text(fragment: str) -> str:
    """Best-effort HTML -> text for the html_with_citations fallback (no new
    deps — plain tag stripping + entity unescape)."""
    t = _TAG_RE.sub("", fragment or "")
    t = html.unescape(t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


def _headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    key = os.getenv("COURTLISTENER_API_TOKEN")
    if key:
        h["Authorization"] = f"Token {key}"
    return h


async def _get_opinion_text(client: httpx.AsyncClient, cite: str) -> dict[str, Any]:
    """GET one opinion by cite from CourtListener. Returns
    {"text": str, "case_name": str} on success, or {"error": <code>}:
      "not_found"  — lookup/cluster produced no result for this cite
      "no_text"    — result exists but neither plain_text nor html_with_citations
      "throttled"  — 429 with Retry-After beyond _MAX_RETRY_AFTER (stop the run)
      "http:<code>"— other failure (caller records error and continues)
    Never raises.

    The opinions endpoint has NO cite filter (verified live 2026-08-10: ?cite=
    returns 400 "Unknown filter parameters" for every case). The verifiable
    path is the two-step seam shared with citation_verify.py:
      1. POST citation-lookup with data={"text": cite} -> first result's
         cluster id (200/300 = found; 404 = fabricated; 400 = bad reporter).
      2. GET opinions?cluster=<id> -> plain_text (html_with_citations fallback).
    Both legs can 429; the API's budget is ~5 req/min (verified live), so on a
    bounded Retry-After we sleep and re-issue, and _pace_api keeps the steady
    state under the budget."""
    await _pace_api()
    resp = await client.post(
        CITATION_LOOKUP_URL,
        data={"text": cite},
        timeout=30.0,
    )
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After") or 0)
        if 0 < retry_after <= _MAX_RETRY_AFTER:
            log.warning("429 lookup for %s — Retry-After %ss", cite, retry_after)
            await asyncio.sleep(retry_after)
            resp = await client.post(
                CITATION_LOOKUP_URL,
                data={"text": cite},
                timeout=30.0,
            )
        else:
            log.warning("429 lookup for %s — Retry-After %ss too long, stopping run", cite, retry_after)
            return {"error": "throttled", "retry_after": retry_after}
    if resp.status_code != 200:
        return {"error": f"http:{resp.status_code}"}
    try:
        body = resp.json()
    except Exception:
        return {"error": "bad-json"}
    if not isinstance(body, list) or not body:
        return {"error": "not_found"}
    item = body[0]
    # 200/300 = found; 404 = fabricated; 400 = bad reporter.
    if item.get("status") in (404, 400):
        return {"error": "not_found"}
    clusters = (item or {}).get("clusters") or []
    if not clusters:
        return {"error": "not_found"}
    cluster_id = clusters[0].get("id")
    case_name = (clusters[0].get("case_name") or "").strip()
    if not cluster_id:
        return {"error": "not_found"}

    await _pace_api()
    opin = await client.get(
        OPINIONS_URL,
        params={"cluster": cluster_id, "format": "json"},
        timeout=30.0,
    )
    if opin.status_code == 429:
        retry_after = int(opin.headers.get("Retry-After") or 0)
        if 0 < retry_after <= _MAX_RETRY_AFTER:
            log.warning("429 opinion for %s — Retry-After %ss", cite, retry_after)
            await asyncio.sleep(retry_after)
            opin = await client.get(
                OPINIONS_URL,
                params={"cluster": cluster_id, "format": "json"},
                timeout=30.0,
            )
        else:
            log.warning("429 opinion for %s — Retry-After %ss too long, stopping run", cite, retry_after)
            return {"error": "throttled", "retry_after": retry_after}
    if opin.status_code != 200:
        return {"error": f"http:{opin.status_code}"}
    try:
        oresults = (opin.json() or {}).get("results") or []
    except Exception:
        return {"error": "bad-json"}
    if not oresults:
        return {"error": "not_found"}
    item = oresults[0]
    text = (item.get("plain_text") or "").strip()
    if not text:
        text = _html_to_text(item.get("html_with_citations") or "")
    if not text:
        return {"error": "no_text"}
    # The opinions endpoint doesn't carry case_name directly; prefer the
    # manifest's name, but report the lookup cluster's name when present.
    # CITATION-GRAPH SEAM (rec 9, CourtListener verified): the opinion object
    # carries `opinions_cited` — an authorities table of opinions THIS case
    # cites (backward edges). Forward-citing cases come from the dedicated
    # /opinions-cited/ edge API (cited_opinion=<id>, field `depth`). Both feed
    # the offline cite-follow graph; capture the backward edges here.
    opinions_cited = []
    for oc in (item.get("opinions_cited") or []):
        if isinstance(oc, dict) and oc.get("id"):
            opinions_cited.append({"id": oc["id"]})
    return {"text": text, "case_name": case_name, "opinions_cited": opinions_cited}


# ---------------------------------------------------------------------------
# EMBED + UPSERT (token-budget batched, mirrors corpus_ingest)
# ---------------------------------------------------------------------------
_embed_client: httpx.AsyncClient | None = None


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None or _embed_client.is_closed:
        _embed_client = httpx.AsyncClient(base_url=EMBED_URL, timeout=60.0)
    return _embed_client


async def _embed(texts: list[str]) -> list[list[float]] | None:
    """Embed a batch via :8081. Retries once on transient 5xx (the embed server
    returns 500 while warming up) — same pattern as corpus_ingest._embed."""
    last_exc: Exception | None = None
    for attempt in (0, 1):
        try:
            fit = [_fit_budget(t) for t in texts]
            resp = await _get_embed_client().post(
                "/embeddings",
                json={"model": EMBED_MODEL, "input": fit},
                headers={"Authorization": "Bearer llama"},
            )
            if resp.status_code >= 500:
                last_exc = RuntimeError(f"embed server {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(1.0 + attempt)
                continue
            resp.raise_for_status()
            data = resp.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
        except Exception as exc:
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None \
                    and exc.response.status_code < 500:
                break
            await asyncio.sleep(1.0 + attempt)
    log.warning("embed failed for batch of %d after retries: %s", len(texts), last_exc)
    return None


async def ingest_one_case(entry: _CASE, text: str, batch_size: int = 16,
                          opinions_cited: list[dict] | None = None) -> int:
    """Chunk + embed + upsert one case's full opinion into legal_cases.
    Idempotent: deletes the cite's existing points first (scoped to this cite,
    so re-runs never duplicate and never touch other cases). Returns chunk count.
    `opinions_cited` (CourtListener authorities table) is stored in every chunk
    payload so the citation graph can be built offline without a re-fetch."""
    ensure_cases_collection()
    cite = entry["cite"]
    # Remove existing points for this cite (idempotent re-run, additive to others).
    try:
        requests.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/delete",
            json={"filter": {"must": [{"key": "cite", "match": {"value": cite}}]}},
            timeout=60.0,
        )
    except Exception as exc:
        log.warning("delete existing %s points failed: %s", cite, exc)

    chunks = chunk_case(text)
    if not chunks:
        return 0
    header = {
        "cite": cite,
        "case_name": entry["name"],
        "court": entry.get("court", ""),
        "circuit": entry.get("circuit", ""),
        "year": int(entry.get("year") or 0),
        "issues": list(entry.get("issues") or []),
        "tier": int(entry.get("tier") or 0),
        "batson": bool(entry.get("batson")),
        "jurisdiction": "case",
        "source": "courtlistener",
        # Backward citation edges (CourtListener authorities table) — enables
        # the offline cite-follow graph without a re-fetch.
        "opinions_cited": [int(o["id"]) for o in (opinions_cited or []) if o.get("id")],
    }
    total = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        embed_texts = [_chunk_embed_text(entry, c) for c in batch]
        vectors = await _embed(embed_texts)
        if not vectors or len(vectors) != len(batch):
            # Fail-closed: a partial embed must NOT be silently marked `done`.
            # Raise so the caller records `error` and a later run retries the
            # whole case — a `continue` here shipped a truncated corpus under a
            # `done` status (the case was never re-fetched).
            raise RuntimeError(
                f"embed failed for {cite} batch at {start} "
                f"(got {len(vectors) if vectors else 0}/{len(batch)} vectors)"
            )
        points = []
        for i, (chunk, vec) in enumerate(zip(batch, vectors)):
            payload = dict(header)
            payload["content"] = chunk
            payload["chunk_index"] = start + i
            payload["chunk_count"] = len(chunks)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"case:{cite}:{start + i}"))
            points.append({"id": point_id, "vector": vec, "payload": payload})
        resp = requests.put(
            f"{QDRANT_URL}/collections/{COLLECTION}/points",
            json={"points": points},
            timeout=120.0,
        )
        resp.raise_for_status()
        total += len(points)
    return total


# ---------------------------------------------------------------------------
# RESUMABLE STATE + CLI
# ---------------------------------------------------------------------------
def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            import json
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    import json
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


async def _ingest_manifest_cli() -> dict[str, Any]:
    """Resumable manifest ingest. Skips cites already `done`. On a `throttled`
    stop, saves state and returns so a later run resumes. Returns the summary."""
    ensure_cases_collection()
    state = _load_state()
    summary: dict[str, Any] = {"done": 0, "not_found": 0, "no_text": 0,
                               "error": 0, "throttled": False, "by_tier": {}}
    client = httpx.AsyncClient(headers=_headers(), timeout=30.0)
    try:
        for entry in CASE_MANIFEST:
            cite = entry["cite"]
            prior = state.get(cite, {})
            if prior.get("status") == "done":
                summary["done"] += 1
                summary.setdefault("by_tier", {}).setdefault(entry["tier"], 0)
                summary["by_tier"][entry["tier"]] = summary["by_tier"].get(entry["tier"], 0) + 1
                continue
            got = await _get_opinion_text(client, cite)
            if got.get("error") == "throttled":
                state.setdefault(cite, {})["status"] = "throttled"
                _save_state(state)
                summary["throttled"] = True
                log.info("throttled at %s — state saved; re-run to resume", cite)
                break
            if got.get("error"):
                state[cite] = {"status": got["error"]}
                summary[got["error"] if got["error"] in summary else "error"] += 1
                _save_state(state)
                continue
            try:
                n = await ingest_one_case(entry, got["text"], opinions_cited=got.get("opinions_cited"))
            except Exception as exc:
                log.warning("ingest failed for %s: %s", cite, exc)
                state[cite] = {"status": "error", "detail": str(exc)[:200]}
                summary["error"] += 1
                _save_state(state)
                continue
            state[cite] = {"status": "done", "chunks": n}
            summary["done"] += 1
            summary.setdefault("by_tier", {}).setdefault(entry["tier"], 0)
            summary["by_tier"][entry["tier"]] = summary["by_tier"].get(entry["tier"], 0) + 1
            _save_state(state)
            log.info("ingested %s (%s) — %d chunks", cite, entry["name"], n)
            await asyncio.sleep(0.5)  # gentle pacing; Retry-After governs the rest
    finally:
        await client.aclose()
    return summary


def run_ingest_cli() -> None:
    """CLI entrypoint for the DETACHED background case-law ingestion."""
    # Load .env (CourtListener token etc.) the same quote-stripping way the
    # rest of the stack does — running the module directly skips settings.
    from swarm_os.config.settings import _load_dotenv
    _load_dotenv()
    import logging
    log_dir = Path("./data/legal")
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_dir / "cases_ingest.log", encoding="utf-8")],
    )
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(_ingest_manifest_cli())
    line = f"CASES INGEST COMPLETE: {summary}"
    log.info(line)
    (log_dir / "cases_ingest.done").write_text(line + "\n", encoding="utf-8")


if __name__ == "__main__":
    run_ingest_cli()
