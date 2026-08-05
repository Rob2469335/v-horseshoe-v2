from organism_console.skills.skill_repository import Skill
import numpy as np
import uuid
from datetime import datetime

try:
    from fastembed import TextEmbedding
except ImportError:  # optional embedding backend; embed() degrades gracefully
    TextEmbedding = None

class SkillMemoryEngine:
    def __init__(self):
        self.repo = None
        self.embedder = TextEmbedding() if TextEmbedding is not None else None

    def _get_repo(self):
        if self.repo is None:
            from organism_console.skills.skill_repository import SkillRepository
            self.repo = SkillRepository()
        return self.repo

    def embed(self, text: str) -> np.ndarray:
        if self.embedder is None:
            raise RuntimeError("fastembed is not installed — cannot embed skills. `pip install fastembed`.")
        emb = next(self.embedder.embed([text]))
        return np.asarray(emb, dtype=np.float32)

    def merge_or_add_by_pattern(self, pattern: str):
        repo = self._get_repo()

        similar = self.find_similar(pattern, top_k=1)
        if similar and similar[0][1] > 0.95:
            existing, score = similar[0]
            existing.success_count += 1
            existing.confidence = self.bayesian_confidence(existing.success_count, existing.failure_count)
            existing.updated_at = datetime.utcnow().isoformat()
            repo.upsert(existing, self.embed(existing.pattern))
            print(f"[memory] Reinforced skill {existing.id}: confidence {existing.confidence}")
            return existing
        else:
            skill_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, pattern))
            skill = Skill(
                id=skill_id,
                pattern=pattern,
                success_count=1,
                failure_count=0,
                confidence=self.bayesian_confidence(1, 0)
            )
            repo.upsert(skill, self.embed(pattern))
            print(f"[memory] Created new skill {skill.id}: confidence {skill.confidence}")
            return skill

    def bayesian_confidence(self, success_count: int, failure_count: int):
        return (success_count + 1) / (success_count + failure_count + 2)

    def reinforce(self, skill_id: str, success: bool):
        repo = self._get_repo()
        skill = repo.get(skill_id)
        if skill:
            if success:
                skill.success_count += 1
            else:
                skill.failure_count += 1
            skill.confidence = self.bayesian_confidence(skill.success_count, skill.failure_count)
            skill.updated_at = datetime.utcnow().isoformat()
            repo.upsert(skill, self.embed(skill.pattern))
            print(f"[memory] Reinforced skill {skill_id}: success={success} confidence={skill.confidence}")

    def find_similar(self, pattern: str, top_k: int = 3):
        repo = self._get_repo()
        emb = self.embed(pattern)
        return repo.search(emb, top_k)

    def select_best(self, pattern: str):
        repo = self._get_repo()
        emb = self.embed(pattern)
        results = repo.search(emb, top_k=5)

        if not results:
            return None

        scored = []
        for skill, sim in results:
            score = (skill.confidence * 0.6) + (sim * 0.4)
            scored.append((skill, score))

        best_skill, best_score = max(scored, key=lambda x: x[1])

        if best_score < 0.72:
            return None

        return best_skill, best_score
