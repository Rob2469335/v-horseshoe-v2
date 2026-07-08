"""
code_indexer.py - Chunks your project files and upserts into Qdrant.
Uses nomic-embed-text:latest for high-quality code embeddings.
"""
from __future__ import annotations

import hashlib
import logging
import os
import traceback
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

COLLECTION = "codebase"
EMBED_MODEL = "nomic-embed-text:latest"
EMBED_DIM = 768
OLLAMA_URL = "http://127.0.0.1:11434"
QDRANT_URL = "http://127.0.0.1:6333"

INCLUDE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".yaml", ".toml"}
EXCLUDE_DIRS = {"node_modules", "__pycache__", ".git", "dist", "build", ".venv", "_legacy_kernel_backup"}
CHUNK_LINES = 30
OVERLAP_LINES = 10


def _chunk_file(path: Path) -> list[dict]:
    """Split a file into overlapping line chunks with metadata."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        logger.error(f"Failed to read file {path}: {e}", exc_info=True)
        return []

    chunks = []
    step = CHUNK_LINES - OVERLAP_LINES
    for start in range(0, max(1, len(lines)), step):
        end = min(start + CHUNK_LINES, len(lines))
        text = "\n".join(lines[start:end]).strip()
        if len(text) < 30:
            continue
        chunks.append({
            "text": text,
            "file": str(path),
            "start_line": start + 1,
            "end_line": end,
            "lang": path.suffix.lstrip("."),
        })
        if end >= len(lines):
            break
    return chunks


def _embed(text: str) -> list[float]:
    """Generate embedding for text using Ollama API."""
    try:
        r = httpx.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=300.0,
        )
        r.raise_for_status()
        response_data = r.json()
        if "embedding" not in response_data:
            raise ValueError("Invalid response: 'embedding' key missing")
        return response_data["embedding"]
    except httpx.RequestError as e:
        logger.error(f"Network error during embedding: {e}", exc_info=True)
        return [0.0] * EMBED_DIM
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}", exc_info=True)
        return [0.0] * EMBED_DIM


def _ensure_collection() -> None:
    try:
        r = httpx.get(f"{QDRANT_URL}/collections/{COLLECTION}", timeout=10)
        if r.status_code == 200:
            logger.debug(f"Collection '{COLLECTION}' already exists")
            return
        httpx.put(
            f"{QDRANT_URL}/collections/{COLLECTION}",
            json={"vectors": {"size": EMBED_DIM, "distance": "Cosine"}},
            timeout=10,
        ).raise_for_status()
        logger.info(f"Created Qdrant collection: {COLLECTION}")
    except httpx.RequestError as e:
        logger.error(f"Network error ensuring collection: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Failed to ensure collection: {e}", exc_info=True)


def _upsert(points: list[dict]) -> None:
    try:
        httpx.put(
            f"{QDRANT_URL}/collections/{COLLECTION}/points",
            json={"points": points},
            timeout=30,
        ).raise_for_status()
    except httpx.RequestError as e:
        logger.error(f"Network error during upsert: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Upsert failed: {e}", exc_info=True)


def index_project(root: str | Path) -> int:
    """
    Walk root, chunk all code files, embed and upsert into Qdrant.
    Returns number of chunks indexed.
    """
    root = Path(root)
    _ensure_collection()

    total = 0
    batch: list[dict] = []

    for path in root.rglob("*"):
        if path.suffix not in INCLUDE_EXTS:
            continue
        if any(ex in path.parts for ex in EXCLUDE_DIRS):
            continue

        for chunk in _chunk_file(path):
            uid = int(hashlib.md5(
                f"{chunk['file']}:{chunk['start_line']}".encode()
            ).hexdigest()[:15], 16)

            vec = _embed(chunk["text"])
            batch.append({
                "id": uid,
                "vector": vec,
                "payload": chunk,
            })

            if len(batch) >= 20:
                _upsert(batch)
                total += len(batch)
                logger.info(f"Indexed {total} chunks...")
                batch.clear()

    if batch:
        _upsert(batch)
        total += len(batch)

    logger.info(f"Indexing complete: {total} chunks in collection '{COLLECTION}'")
    return total
