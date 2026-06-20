"""
brain/retriever.py - Hybrid Retriever (Graph + Vector)
"""
from typing import Dict, List, Any


class Retriever:
    def __init__(self, graph_memory, vector_memory):
        self.graph = graph_memory
        self.vector = vector_memory
    
    def query(self, text: str) -> Dict[str, Any]:
        graph_hits = self.graph.nodes
        vector_hits = self.vector.vectors[:5]
        return {"graph": graph_hits, "semantic": vector_hits}
    
    def find_relevant_files(self, task: str) -> List[str]:
        return self.graph.files[:5]
