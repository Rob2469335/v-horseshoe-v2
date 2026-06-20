from organism_console.review.critic_engine import CriticEngine
from organism_console.memory.skill_journal import SkillJournal

class CriticReinforcer:
    def __init__(self):
        self.critic = CriticEngine()
        self.journal = SkillJournal()

    def reinforce(self, skill, success: bool, confidence: float):
        score = self.critic.score(success, confidence)

        self.journal.log({
            "skill_id": getattr(skill, "id", None),
            "pattern": getattr(skill, "pattern", None),
            "success": success,
            "critic_score": score,
            "confidence": confidence
        })

        # overwrite confidence using critic signal ONLY
        skill.confidence = score

        return skill
