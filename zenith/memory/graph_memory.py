"""
memory/graph_memory.py - AST Graph Memory
"""
import ast
import os
from typing import Dict, List, Any


class GraphMemory:
    def __init__(self, root: str = "."):
        self.root = root
        self.nodes = {}
        self.edges = []
        self.files = []
    
    def build(self) -> Dict[str, Any]:
        self.nodes = {}
        self.edges = []
        self.files = []
        
        for path in self._py_files():
            self.files.append(path)
            self._analyze_file(path)
        
        return {"nodes": self.nodes, "edges": self.edges, "files": self.files}
    
    def _py_files(self) -> List[str]:
        for root, _, files in os.walk(self.root):
            for f in files:
                if f.endswith(".py"):
                    yield os.path.join(root, f)
    
    def _analyze_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except:
            return
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                self.nodes[node.name] = {"type": "function", "file": path, "line": node.lineno}
            if isinstance(node, ast.ClassDef):
                self.nodes[node.name] = {"type": "class", "file": path, "line": node.lineno}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.edges.append({"from": "unknown", "to": node.func.id, "file": path})
    
    def find_dependents(self, symbol: str) -> List[str]:
        affected = []
        for edge in self.edges:
            if edge["to"] == symbol:
                affected.append(edge["file"])
        return list(set(affected))
    
    def get_symbol(self, name: str) -> Dict:
        return self.nodes.get(name)
