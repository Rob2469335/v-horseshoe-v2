"""
memory/style_profile.py - Coding Style Profile
"""
from typing import Dict, Any


class StyleProfile:
    def __init__(self):
        self.patterns: Dict[str, Any] = {
            "verbosity": 0.5,
            "error_handling": "moderate",
            "modularity": "medium",
            "async_usage": False,
            "typing": False
        }
    
    def update(self, code_sample: str):
        if "try:" in code_sample:
            self.patterns["error_handling"] = "high"
        if "class " in code_sample:
            self.patterns["modularity"] = "high"
        if "async " in code_sample:
            self.patterns["async_usage"] = True
        if "def " in code_sample and ":" in code_sample:
            self.patterns["typing"] = True
        return self.patterns
    
    def get_style_hint(self) -> str:
        hints = []
        if self.patterns["error_handling"] == "high":
            hints.append("use try/except")
        if self.patterns["modularity"] == "high":
            hints.append("use classes")
        if self.patterns["async_usage"]:
            hints.append("use async")
        return ", ".join(hints)
