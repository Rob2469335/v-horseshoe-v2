from __future__ import annotations
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from swarm_os.apps.simulation_runner import main

LOG_DIR = Path("swarm_os/logs")
TEXT_LOG = LOG_DIR / "swarm.log"
JSON_LOG = LOG_DIR / "swarm.jsonl"

class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:
        root.handlers.clear()

    text_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(text_fmt)
    console.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(TEXT_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(text_fmt)
    file_handler.setLevel(logging.INFO)

    json_handler = RotatingFileHandler(JSON_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    json_handler.setFormatter(JsonLineFormatter())
    json_handler.setLevel(logging.INFO)

    root.addHandler(console)
    root.addHandler(file_handler)
    root.addHandler(json_handler)

if __name__ == "__main__":
    setup_logging()
    logging.getLogger(__name__).info("Starting swarm OS")
    main()
