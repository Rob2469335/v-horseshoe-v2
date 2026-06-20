from organism_console.skills.skill_repository import SkillRepository
from swarm_os.memory.intelligence.skill_memory_engine import SkillMemoryEngine
from datetime import datetime

class SelfRepairEngine:
    def __init__(self):
        self.memory = SkillMemoryEngine()
        self.repo = self.memory._get_repo()
        self.repair_log = []

    def handle_failure(self, task: str, skill_id: str, result: str):
        skill = self.repo.get(skill_id)

        if not skill:
            print("[repair] skill not found")
            return

        # STEP 1: DECAY CONFIDENCE
        old_conf = skill.confidence
        skill.failure_count += 1
        skill.confidence *= 0.85  # decay factor

        # STEP 2: DETECT TYPE OF FAILURE
        failure_type = self._classify_failure(task, result)

        # STEP 3: APPLY REPAIR STRATEGY
        if failure_type == "pattern_mismatch":
            skill.pattern = self._generalize(skill.pattern)

        elif failure_type == "overfitting":
            skill.confidence *= 0.9

        elif failure_type == "stale_behavior":
            skill.success_count = max(1, skill.success_count // 2)

        # STEP 4: UPDATE TIMESTAMP
        skill.updated_at = datetime.utcnow().isoformat()

        # STEP 5: SAVE BACK
        self.repo.upsert(skill, self.memory.embed(skill.pattern))

        self.repair_log.append({
            "skill_id": skill_id,
            "old_conf": old_conf,
            "new_conf": skill.confidence,
            "failure_type": failure_type
        })

        print(f"[repair] skill={skill_id}")
        print(f"[repair] type={failure_type}")
        print(f"[repair] confidence {old_conf:.3f} → {skill.confidence:.3f}")

    def _classify_failure(self, task, result):
        t = task.lower()
        r = str(result).lower()

        if "import error" in t and "resolved" not in r:
            return "pattern_mismatch"

        if "syntax" in t and "fix" not in r:
            return "overfitting"

        return "stale_behavior"

    def _generalize(self, pattern):
        return pattern.replace("module", "X")
