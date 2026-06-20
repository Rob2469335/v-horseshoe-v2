from typing import Dict, Any

class ScoringEngine:
    """
    Pure deterministic scoring logic.
    No side effects.
    """
    @staticmethod
    def calculate(ci_results: Dict[str, Any], confidence: float = 0.8) -> float:
        score = 0.4 # Base Score
        
        # 1. Compile (0.3)
        if ci_results.get("compile", {}).get("status") == "ok":
            score += 0.3
            
        # 2. Tests (0.2)
        if ci_results.get("tests", {}).get("status") == "ok":
            score += 0.2
            
        # 3. Lint (0.1)
        if ci_results.get("lint", {}).get("status") == "ok":
            score += 0.1
            
        # Optional: Add small weight for AI confidence
        score += (confidence * 0.05)
        
        return round(min(score, 1.0), 2)
