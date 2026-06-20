"""
brain/planner.py - Intent -> Structured Plan
"""
from typing import Dict, Any, List


class Planner:
    def __init__(self, llm_fn=None):
        self.llm = llm_fn
    
    def create_plan(self, task: str, memory_context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "intent": task,
            "target_files": memory_context.get("graph", {}).get("files", [])[:5],
            "affected_symbols": memory_context.get("graph", {}).get("nodes", {}),
            "retrieved_context": memory_context.get("vectors", [])[:5],
            "patches": [
                {
                    "file": "example.py",
                    "before": "old code",
                    "after": "new code with error handling"
                }
            ]
        }
    
    def create_repair_plan(self, original_plan: Dict, error: str) -> Dict[str, Any]:
        return {
            "intent": f"fix error: {error}",
            "patches": original_plan.get("patches", [])
        }
