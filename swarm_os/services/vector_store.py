"""
vector_store.py - Qdrant vector database for memory/embeddings.
"""
from __future__ import annotations

import uuid
import logging
from typing import List, Dict, Any, Optional

from qdrant_client import QdrantClient
from qdrant_client import models

logger = logging.getLogger(__name__)


class VectorStore:
    """Vector store using Qdrant for semantic memory and chat archive search."""

    def __init__(
        self,
        collection_name: str = "swarm_memory",
        vector_size: int = 768,
        use_memory: bool = True
    ):
        if use_memory:
            # In-memory for development/testing
            self.client = QdrantClient(":memory:")
            logger.info("Initialized in-memory QdrantClient")
        else:
            # Production: connect to local Qdrant instance
            self.client = QdrantClient(host="localhost", port=6333)
            logger.info("Connected to local Qdrant instance")

        self.collection_name = collection_name
        self._ensure_collection(vector_size)

    def _ensure_collection(self, vector_size: int = 768):
        """Create collection if it doesn't exist."""
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE
                ),
            )
            logger.info(f"Created collection: {self.collection_name}")

    def upsert(
        self,
        doc_id: Optional[str],
        vector: List[float],
        payload: Dict[str, Any]
    ) -> str:
        """Upsert a vector with payload. Returns doc_id."""
        if doc_id is None:
            doc_id = str(uuid.uuid4())

        self.client.upsert(
            collection_name=self.collection_name,
            points=[models.PointStruct(id=doc_id, vector=vector, payload=payload)]
        )
        logger.debug(f"Upserted document: {doc_id}")
        return doc_id

    def search(
        self,
        query_vector: List[float],
        limit: int = 5,
        filter_condition: Optional[models.Filter] = None
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors."""
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=limit,
            query_filter=filter_condition
        )

        return [
            {
                "id": result.id,
                "score": result.score,
                "payload": result.payload,
            }
            for result in results
        ]

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document by ID."""
        try:
            points = self.client.retrieve(
                collection_name=self.collection_name,
                ids=[doc_id]
            )
            if points:
                return {
                    "id": points[0].id,
                    "payload": points[0].payload
                }
        except Exception as e:
            logger.error(f"Retrieve failed: {e}")
        return None

    def delete(self, doc_id: str) -> bool:
        """Delete a document by ID."""
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=[doc_id])
            )
            return True
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            return False

    def count(self) -> int:
        """Return number of documents in collection."""
        try:
            info = self.client.get_collection_info(self.collection_name)
            return info.points_count or 0
        except Exception:
            return 0
