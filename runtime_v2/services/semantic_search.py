import requests
from .indexer import get_embeddings, QDRANT_URL, COLLECTION_NAME

def semantic_search(query: str, limit: int = 5) -> str:
    """Searches the codebase index and returns a formatted string of relevant snippets."""
    vectors = get_embeddings([query])
    vector = vectors[0] if vectors else None
    if not vector:
        raise RuntimeError("Error: Could not generate embedding for query. Is the llama.cpp embedding service running?")
        
    try:
        resp = requests.post(f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search", json={
            "vector": vector,
            "limit": limit,
            "with_payload": True
        }, timeout=10.0)
        
        if resp.status_code != 200:
            raise RuntimeError(f"Error: Qdrant search failed with status {resp.status_code}.")
            
        results = resp.json().get("result", [])
        if not results:
            return "No relevant code snippets found in the codebase index. Has the codebase been indexed?"
            
        output = []
        for i, r in enumerate(results):
            score = r.get("score", 0.0)
            payload = r.get("payload", {})
            path = payload.get("path", "unknown")
            name = payload.get("name", "unknown")
            code = payload.get("content", "")
            
            output.append(f"--- Result {i+1} (Relevance: {score:.2f}) ---")
            output.append(f"File: {path}")
            output.append(f"Symbol: {name}")
            output.append(f"Code:\n```python\n{code}\n```\n")
            
        return "\n".join(output)
    except requests.RequestException as e:
        raise RuntimeError(f"Network error performing semantic search: {e}")
