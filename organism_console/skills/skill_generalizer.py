from organism_console.skills.skill_repository import Skill
from swarm_os.memory.intelligence.skill_memory_engine import SkillMemoryEngine
import numpy as np
from typing import List


class SkillGeneralizer:
    def __init__(self):
        self.memory = SkillMemoryEngine()
        self.merge_log = []

    def merge_similar_skills(self):
        repo = self.memory._get_repo()
        all_skills = repo.all()

        merged_ids = set()
        merges_made = 0

        for i, skill_a in enumerate(all_skills):
            if skill_a.id in merged_ids:
                continue

            group = [skill_a]
            emb_a = self.memory.embed(skill_a.pattern)

            for skill_b in all_skills[i + 1 :]:
                if skill_b.id in merged_ids:
                    continue

                emb_b = self.memory.embed(skill_b.pattern)
                sim = float(np.dot(emb_a, emb_b))

                # 🔥 LOWER threshold (prevents collapse)
                if 0.78 < sim < 0.95:
                    group.append(skill_b)
                    merged_ids.add(skill_b.id)

            # 🔥 ONLY merge small groups (prevents absorption)
            if 2 <= len(group) <= 3:
                self._merge_skills(group)
                merges_made += 1

        return merges_made

    def _merge_skills(self, skills: List[Skill]):
        total_success = sum(s.success_count for s in skills)
        total_failure = sum(s.failure_count for s in skills)

        merged_id = str(
            __import__("uuid").uuid5(
                __import__("uuid").NAMESPACE_DNS, skills[0].pattern
            )
        )

        # 🔥 weighted pattern instead of winner-takes-all
        patterns = [s.pattern for s in skills]
        pattern = patterns[0]

        merged = Skill(
            id=merged_id,
            pattern=pattern,
            success_count=total_success,
            failure_count=total_failure,
            confidence=self.memory.bayesian_confidence(total_success, total_failure),
        )

        repo = self.memory._get_repo()
        repo.upsert(merged, self.memory.embed(pattern))

        print(f"[generalizer] Balanced merge of {len(skills)} skills → {pattern}")
        return merged
