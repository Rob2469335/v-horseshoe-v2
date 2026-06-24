from qdrant_client import QdrantClient
from qdrant_client.http import models as rest
from datetime import datetime
import uuid

COLLECTION = "upwork_learning"

client = QdrantClient(url="http://127.0.0.1:6333")


def init_collection(vector_size: int):
    collections = [c.name for c in client.get_collections().collections]

    if COLLECTION not in collections:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=rest.VectorParams(
                size=vector_size,
                distance=rest.Distance.COSINE
            )
        )


# v2: store dual embeddings (job + proposal separation)
def store_proposal(job_vector, proposal_vector, payload: dict):
    client.upsert(
        collection_name=COLLECTION,
        points=[
            rest.PointStruct(
                id=str(uuid.uuid4()),
                vector=job_vector,
                payload={
                    **payload,
                    "proposal_vector": proposal_vector,
                    "created_at": datetime.utcnow().isoformat()
                }
            )
        ]
    )


def search_similar(vector, limit=30):
    try:
        if hasattr(client, "search"):
            return client.search(
                collection_name=COLLECTION,
                query_vector=vector,
                limit=limit,
                with_payload=True
            )
        response = client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=limit,
            with_payload=True,
        )
        return getattr(response, "points", response)
    except Exception:
        return []
