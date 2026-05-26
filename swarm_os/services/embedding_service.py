"""
embedding_service.py - Generate embeddings using Ollama.
"""
from __future__ import annotations

import httpx
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Generate embeddings using Ollama's nomic-embed-text model."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "nomic-embed-text"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.Client(timeout=30.0)
        logger.info(f"Initialized EmbeddingService with model: {model}")

    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        try:
            response = self.client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text}
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            # Return zero vector as fallback
            return [0.0] * 768

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        return [self.embed(text) for text in texts]
