"""
embedding_service.py - Generate embeddings using llama.cpp.
"""
from __future__ import annotations

import httpx
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generate embeddings using llama.cpp model."""

    def __init__(self, base_url: str = "http://127.0.0.1:8081", model: str = "nomic-embed-text-v1.5.Q8_0"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.AsyncClient(timeout=120.0, headers={"Authorization": "Bearer llama"})
        logger.info(f"Initialized EmbeddingService with model: {model}")

    async def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        try:
            response = await self.client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "input": text}
            )
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            # Raise error to avoid Qdrant mathematical crash on zero-vector
            raise RuntimeError(f"Embedding failed: {e}")

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        import asyncio
        return await asyncio.gather(*(self.embed(text) for text in texts))

    async def aclose(self) -> None:
        """Close the underlying httpx client to release connection pool."""
        await self.client.aclose()

