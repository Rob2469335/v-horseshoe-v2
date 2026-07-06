import json
from datetime import datetime

class CriticJournal:
    def __init__(self, path="runtime_v2/services/learning/critic_journal.jsonl"):
        self.path = path

    def log(self, data: dict):
        data["ts"] = datetime.utcnow().isoformat()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
