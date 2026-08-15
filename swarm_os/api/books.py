"""Books knowledge-base routes for the multi-genre book library.

Endpoints:
- GET  /books — list the library (filter by genre / track / priority / tier),
  plus available tracks + priorities + genres for the UI.
- GET  /books/{slug} — full expert digest for one book.
- GET  /books/search?q= — keyword search over title/author/best-parts (genre-scoped).
- POST /books/synthesize — topic-aware cross-book retrieval (the knowledge
  layer for an LLM agent to reason over).

Content is original expert synthesis from data/books/expert-digest.md and
data/books/chess-digest.json (built to data/books/manifest.json by
scripts/build_book_manifest.py) — never the books' own text.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from swarm_os.services.books_service import get_books_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/books", tags=["books"])


def _svc():
    return get_books_service()


@router.get("")
async def books_list(
    genre: str | None = Query(None, description="Filter by genre, or 'all'"),
    track: str | None = Query(
        None, description="Filter by track, or 'all' (freelancer)"
    ),
    priority: str | None = Query(None, description="Filter by priority, or 'all'"),
    tier: int | None = Query(None, description="Filter by chess tier"),
) -> dict[str, Any]:
    res = _svc().list_books(genre=genre, track=track, priority=priority, tier=tier)
    if not res.get("ok"):
        raise HTTPException(
            status_code=503, detail=res.get("error", "Books manifest unavailable")
        )
    return res


@router.get("/search")
async def book_search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(12, ge=1, le=50),
    genre: str | None = Query(
        None, description="Scope the search to a genre, or 'all'"
    ),
) -> dict[str, Any]:
    res = _svc().search_books(q, limit=limit, genre=genre)
    if not res.get("ok"):
        raise HTTPException(
            status_code=503, detail=res.get("error", "Books manifest unavailable")
        )
    return res


@router.get("/{slug}")
async def book_detail(slug: str) -> dict[str, Any]:
    res = _svc().get_book(slug)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "Book not found"))
    return res


class SynthesizeRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Cross-book question, e.g. 'productize my AI automation service'",
    )
    genre: str | None = Field(
        None,
        description="Scope to a genre ('freelancer' | 'chess'); defaults to freelancer",
    )
    level: int | None = Field(
        None,
        description="Chess rating band hint (1-5 tiers, ascending). 'at 500' questions "
        "should pull Tier 1, not Tier 5. Defaults to 1 for chess.",
    )
    generate: bool = Field(
        True,
        description="Also synthesize a written answer via the model (True) or return "
        "only the grounded fragments (False).",
    )


@router.post("/synthesize")
async def book_synthesize(req: SynthesizeRequest) -> dict[str, Any]:
    """Topic-aware cross-book reasoning: pulls the highest-signal digest
    fragments (beginner-friendly for chess — lowest tier first, so 'at 500'
    surfaces Tier 1, not Tier 5), then when `generate` is set, actually writes a
    grounded answer via the analysis-cloud model."""
    res = await _svc().synthesize(
        req.question,
        genre=req.genre,
        level=req.level,
        generate=req.generate,
    )
    if not res.get("ok"):
        raise HTTPException(
            status_code=503, detail=res.get("error", "Books manifest unavailable")
        )
    return res
