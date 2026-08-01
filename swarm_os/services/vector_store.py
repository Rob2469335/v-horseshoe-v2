"""
vector_store.py - Qdrant vector database for memory/embeddings.
"""
from __future__ import annotations

import asyncio
import uuid
import logging
from typing import List, Dict, Any, Optional

from qdrant_client import AsyncQdrantClient
from swarm_os.core.settings import get_settings
from qdrant_client import models

logger = logging.getLogger(__name__)


class VectorStore:
    """Vector store using Qdrant for semantic memory and chat archive search."""

    def __init__(
        self,
        collection_name: str = "swarm_memory",
        vector_size: int = 768,
        use_memory: bool = False
    ):
        if use_memory:
            # AsyncQdrantClient supports :memory:
            self.client = AsyncQdrantClient(":memory:")
            logger.info("Initialized in-memory AsyncQdrantClient")
        else:
            settings = get_settings()
            self.client = AsyncQdrantClient(url=settings.qdrant_url)
            logger.info("Connected to local Qdrant instance")

        self.collection_name = collection_name
        self.vector_size = vector_size
        try:
            loop = asyncio.get_running_loop()
            self._init_task = loop.create_task(self._ensure_collection(vector_size))
        except RuntimeError:
            self._init_task = None
        self._ensured = False

    async def _wait_init(self):
        if self._init_task:
            await self._init_task
        elif not self._ensured:
            await self._ensure_collection(self.vector_size)
            self._ensured = True

    async def _ensure_collection(self, vector_size: int = 768):
        """Create collection if it doesn't exist."""
        try:
            if not await self.client.collection_exists(self.collection_name):
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE
                    ),
                )
                try:
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name="tasks",
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name="types",
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name="models",
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name="consolidated",
                        field_schema=models.PayloadSchemaType.BOOL,
                    )
                    # UPGRADE: index the fields memory_core filters on (category shards,
                    # timestamp decay) so filtered queries don't do full payload scans.
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name="category",
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                    await self.client.create_payload_index(
                        collection_name=self.collection_name,
                        field_name="timestamp",
                        field_schema=models.PayloadSchemaType.FLOAT,
                    )
                except Exception as e:
                    logger.warning("Could not create payload indexes: %s", e)
                    
                logger.info(f"Created collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Error ensuring collection: {e}")

    async def upsert(
        self,
        doc_id: Optional[str],
        vector: List[float],
        payload: Dict[str, Any]
    ) -> str:
        """Upsert a vector with payload. Returns doc_id."""
        await self._wait_init()
        if doc_id is None:
            doc_id = str(uuid.uuid4())

        await self.client.upsert(
            collection_name=self.collection_name,
            points=[models.PointStruct(id=doc_id, vector=vector, payload=payload)]
        )
        logger.debug(f"Upserted document: {doc_id}")
        return doc_id

    async def search(
        self,
        query_vector: List[float],
        limit: int = 5,
        filter_condition: Optional[models.Filter] = None
    ) -> List[Dict[str, Any]]:
        """Search for similar vectors."""
        await self._wait_init()
        try:
            if hasattr(self.client, "search"):
                results = await self.client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=limit,
                    query_filter=filter_condition
                )
            else:
                response = await self.client.query_points(
                    collection_name=self.collection_name,
                    query=query_vector,
                    limit=limit,
                    query_filter=filter_condition,
                )
                results = getattr(response, "points", response)
        except Exception as exc:
            logger.warning(
                "Qdrant search failed once for collection=%s error=%s: %s",
                self.collection_name,
                exc.__class__.__name__,
                exc,
            )
            return []

        return [
            {
                "id": result.id,
                "score": result.score,
                "payload": result.payload,
            }
            for result in results
        ]

    async def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a document by ID."""
        await self._wait_init()
        try:
            points = await self.client.retrieve(
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

    async def delete(self, doc_id: str) -> bool:
        """Delete a document by ID."""
        await self._wait_init()
        try:
            await self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(points=[doc_id])
            )
            return True
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            return False

    async def count(self) -> int:
        """Return number of documents in collection."""
        await self._wait_init()
        try:
            info = await self.client.get_collection_info(self.collection_name)
            return info.points_count or 0
        except Exception:
            return 0


