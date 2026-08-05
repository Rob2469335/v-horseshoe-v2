from swarm_os.memory.intelligence.skill_memory_engine import SkillMemoryEngine
from datetime import datetime

class ForgettingCurve:
    def __init__(self, decay_rate: float = 0.01, min_confidence: float = 0.2):
        self.memory = SkillMemoryEngine()
        self.decay_rate = decay_rate
        self.min_confidence = min_confidence

    def decay_all(self):
        """Apply forgetting curve to all skills"""
        repo = self.memory._get_repo()
        skills = repo.all()
        
        decayed = 0
        for skill in skills:
            old_conf = skill.confidence
            
            # Decay confidence slightly
            skill.confidence = max(
                self.min_confidence,
                skill.confidence * (1 - self.decay_rate)
            )
            
            skill.updated_at = datetime.utcnow().isoformat()
            repo.upsert(skill, self.memory.embed(skill.pattern))
            
            if old_conf != skill.confidence:
                decayed += 1
        
        print(f"[forgetting] Decayed {decayed} skills")
        return decayed

    def prune_weak_skills(self):
        """Remove skills below minimum confidence"""
        repo = self.memory._get_repo()
        skills = repo.all()
        
        pruned = 0
        for skill in skills:
            if skill.confidence < self.min_confidence:
                repo.client.delete_collection("skills")  # Simplified
                pruned += 1
        
        print(f"[forgetting] Pruned {pruned} weak skills")
        return pruned
