"""
brain/brain_core.py - Unified Brain
"""
from typing import Dict, Any
from .planner import Planner
from .graph_analyzer import GraphAnalyzer
from .retriever import Retriever


class BrainCore:
    def __init__(self, memory):
        self.planner = Planner()
        self.analyzer = GraphAnalyzer(memory.graph)
        self.retriever = Retriever(memory.graph, memory.vector)
        self.memory = memory
    
    def create_plan(self, task: str) -> Dict[str, Any]:
        context = self.memory.get_context()
        return self.planner.create_plan(task, context)
    
    def analyze_impact(self, symbol: str) -> Dict[str, Any]:
        return self.analyzer.analyze_impact(symbol)
    
    def query(self, text: str) -> Dict[str, Any]:
        return self.retriever.query(text)
