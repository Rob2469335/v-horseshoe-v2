"""
memory/zen_memory.py - Unified Zenith Memory
"""
from typing import Dict, Any, List
from .graph_memory import GraphMemory
from .vector_memory import VectorMemory
from .style_profile import StyleProfile


class ZenithMemory:
    def __init__(self, root: str = "."):
        self.graph = GraphMemory(root)
        self.vector = VectorMemory()
        self.style = StyleProfile()
        self.history: List[Dict] = []
    
    def build_graph(self) -> Dict[str, Any]:
        return self.graph.build()
    
    def add_vector(self, text: str, embedding: List[float]):
        self.vector.add(text, embedding)
    
    def update_style(self, code_sample: str):
        self.style.update(code_sample)
    
    def log_event(self, event: Dict):
        self.history.append(event)
    
    def get_context(self) -> Dict[str, Any]:
        return {
            "graph": {
                "nodes": self.graph.nodes,
                "edges": self.graph.edges,
                "files": self.graph.files
            },
            "vectors": self.vector.vectors[:10],
            "style": self.style.patterns,
            "history": self.history[-20:]
        }
