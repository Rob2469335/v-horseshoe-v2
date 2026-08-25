"""Qdrant-backed chess book index — the trainer's "book memory".

The user's design: embed the 100 chess-book digests into Qdrant once, then
retrieve only the fragments relevant to a specific blunder concept (hanging
piece, fork, development, endgame) instead of stuffing the whole library into
an LLM prompt. This is the RAG layer that makes explanations both fast and
grounded.

Each point = one book's best_parts fragment (or its beginner_translation),
payload carries {slug, title, author, tier, rating_band, priority, kind,
concept_hint}. Retrieval embeds the query via :8081 (gte-modernbert) and hits
the `chess_books` collection (768-dim, same as every other collection here).

Fail-closed: an unreachable embedder/Qdrant degrades to a deterministic
keyword fallback over the in-memory digest — never a crash, never fabricated
citations.
"""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

COLLECTION = "chess_books"
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
EMBED_URL = "http://127.0.0.1:8081/v1"
EMBED_DIM = 768

# Serializes the check-and-create collection init so concurrent index_books
# calls can't both see "missing" and both create it (TOCTOU race).
_init_lock = asyncio.Lock()

# Concept keywords -> deterministic fallback scoring (used when the embedder
# or Qdrant is down). Maps the blunder type to the books that teach it.
_CONCEPT_KEYWORDS: dict[str, list[str]] = {
    "hanging": [
        "hanging",
        "undefended",
        "capture",
        "en prise",
        "protect",
        "defend",
        "material",
    ],
    "fork": ["fork", "double attack", "knight", "attack two"],
    "pin": ["pin", "absolute pin", "relative pin"],
    "skewer": ["skewer", "x-ray", "xray"],
    "development": ["develop", "development", "center", "castle", "piece out"],
    "endgame": ["endgame", "king", "pawn ending", "rook ending", "opposition", "mate"],
    "checkmate": ["checkmate", "mate", "back rank", "smothered", "attack"],
    "tactic": ["tactic", "combination", "blunder", "sacrifice", "deflection"],
    "opening": ["opening", "trap", "repertoire", "gambit"],
}


def _books() -> list[dict]:
    try:
        from .books_service import get_books_service

        data = get_books_service()._load()
        if data.get("ok", True):
            return [b for b in data.get("books", []) if b.get("genre") == "chess"]
    except Exception as exc:
        log.warning("books load failed: %s", exc)
    return []


def _concept_from(classification: str, query: str = "") -> str:
    q = f"{classification} {query}".lower()
    best, best_score = "tactic", 0
    for concept, words in _CONCEPT_KEYWORDS.items():
        score = sum(1 for w in words if w in q)
        if score > best_score:
            best, best_score = concept, score
    return best


import httpx

_embed_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=10.0),
    base_url=EMBED_URL,
    headers={"Authorization": "Bearer llama"},
)


async def _embed(text: str) -> list[float]:
    try:
        resp = await _embed_client.post("/embeddings", json={"input": text})
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as exc:
        log.warning("chess-book embed failed: %s", exc)
        return [0.0] * EMBED_DIM


async def index_books(force: bool = False) -> dict:
    """Upsert all chess-book digest fragments into the `chess_books` collection.
    Idempotent: skips a book already present (unless force). Returns counts."""
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    books = _books()
    if not books:
        return {"ok": False, "error": "no chess books found in manifest"}
    client = AsyncQdrantClient(url=QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY"))
    try:
        async with _init_lock:
            # Check-and-create under the lock: two concurrent index_books calls
            # must not both see the collection missing and both create it.
            async with asyncio.timeout(10.0):
                collections = await client.get_collections()
            names = {c.name for c in collections.collections}
            if COLLECTION not in names:
                await client.create_collection(
                    collection_name=COLLECTION,
                    vectors_config=VectorParams(
                        size=EMBED_DIM, distance=Distance.COSINE
                    ),
                )

        if not force:
            async with asyncio.timeout(10.0):
                res = await client.count(collection_name=COLLECTION, exact=True)
            existing_count = getattr(res, "count", 0)
            if existing_count > 0:
                return {
                    "ok": True,
                    "indexed": 0,
                    "existing": existing_count,
                    "total": len(books),
                }

        points = []
        for b in books:
            fragments = b.get("best_parts", [])
            fragments = fragments[:2] + (
                [b.get("beginner_translation", "")]
                if b.get("beginner_translation")
                else []
            )
            for i, frag in enumerate(fragments[:3]):
                if not frag or len(frag) < 20:
                    continue
                text = f"{b['title']}: {frag}"
                vec = await _embed(text)
                if not any(vec):
                    continue
                points.append(
                    PointStruct(
                        # Stable ID across restarts — Python's built-in hash() is
                        # salted per-process, so it would mint a new ID on every
                        # re-index and duplicate points. Use sha256 instead.
                        id=int.from_bytes(
                            __import__("hashlib")
                            .sha256(f"{b['slug']}:{i}".encode())
                            .digest()[:8],
                            "big",
                        ),
                        vector=vec,
                        payload={
                            "slug": b.get("slug"),
                            "title": b.get("title"),
                            "author": b.get("author"),
                            "tier": b.get("tier"),
                            "rating_band": b.get("rating_band", ""),
                            "priority": b.get("priority"),
                            "kind": "best_part"
                            if i < len(b.get("best_parts", []))
                            else "beginner_translation",
                            "text": text[:1000],
                        },
                    )
                )
        if points:
            async with asyncio.timeout(10.0):
                await client.upsert(collection_name=COLLECTION, points=points)
        return {"ok": True, "indexed": len(points), "existing": 0, "total": len(books)}
    finally:
        await client.close()


async def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """Retrieve the chess-book fragments most relevant to the query (a blunder
    concept / position description). Dense search first; keyword fallback when
    the embedder or Qdrant is unreachable."""
    vector = await _embed(query)
    if any(vector):
        try:
            from qdrant_client import AsyncQdrantClient

            client = AsyncQdrantClient(
                url=QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY")
            )
            try:
                async with asyncio.timeout(10.0):
                    response = await client.query_points(
                        collection_name=COLLECTION,
                        query=vector,
                        limit=top_k,
                        with_payload=True,
                    )
                points = getattr(response, "points", response) or []
                results = []
                for p in points:
                    payload = getattr(p, "payload", {}) or {}
                    results.append(
                        {
                            "score": getattr(p, "score", 0.0),
                            "slug": payload.get("slug"),
                            "title": payload.get("title"),
                            "author": payload.get("author"),
                            "tier": payload.get("tier"),
                            "rating_band": payload.get("rating_band", ""),
                            "priority": payload.get("priority"),
                            "kind": payload.get("kind"),
                            "text": payload.get("text", ""),
                        }
                    )
                if results:
                    return results
            finally:
                await client.close()
        except Exception as exc:
            log.warning("chess-book dense retrieval failed: %s", exc)

    # Keyword fallback (fail-closed): score fragments by concept keywords.
    concept = _concept_from(query)
    keywords = _CONCEPT_KEYWORDS.get(concept, _CONCEPT_KEYWORDS["tactic"])
    scored = []
    for b in _books():
        text = (
            " ".join(b.get("best_parts", [])[:2])
            + " "
            + b.get("beginner_translation", "")
        )
        score = sum(1 for w in keywords if w in text.lower())
        if score:
            scored.append(
                {
                    "score": score,
                    "slug": b.get("slug"),
                    "title": b.get("title"),
                    "author": b.get("author"),
                    "tier": b.get("tier"),
                    "rating_band": b.get("rating_band", ""),
                    "priority": b.get("priority"),
                    "kind": "best_part",
                    "text": f"{b.get('title')}: {text[:500]}",
                }
            )
    scored.sort(key=lambda r: -r["score"])
    return scored[:top_k]


async def concept_context(classification: str, query: str = "") -> str:
    """Render the retrieved fragments as a short text block for a prompt or a
    deterministic explanation's citation list."""
    frags = await retrieve(f"{_concept_from(classification, query)} {query}", top_k=3)
    if not frags:
        return ""
    return "\n".join(
        f"[{r['title']}] ({r.get('rating_band', '')}): {r['text'][:300]}"
        for r in frags
        if r.get("text")
    )
