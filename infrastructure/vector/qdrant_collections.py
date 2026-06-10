from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.http import models

from infrastructure.config.settings import get_settings


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    size: int
    distance: models.Distance


def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    if settings.qdrant_api_key:
        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return QdrantClient(url=settings.qdrant_url)


def get_collection_specs() -> list[CollectionSpec]:
    settings = get_settings()
    return [
        CollectionSpec(
            name=settings.qdrant_collection_traces,
            size=settings.embedding_dimension,
            distance=models.Distance.COSINE,
        ),
        CollectionSpec(
            name=settings.qdrant_collection_memory,
            size=settings.embedding_dimension,
            distance=models.Distance.COSINE,
        ),
    ]


def ensure_collections() -> list[str]:
    client = get_qdrant_client()
    created_or_verified: list[str] = []

    for spec in get_collection_specs():
        exists = client.collection_exists(spec.name)
        if not exists:
            client.create_collection(
                collection_name=spec.name,
                vectors_config=models.VectorParams(
                    size=spec.size,
                    distance=spec.distance,
                ),
            )
        created_or_verified.append(spec.name)

    return created_or_verified
