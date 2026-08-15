"""Books knowledge-base service — multi-genre book library.

Reads the generated `data/books/manifest.json` (built by
scripts/build_book_manifest.py from `data/books/expert-digest.md` — the
freelancer library — and `data/books/chess-digest.json` — the chess library).
Serves the curated expert digests — original synthesis, never book text — as
the knowledge layer for the console.

Both genres share one schema with genre-specific extras: freelancer books carry
`track`/`scores{ai,income,...}`/`freelancer_translation`; chess books carry
`tier`/`rating_band`/`scores{tactics,strategy,...}`/`beginner_translation`.
All endpoints accept a `genre` filter; `tier` is the chess analogue of `track`.

Fail-closed design mirrors the legal package: a missing/corrupt manifest yields
an empty library with `ok: false` and an error string, never a crash and never
fabricated data.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MANIFEST = (
    Path(__file__).resolve().parent.parent.parent / "data" / "books" / "manifest.json"
)

TRACKS: list[str] = [
    "income",
    "mindset",
    "personal finance",
    "investing",
    "real estate",
    "technical",
]
PRIORITIES: list[str] = ["READ NOW", "READ LATER", "REFERENCE"]
GENRES: list[str] = ["freelancer", "chess"]


def _detect_chess_level(question: str) -> int | None:
    """Infer a chess tier cap from the question's rating language. 'at 500',
    'beginner', 'just started' -> Tier 1; '1000' -> Tier 2; explicit higher
    ratings map up. None when no rating hint is present (full pool, but the
    sort still puts READ NOW + lowest tier first)."""
    q = (question or "").lower()
    for phrase, tier in (
        ("2000", 5),
        ("1800", 4),
        ("1600", 4),
        ("1500", 3),
        ("1400", 3),
        ("1200", 3),
        ("1000", 2),
        ("900", 2),
    ):
        if phrase in q:
            return tier
    for phrase in (
        "500",
        "beginner",
        "just started",
        "new to chess",
        "novice",
        "at 500",
        "500-rated",
    ):
        if phrase in q:
            return 1
    return None


class BooksService:
    def __init__(self, manifest_path: Path | None = None) -> None:
        self._manifest_path = manifest_path or MANIFEST
        self._cache: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("books manifest missing at %s", self._manifest_path)
            return {
                "ok": False,
                "error": "Manifest not found. Run scripts/build_book_manifest.py first.",
            }
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("books manifest unreadable: %s", exc)
            return {"ok": False, "error": "Manifest unreadable."}
        self._cache = data
        return data

    def list_books(
        self,
        genre: str | None = None,
        track: str | None = None,
        priority: str | None = None,
        tier: int | None = None,
    ) -> dict[str, Any]:
        data = self._load()
        if not data.get("ok", True):
            return data
        books = data.get("books", [])
        if genre and genre != "all":
            books = [b for b in books if b.get("genre") == genre]
        if track and track != "all":
            books = [b for b in books if b.get("track") == track]
        if priority and priority != "all":
            books = [b for b in books if b.get("priority") == priority]
        if tier is not None:
            books = [b for b in books if b.get("tier") == tier]
        tiers = sorted({b.get("tier") for b in data.get("books", []) if b.get("tier")})
        return {
            "ok": True,
            "count": len(books),
            "books": books,
            "tracks": TRACKS,
            "priorities": PRIORITIES,
            "genres": GENRES,
            "tiers": tiers,
            "generated_at": data.get("generated_at"),
        }

    def get_book(self, slug: str) -> dict[str, Any]:
        data = self._load()
        if not data.get("ok", True):
            return data
        for b in data.get("books", []):
            if b.get("slug") == slug:
                return {"ok": True, "book": b}
        return {"ok": False, "error": f"No book with slug '{slug}'."}

    def search_books(
        self, query: str, limit: int = 12, genre: str | None = None
    ) -> dict[str, Any]:
        data = self._load()
        if not data.get("ok", True):
            return data
        q = query.strip().lower()
        if not q:
            return {"ok": True, "count": 0, "results": []}
        tokens = re.findall(r"\w+", q)
        hits: list[dict[str, Any]] = []
        for b in data.get("books", []):
            if genre and genre != "all" and b.get("genre") != genre:
                continue
            haystack = " ".join(
                [
                    b.get("title", ""),
                    b.get("author", ""),
                    b.get("track_label", ""),
                    b.get("rating_band", ""),
                    " ".join(b.get("best_parts", [])),
                    b.get("freelancer_translation", ""),
                    b.get("beginner_translation", ""),
                ]
            ).lower()
            haystack_tokens = set(re.findall(r"\w+", haystack))
            score = sum(1 for t in tokens if t in haystack_tokens)
            if score:
                hits.append(
                    {
                        "slug": b["slug"],
                        "title": b["title"],
                        "author": b["author"],
                        "genre": b.get("genre"),
                        "track": b.get("track"),
                        "tier": b.get("tier"),
                        "priority": b.get("priority"),
                        "rating_band": b.get("rating_band"),
                        "ai_relevance": b.get("ai_relevance"),
                        "score": score,
                    }
                )
        hits.sort(key=lambda h: (-h["score"], h["title"]))
        return {"ok": True, "count": len(hits), "results": hits[:limit]}

    async def synthesize(
        self,
        question: str,
        genre: str | None = None,
        level: int | None = None,
        generate: bool = True,
    ) -> dict[str, Any]:
        """Cross-book reasoning: find the books whose ideas bear on the question
        and return the relevant digest fragments as a grounded knowledge layer.

        When `generate` is True, ALSO writes a synthesized answer via the
        analysis-cloud model, grounded in those fragments. Chess ranking is
        beginner-first: lowest tier surfaces first (a 'at 500' question must
        pull Tier 1, not Tier 5)."""
        data = self._load()
        if not data.get("ok", True):
            return data
        if genre in (None, "all"):
            genre = "freelancer"
        # Topic-to-track weighting: freelancer questions pull business tracks,
        # chess questions pull the skill areas. The genre determines the hints.
        if genre == "chess":
            topic_hints = {
                "tactics": (
                    "fork",
                    "pin",
                    "skewer",
                    "tactic",
                    "checkmate",
                    "mate",
                    "capture",
                    "combination",
                    "blunder",
                    "hang",
                ),
                "endgames": (
                    "endgame",
                    "king and pawn",
                    "opposition",
                    "rook",
                    "ending",
                    "convert",
                ),
                "openings": (
                    "opening",
                    "develop",
                    "castle",
                    "center",
                    "repertoire",
                    "trap",
                ),
                "strategy": (
                    "strategy",
                    "plan",
                    "imbalance",
                    "positional",
                    "advantage",
                    "piece",
                    "pawn structure",
                ),
            }
            rank_key = "tier"
            reverse = False  # lowest tier (beginner) FIRST for chess
            # Auto-level from the question text: a low-rating hint caps the pool
            # so a "at 500" question never surfaces Tier-5 books.
            if level is None:
                level = _detect_chess_level(question)
        else:
            topic_hints = {
                "technical": (
                    "llm",
                    "rag",
                    "model",
                    "data",
                    "pipeline",
                    "agent",
                    "engineer",
                    "architecture",
                    "code",
                    "production",
                    "ml",
                    "ai",
                ),
                "income": (
                    "client",
                    "freelance",
                    "upwork",
                    "pricing",
                    "sell",
                    "revenue",
                    "offer",
                    "niche",
                    "business",
                    "income",
                ),
                "mindset": (
                    "decide",
                    "focus",
                    "habit",
                    "risk",
                    "noise",
                    "discipline",
                    "mind",
                    "choice",
                ),
                "personal finance": (
                    "money",
                    "save",
                    "savings",
                    "finance",
                    "invest",
                    "runway",
                    "wealth",
                ),
                "investing": (
                    "invest",
                    "stock",
                    "market",
                    "index",
                    "portfolio",
                    "returns",
                ),
                "real estate": ("property", "rental", "real estate", "cash flow"),
            }
            rank_key = "track"
            reverse = True
        weighted: list[tuple[str, int]] = []
        for topic, words in topic_hints.items():
            weight = sum(1 for w in words if w in question.lower())
            if weight:
                weighted.append((topic, weight))
        preferred = [t for t, _ in sorted(weighted, key=lambda x: -x[1])]
        pool = [b for b in data.get("books", []) if b.get("genre") == genre]
        # For chess: keep the pool to the requested level (or below) so a
        # beginner question never surfaces Tier-5 books first.
        if genre == "chess" and level is not None:
            pool = [b for b in pool if (b.get("tier") or 99) <= level]
        if genre == "chess":
            # READ NOW first, then LOWEST tier first (beginner books first).
            books = sorted(
                pool,
                key=lambda b: (b.get("priority") != "READ NOW", b.get(rank_key) or 0),
            )
        else:
            books = sorted(
                pool,
                key=lambda b: (
                    b.get("priority") == "READ NOW",
                    b.get(rank_key) or "",
                ),
                reverse=reverse,
            )
        # Grounded fragments: the highest-signal digest content only.
        fragments = []
        for b in books[:6]:
            fragments.append(
                {
                    "slug": b["slug"],
                    "title": b["title"],
                    "author": b["author"],
                    "genre": b.get("genre"),
                    "track": b.get("track"),
                    "tier": b.get("tier"),
                    "rating_band": b.get("rating_band"),
                    "priority": b.get("priority"),
                    "best_parts": b.get("best_parts", [])[:3],
                    "freelancer_translation": b.get("freelancer_translation", ""),
                    "beginner_translation": b.get("beginner_translation", ""),
                }
            )
        out: dict[str, Any] = {
            "ok": True,
            "question": question,
            "genre": genre,
            "level": level,
            "topic_tracks": preferred,
            "fragments": fragments,
        }
        if not generate or not fragments:
            return out
        # Actually write a synthesized answer, grounded in the selected fragments.
        try:
            answer = await self._write_answer(question, genre, level, fragments)
            out["answer"] = answer
        except Exception as exc:
            log.warning("books synthesize generation failed: %s", exc)
            out["answer"] = ""
            out["generation_error"] = str(exc)[:300]
        return out

    async def _write_answer(
        self,
        question: str,
        genre: str,
        level: int | None,
        fragments: list[dict[str, Any]],
    ) -> str:
        """Generate a grounded answer via the analysis-cloud model (the console
        'Ask the library' actually answers, instead of only returning fragments)."""
        import os

        import litellm

        from ..core.settings import get_settings

        if genre == "chess":
            audience = (
                f"a ~{level * 500}-rated chess player"
                if level
                else "a beginner chess player"
            )
            instruction = (
                "Answer the chess question. Ground every point in the book fragments "
                "below (cite the book title in brackets, e.g. [Bain]). Be practical "
                "and specific — give numbered, actionable tips. Keep the answer at "
                "the reader's level; do not recommend advanced concepts they can't use yet."
            )
        else:
            audience = "an AI/Data freelancer"
            instruction = (
                "Answer the question. Ground every point in the book fragments below "
                "(cite the book title in brackets, e.g. [The E-Myth Revisited]). "
                "Be practical and specific — give numbered, actionable advice."
            )
        block = "\n\n".join(
            f"[{f['title']}] ({f.get('rating_band', '')})\n"
            + "\n".join(f.get("best_parts", []))
            for f in fragments
        )
        prompt = (
            f"You are a knowledge-base assistant for {audience}.\n{instruction}\n\n"
            f"QUESTION: {question}\n\nBOOK FRAGMENTS:\n{block}"
        )
        s = get_settings()
        model = getattr(s, "analysis_cloud_model", None) or "openai/deepseek-v4-flash"
        base = os.getenv("OPENAI_API_BASE", "https://opencode.ai/zen/go/v1")
        key = os.getenv("OPENAI_API_KEY", "")
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_base=base,
            api_key=key,
            custom_llm_provider="openai",
            max_tokens=1200,
            timeout=120,
        )
        return resp.choices[0].message.content or ""


_service: BooksService | None = None


def get_books_service() -> BooksService:
    global _service
    if _service is None:
        _service = BooksService()
    return _service
