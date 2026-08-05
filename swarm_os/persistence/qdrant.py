# lib/vector/qdrant_store.py
"""
Primary long-term memory layer.
Uses Qdrant in local (embedded) mode by default — no server needed.
Switch to server mode by setting QDRANT_LOCAL=false in .env.
"""
import logging
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct
)
import httpx

from config.settings import (
    QDRANT_DIR, QDRANT_HOST, QDRANT_PORT, QDRANT_LOCAL,
    COLLECTIONS, EMBED_MODEL, EMBED_DIM, OLLAMA_BASE_URL,
)

log = logging.getLogger("qdrant_store")
_client: AsyncQdrantClient | None = None


def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        if QDRANT_LOCAL:
            _client = AsyncQdrantClient(path=str(QDRANT_DIR))
            log.info(f"Qdrant running in local mode at {QDRANT_DIR}")
        else:
            _client = AsyncQdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
            log.info(f"Qdrant connected to {QDRANT_HOST}:{QDRANT_PORT}")
    return _client


async def init_collections() -> None:
    """Create all 4 collections if they don't already exist."""
    client = get_client()
    existing = {c.name for c in await client.get_collections().collections}

    for key, name in COLLECTIONS.items():
        if name not in existing:
            await client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=EMBED_DIM,
                    distance=Distance.COSINE,
                ),
            )
            log.info(f"Created Qdrant collection: {name} ({key})")
        else:
            log.debug(f"Collection exists: {name}")


_embed_client: httpx.AsyncClient | None = None


def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None:
        _embed_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
        )
    return _embed_client


async def _embed(text: str) -> list[float]:
    """Get embedding from Ollama nomic-embed-text."""
    client = _get_embed_client()
    resp = await client.post(
        f"{OLLAMA_BASE_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


async def upsert(collection_key: str, doc_id: str, text: str, payload: dict[str, Any]) -> None:
    """Embed text and store in the named collection."""
    name = COLLECTIONS[collection_key]
    vector = await _embed(text)
    client = get_client()
    await client.upsert(
        collection_name=name,
        points=[PointStruct(id=doc_id, vector=vector, payload={**payload, "text": text})],
    )


async def search(collection_key: str, query: str, top_k: int = 20) -> list[dict]:
    """Semantic search — returns top_k candidates before reranking."""
    name = COLLECTIONS.get(collection_key, collection_key)
    vector = await _embed(query)
    client = get_client()
    try:
        if hasattr(client, "search"):
            results = await client.search(
                collection_name=name,
                query_vector=vector,
                limit=top_k,
                with_payload=True,
            )
        else:
            response = await client.query_points(
                collection_name=name,
                query=vector,
                limit=top_k,
                with_payload=True,
            )
            results = getattr(response, "points", response)
    except Exception as exc:
        log.warning(
            "Qdrant search failed once for collection=%s error=%s: %s",
            name,
            exc.__class__.__name__,
            exc,
        )
        return []
    return [{"score": r.score, "payload": r.payload} for r in results]
