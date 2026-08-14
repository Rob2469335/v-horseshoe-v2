"""Books knowledge-base service — the AI/Data Freelancer book library.

Reads the generated `data/books/manifest.json` (built from the canonical
`data/books/expert-digest.md` by scripts/build_book_manifest.py). Serves the
curated expert digests — original synthesis, never book text — as the knowledge
layer for the console.

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

MANIFEST = Path(__file__).resolve().parent.parent.parent / "data" / "books" / "manifest.json"

TRACKS: list[str] = ["income", "mindset", "personal finance", "investing", "real estate", "technical"]
PRIORITIES: list[str] = ["READ NOW", "READ LATER", "REFERENCE"]


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
            return {"ok": False, "error": "Manifest not found. Run scripts/build_book_manifest.py first."}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("books manifest unreadable: %s", exc)
            return {"ok": False, "error": "Manifest unreadable."}
        self._cache = data
        return data

    def list_books(self, track: str | None = None, priority: str | None = None) -> dict[str, Any]:
        data = self._load()
        if not data.get("ok", True):
            return data
        books = data.get("books", [])
        if track and track != "all":
            books = [b for b in books if b.get("track") == track]
        if priority and priority != "all":
            books = [b for b in books if b.get("priority") == priority]
        return {"ok": True, "count": len(books), "books": books, "tracks": TRACKS,
                "priorities": PRIORITIES, "generated_at": data.get("generated_at")}

    def get_book(self, slug: str) -> dict[str, Any]:
        data = self._load()
        if not data.get("ok", True):
            return data
        for b in data.get("books", []):
            if b.get("slug") == slug:
                return {"ok": True, "book": b}
        return {"ok": False, "error": f"No book with slug '{slug}'."}

    def search_books(self, query: str, limit: int = 12) -> dict[str, Any]:
        data = self._load()
        if not data.get("ok", True):
            return data
        q = query.strip().lower()
        if not q:
            return {"ok": True, "count": 0, "results": []}
        tokens = re.findall(r"\w+", q)
        hits: list[dict[str, Any]] = []
        for b in data.get("books", []):
            haystack = " ".join([
                b.get("title", ""),
                b.get("author", ""),
                b.get("track_label", ""),
                " ".join(b.get("best_parts", [])),
                b.get("freelancer_translation", ""),
            ]).lower()
            haystack_tokens = set(re.findall(r"\w+", haystack))
            score = sum(1 for t in tokens if t in haystack_tokens)
            if score:
                hits.append({"slug": b["slug"], "title": b["title"], "author": b["author"],
                             "track": b["track"], "priority": b["priority"],
                             "ai_relevance": b.get("ai_relevance"), "score": score})
        hits.sort(key=lambda h: (-h["score"], h["title"]))
        return {"ok": True, "count": len(hits), "results": hits[:limit]}

    def synthesize(self, question: str) -> dict[str, Any]:
        """Cross-book reasoning: find the books whose ideas bear on the question,
        and return the relevant digest fragments as a grounded knowledge layer.

        The console renders these fragments + the matched book list; the actual
        prose synthesis is produced by the calling LLM agent (this service is
        deterministic retrieval only — no generation here)."""
        data = self._load()
        if not data.get("ok", True):
            return data
        # Topic-to-track weighting: career/income questions pull business tracks,
        # engineering questions pull the technical track, etc.
        topic_hints = {
            "technical": ("llm", "rag", "model", "data", "pipeline", "agent", "engineer", "architecture", "code", "production", "ml", "ai"),
            "income": ("client", "freelance", "upwork", "pricing", "sell", "revenue", "offer", "niche", "business", "income"),
            "mindset": ("decide", "focus", "habit", "risk", "noise", "discipline", "mind", "choice"),
            "personal finance": ("money", "save", "savings", "finance", "invest", "runway", "wealth"),
            "investing": ("invest", "stock", "market", "index", "portfolio", "returns"),
            "real estate": ("property", "rental", "real estate", "cash flow"),
        }
        weighted: list[tuple[str, int]] = []
        for track, words in topic_hints.items():
            weight = sum(1 for w in words if w in question.lower())
            if weight:
                weighted.append((track, weight))
        preferred = [t for t, _ in sorted(weighted, key=lambda x: -x[1])]
        books = sorted(
            data.get("books", []),
            key=lambda b: (b.get("track") in preferred, b.get("priority") == "READ NOW"),
            reverse=True,
        )
        # Grounded fragments: the highest-signal digest content only.
        fragments = []
        for b in books[:6]:
            fragments.append({
                "slug": b["slug"],
                "title": b["title"],
                "author": b["author"],
                "track": b["track"],
                "priority": b["priority"],
                "best_parts": b.get("best_parts", [])[:3],
                "freelancer_translation": b.get("freelancer_translation", ""),
            })
        return {
            "ok": True,
            "question": question,
            "topic_tracks": preferred,
            "fragments": fragments,
        }


_service: BooksService | None = None


def get_books_service() -> BooksService:
    global _service
    if _service is None:
        _service = BooksService()
    return _service
