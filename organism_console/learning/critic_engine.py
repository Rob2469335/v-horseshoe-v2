from datetime import datetime, timedelta
import json
from organism_console.skills.skill_repository import SkillRepository

class CriticEngine:
    def __init__(self, decay=0.995, merge_threshold=0.92):
        self.repo = SkillRepository()
        self.decay = decay
        self.merge_threshold = merge_threshold

    def score(self, skill):
        base = skill.confidence
        age_factor = self._age_decay(skill)
        stability = (skill.success_count + 1) / (skill.success_count + skill.failure_count + 2)
        return base * 0.6 + stability * 0.4 * age_factor

    def _age_decay(self, skill):
        try:
            updated = datetime.fromisoformat(skill.updated_at)
            days = (datetime.utcnow() - updated).days
            return self.decay ** days
        except (ValueError, TypeError):
            return 1.0

    def evolve(self):
        skills = self.repo.all()
        updated = []

        for s in skills:
            new_score = self.score(s)

            # DECAY confidence
            s.confidence = new_score

            # prune weak skills
            if new_score < 0.25:
                continue

            updated.append(s)
            self.repo.upsert(s, self.repo.embed(s.pattern))

        print(f"[critic] evolved {len(updated)} skills")
        return updated

    def merge_similar(self):
        skills = self.repo.all()

        for i, a in enumerate(skills):
            for b in skills[i+1:]:
                if self._similar(a.pattern, b.pattern):
                    # merge weaker into stronger
                    winner = a if a.confidence >= b.confidence else b
                    winner.success_count += a.success_count + b.success_count
                    winner.failure_count += a.failure_count + b.failure_count
                    winner.confidence = self.score(winner)
                    self.repo.upsert(winner, self.repo.embed(winner.pattern))

    def _similar(self, a, b):
        # simple heuristic (no extra libs)
        return a.split(":")[0:3] == b.split(":")[0:3]
