import json
from datetime import datetime

class SkillJournal:
    """
    Immutable learning log.
    Never overwritten. Never reset.
    """

    def __init__(self, path="organism_console/memory/skill_journal.jsonl"):
        self.path = path

    def log(self, record: dict):
        record["ts"] = datetime.utcnow().isoformat()

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
