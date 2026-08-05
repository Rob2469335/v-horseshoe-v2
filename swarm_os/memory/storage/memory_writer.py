from __future__ import annotations

from typing import Any
from uuid import uuid4

from qdrant_client import QdrantClient, models
from qdrant_client.models import PointStruct, VectorParams, Distance


class MemoryWriter:
    def __init__(self, client: QdrantClient, collection: str, vector_size: int = 1536):
        self.client = client
        self.collection = collection
        self.vector_size = vector_size
        self._ensure_collection()
        self._ensure_payload_indexes()

    def _ensure_collection(self) -> None:
        exists = False
        try:
            collections = self.client.get_collections().collections
            exists = any(c.name == self.collection for c in collections)
        except Exception:
            exists = False

        if not exists:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
            )
            return

        try:
            info = self.client.get_collection(self.collection)
            current = getattr(info.config.params, "vectors", None)
            if isinstance(current, dict):
                current = next(iter(current.values()))
            current_size = getattr(current, "size", None)
            if current_size and int(current_size) != int(self.vector_size):
                self.client.delete_collection(self.collection)
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
                )
        except Exception:
            pass

    def _ensure_payload_indexes(self) -> None:
        for field_name in ["memory_type", "topic", "tool_sequence"]:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass

    def write_episode(
        self,
        trace_id: str,
        summary: str,
        vector: list[float],
        memory_type: str,
        payload: dict[str, Any],
    ) -> str:
        point_id = str(uuid4())
        full_payload = {"trace_id": trace_id, "summary": summary, "memory_type": memory_type, **payload}
        self.client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point_id, vector=vector, payload=full_payload)],
            wait=True,
        )
        return point_id
