from organism_console.memory.skill_journal import SkillJournal
from organism_console.skills.skill_repository import SkillRepository
import json

def rebuild_from_journal():
    repo = SkillRepository()
    journal = SkillJournal()

    path = journal.path

    if not path.exists():
        print("[rebuild] No journal found")
        return

    count = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)

                skill = type("Skill", (), {})()
                skill.id = data["id"]
                skill.pattern = data["pattern"]
                skill.confidence = data.get("confidence", 0.5)
                skill.success_count = data.get("success", 0)
                skill.failure_count = data.get("failure", 0)
                skill.created_at = data.get("ts", "")
                skill.updated_at = data.get("ts", "")

                repo.upsert(skill, repo.embed(skill.pattern))
                count += 1

            except Exception as e:
                print("[rebuild error]", e)

    print(f"[rebuild] restored {count} skills")

if __name__ == "__main__":
    rebuild_from_journal()
