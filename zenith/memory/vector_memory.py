"""
memory/vector_memory.py - Vector Memory (Semantic Embeddings)
"""
from typing import Dict, List, Any


class VectorMemory:
    def __init__(self):
        self.vectors = []
    
    def add(self, text: str, embedding: List[float]):
        self.vectors.append({"text": text, "embedding": embedding})
    
    def search(self, query_embedding: List[float], top_k: int = 5) -> List[Dict]:
        return self.vectors[:top_k]
    
    def clear(self):
        self.vectors = []
