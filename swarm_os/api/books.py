"""Books knowledge-base routes for the AI/Data Freelancer library.

Endpoints:
- GET  /books — list the library (filter by track / priority), plus available
  tracks + priorities for the UI.
- GET  /books/{slug} — full expert digest for one book.
- GET  /books/search?q= — keyword search over title/author/best-parts.
- POST /books/synthesize — topic-aware cross-book retrieval (the knowledge
  layer for an LLM agent to reason over).

Content is original expert synthesis from data/books/expert-digest.md (built to
data/books/manifest.json by scripts/build_book_manifest.py) — never the books'
own text.
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
    track: str | None = Query(None, description="Filter by track, or 'all'"),
    priority: str | None = Query(None, description="Filter by priority, or 'all'"),
) -> dict[str, Any]:
    res = _svc().list_books(track=track, priority=priority)
    if not res.get("ok"):
        raise HTTPException(status_code=503, detail=res.get("error", "Books manifest unavailable"))
    return res


@router.get("/search")
async def book_search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(12, ge=1, le=50),
) -> dict[str, Any]:
    res = _svc().search_books(q, limit=limit)
    if not res.get("ok"):
        raise HTTPException(status_code=503, detail=res.get("error", "Books manifest unavailable"))
    return res


@router.get("/{slug}")
async def book_detail(slug: str) -> dict[str, Any]:
    res = _svc().get_book(slug)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "Book not found"))
    return res


class SynthesizeRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000,
                          description="Cross-book question, e.g. 'productize my AI automation service'")


@router.post("/synthesize")
async def book_synthesize(req: SynthesizeRequest) -> dict[str, Any]:
    """Topic-aware cross-book retrieval: returns the highest-signal digest
    fragments for an LLM agent to reason over (deterministic retrieval only —
    no generation happens here)."""
    res = _svc().synthesize(req.question)
    if not res.get("ok"):
        raise HTTPException(status_code=503, detail=res.get("error", "Books manifest unavailable"))
    return res
