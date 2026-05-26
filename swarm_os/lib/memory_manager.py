from qdrant_client import QdrantClient

def should_archive(client: QdrantClient, new_embedding: list, threshold: float = 0.95) -> bool:
    """
    Checks if a similar strategy already exists in the memory.
    """
    search_result = client.search(
        collection_name="chat_archive",
        query_vector=new_embedding,
        limit=1
    )
    
    # If a very similar memory exists, don't archive; just return False
    if search_result and search_result[0].score > threshold:
        return False
    return True
