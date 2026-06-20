from organism_console.skills.skill_repository import Skill
from swarm_os.memory.intelligence.skill_memory_engine import SkillMemoryEngine
from typing import List

class SelfImprovementAgent:
    def __init__(self):
        self.memory = SkillMemoryEngine()
        self.upgrade_log = []

    def analyze_and_upgrade(self):
        """Analyze current system and suggest/implement upgrades"""
        skills = self.memory._get_repo().all()
        
        print("[self-improvement] Analyzing system...")
        print(f"[self-improvement] Total skills: {len(skills)}")
        
        # Analyze skill patterns
        patterns = [s.pattern for s in skills]
        
        # Suggest upgrades
        upgrades = self._suggest_upgrades(patterns, skills)
        
        print(f"[self-improvement] Found {len(upgrades)} potential upgrades")
        
        for upgrade in upgrades:
            print(f"[self-improvement] {upgrade['type']}: {upgrade['description']}")
        
        return upgrades

    def _suggest_upgrades(self, patterns: List[str], skills: List[Skill]):
        """Suggest system upgrades based on current state"""
        upgrades = []
        
        # Upgrade 1: Generalization needed?
        import_patterns = [p for p in patterns if "import error" in p]
        if len(import_patterns) > 2:
            upgrades.append({
                "type": "generalization",
                "description": f"Merge {len(import_patterns)} import skills into abstraction",
                "priority": "high"
            })
        
        # Upgrade 2: Forgetting curve needed?
        low_confidence = [s for s in skills if s.confidence < 0.5]
        if len(low_confidence) > 0:
            upgrades.append({
                "type": "forgetting",
                "description": f"Decay {len(low_confidence)} weak skills",
                "priority": "medium"
            })
        
        # Upgrade 3: More generalization patterns?
        unique_patterns = len(set(patterns))
        if unique_patterns > 5:
            upgrades.append({
                "type": "abstraction",
                "description": f"Create abstractions for {unique_patterns} patterns",
                "priority": "high"
            })
        
        return upgrades

    def execute_upgrade(self, upgrade: dict):
        """Execute a suggested upgrade"""
        if upgrade["type"] == "generalization":
            from organism_console.skills.skill_generalizer import SkillGeneralizer
            gen = SkillGeneralizer()
            merges = gen.merge_similar_skills()
            print(f"[self-improvement] Executed generalization: {merges} merges")
        
        elif upgrade["type"] == "forgetting":
            self._execute_forgetting()
            print(f"[self-improvement] Executed forgetting curve")
        
        self.upgrade_log.append(upgrade)
