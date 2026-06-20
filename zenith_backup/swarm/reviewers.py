"""
swarm/reviewers.py - Swarm Review
"""
from typing import Dict, Any, List


class SwarmReview:
    def review(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[str] = []
        change = patch.get("after", "")
        
        if "print(" in change:
            issues.append("debug code present")
        if "TODO" in change:
            issues.append("unfinished logic")
        if "rm -rf" in change:
            issues.append("dangerous command")
        
        return {"approved": len(issues) == 0, "issues": issues}
    
    def review_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        all_issues = []
        for patch in plan.get("patches", []):
            review = self.review(patch)
            all_issues.extend(review["issues"])
        
        return {"approved": len(all_issues) == 0, "issues": all_issues}
