"""
context_retriever.py - Queries Qdrant for relevant code chunks before agent prompts.
This is what makes rob context-aware like Copilot.
"""
from __future__ import annotations

import logging
import httpx

logger = logging.getLogger(__name__)

COLLECTION = "codebase"
EMBED_MODEL = "nomic-embed-text:latest"
EMBED_URL = "http://127.0.0.1:8081/v1"
OLLAMA_URL = EMBED_URL  # Backward compatibility alias
QDRANT_URL = "http://127.0.0.1:6333"
MAX_CONTEXT_CHUNKS = 5
MAX_CONTEXT_CHARS = 6000


def _embed(text: str) -> list[float]:
    try:
        r = httpx.post(
            f"{EMBED_URL}/embeddings",
            headers={"Authorization": "Bearer llama"},
            json={"input": text},
            timeout=60.0,
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]
    except Exception as exc:
        logger.warning(f"Embed failed: {exc}")
        return [0.0] * EMBED_DIM


def retrieve(query: str, top_k: int = MAX_CONTEXT_CHUNKS) -> list[dict]:
    """Return the most relevant code chunks for a query."""
    vec = _embed(query)
    try:
        r = httpx.post(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
            json={"vector": vec, "limit": top_k, "with_payload": True},
            timeout=15.0,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as exc:
        logger.warning(f"Retrieval failed: {exc}")
        return []


def build_context_prompt(query: str) -> str:
    """
    Retrieve relevant code chunks and prepend them to the user query.
    This is injected before every agent prompt so the model has real context.
    """
    chunks = retrieve(query)
    if not chunks:
        return query

    parts = ["### Relevant code context from your project:\n"]
    total_chars = 0

    for hit in chunks:
        payload = hit.get("payload", {})
        file_path = payload.get("file", "unknown")
        start = payload.get("start_line", "?")
        end = payload.get("end_line", "?")
        text = payload.get("text", "")
        score = hit.get("score", 0)

        if total_chars + len(text) > MAX_CONTEXT_CHARS:
            break

        # Make file path relative-looking for cleaner display
        short_path = file_path.replace("C:\\Users\\rober\\Projects\\v-horseshoe-v2\\", "")
        parts.append(f"# {short_path} (lines {start}-{end}, relevance: {score:.2f})\n```\n{text}\n```\n")
        total_chars += len(text)

    parts.append(f"\n### User request:\n{query}")
    return "\n".join(parts)



