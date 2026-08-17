from dataclasses import dataclass, field
from datetime import datetime
import numpy as np
import uuid
from qdrant_client import QdrantClient
from qdrant_client.http import models


@dataclass
class Skill:
    id: str
    pattern: str
    confidence: float = 1.0
    success_count: int = 0
    failure_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class SkillRepository:
    def __init__(
        self, qdrant_url: str = "http://localhost:6333", collection: str = "skills"
    ):
        self.client = QdrantClient(url=qdrant_url)
        self.collection = collection
        self._init_collection()

    def _init_collection(self):
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=384, distance=models.Distance.COSINE
                ),
            )

    def upsert(self, skill: Skill, embedding: np.ndarray):
        self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(
                    id=skill.id,
                    vector=embedding.tolist(),
                    payload={
                        "pattern": skill.pattern,
                        "confidence": skill.confidence,
                        "success_count": skill.success_count,
                        "failure_count": skill.failure_count,
                        "created_at": skill.created_at,
                        "updated_at": skill.updated_at,
                    },
                )
            ],
        )

    def all(self):
        results, _ = self.client.scroll(
            collection_name=self.collection, limit=10000, with_payload=True
        )
        return [
            Skill(
                id=str(r.id),
                pattern=r.payload["pattern"],
                confidence=r.payload["confidence"],
                success_count=r.payload["success_count"],
                failure_count=r.payload["failure_count"],
                created_at=r.payload["created_at"],
                updated_at=r.payload["updated_at"],
            )
            for r in results
        ]

    def get(self, id: str):
        result = self.client.retrieve(
            collection_name=self.collection, ids=[id], with_payload=True
        )
        if result:
            p = result[0].payload
            return Skill(
                id=id,
                pattern=p["pattern"],
                confidence=p["confidence"],
                success_count=p["success_count"],
                failure_count=p["failure_count"],
                created_at=p["created_at"],
                updated_at=p["updated_at"],
            )
        return None

    def search(self, query_emb: np.ndarray, top_k: int = 3):
        search_result = self.client.query_points(
            collection_name=self.collection,
            query=query_emb.tolist(),
            limit=top_k,
            with_payload=True,
        )
        return [
            (
                Skill(
                    id=str(pt.id),
                    pattern=pt.payload["pattern"],
                    confidence=pt.payload["confidence"],
                    success_count=pt.payload["success_count"],
                    failure_count=pt.payload["failure_count"],
                    created_at=pt.payload["created_at"],
                    updated_at=pt.payload["updated_at"],
                ),
                pt.score,
            )
            for pt in search_result.points
        ]

    def generate_id(self) -> str:
        return str(uuid.uuid4())
