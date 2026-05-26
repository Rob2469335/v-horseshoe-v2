# core/runtime/task_queue.py
"""
Blueprint loader and task queue.
Scans blueprints/ directory and registers each manifest.
"""
import json
import logging
from pathlib import Path
from config.settings import BLUEPRINT_DIR

log = logging.getLogger("task_queue")
_blueprints: dict[str, dict] = {}


async def load_blueprints() -> None:
    """Scan blueprints/ and load all manifest.json files."""
    global _blueprints
    _blueprints = {}
    for manifest_path in BLUEPRINT_DIR.glob("*/manifest.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            key = manifest_path.parent.name
            _blueprints[key] = {**data, "_path": str(manifest_path.parent)}
            log.info(f"Loaded blueprint: {key}")
        except Exception as e:
            log.warning(f"Failed to load blueprint at {manifest_path}: {e}")
    log.info(f"{len(_blueprints)} blueprints loaded")


def get_blueprints() -> dict[str, dict]:
    return _blueprints
