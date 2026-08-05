"""
brain/graph_analyzer.py - AST Graph Analysis
"""
from typing import Dict, List, Any


class GraphAnalyzer:
    def __init__(self, graph_memory):
        self.graph = graph_memory
    
    def find_dependents(self, symbol: str) -> List[str]:
        return self.graph.find_dependents(symbol)
    
    def find_symbol(self, name: str) -> Dict:
        return self.graph.get_symbol(name)
    
    def analyze_impact(self, symbol: str) -> Dict[str, Any]:
        dependents = self.find_dependents(symbol)
        symbol_info = self.find_symbol(symbol)
        return {
            "symbol": symbol,
            "symbol_file": symbol_info.get("file") if symbol_info else None,
            "affected_files": dependents,
            "impact_size": len(dependents)
        }
