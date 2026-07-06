import requests
from typing import List, Dict, Any
from .indexer import get_embedding, QDRANT_URL, COLLECTION_NAME

def semantic_search(query: str, limit: int = 5) -> str:
    """Searches the codebase index and returns a formatted string of relevant snippets."""
    vector = get_embedding(query)
    if not vector:
        return "Error: Could not generate embedding for query. Is Ollama running?"
        
    try:
        resp = requests.post(f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search", json={
            "vector": vector,
            "limit": limit,
            "with_payload": True
        }, timeout=10.0)
        
        if resp.status_code != 200:
            return f"Error: Qdrant search failed with status {resp.status_code}."
            
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
    except Exception as e:
        return f"Error performing semantic search: {e}"
