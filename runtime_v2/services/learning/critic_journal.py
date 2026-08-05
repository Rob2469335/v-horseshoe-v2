import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class CriticJournal:
    def __init__(self, path="runtime_v2/services/learning/critic_journal.jsonl"):
        self.path = path

    def log(self, data: dict):
        data["ts"] = datetime.now(timezone.utc).isoformat()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")

    def load(self, limit: int = 200) -> list[dict]:
        """Read back recent journal entries so the critic can seed its weights
        from history (previously the journal was write-only — every restart reset
        the critic to defaults and the 'evolution' was lost)."""
        path = Path(self.path)
        if not path.exists():
            return []
        entries = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            log.warning("CriticJournal load failed: %s", exc)
            return []
        return entries[-limit:]
