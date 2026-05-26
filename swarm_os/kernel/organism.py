from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Dict
import json
import logging
import os
import time

from .genetics import Genome
from swarm_os.config.settings import settings

log = logging.getLogger(__name__)
LOG_PATH = settings.log_path

class MemoryBank:
    def __init__(self, org_id: str):
        self.org_id = org_id
        self.events: list = []

    def write(self, event: Dict[str, Any]) -> None:
        record = {"ts": time.time(), "org": self.org_id, **event}
        self.events.append(record)
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        log.debug("memory write org=%s event=%s", self.org_id, event.get("event", "?"))

    def recent(self, n: int = 20) -> list:
        return self.events[-n:]

    def last_content(self) -> str:
        for e in reversed(self.events):
            if "content" in e:
                return e["content"]
        return ""

class Organism:
    def __init__(self, id: str, brain: Callable[[Dict[str, Any]], Dict[str, Any]], genome: Genome):
        self.id = id
        self.brain = brain
        self.genome = genome
        self.fitness: float = 0.0
        self.memory = MemoryBank(id)
        self._action_count = 0

    def act(self, env_state: Dict[str, Any]) -> Dict[str, Any]:
        genome_data = asdict(self.genome)
        context = {
            "id": self.id,
            "genome": genome_data,
            "active_tools": genome_data.get("tool_genes", {}),
            "env": env_state,
            "task": env_state.get("task", ""),
            "action_count": self._action_count,
        }
        try:
            action = self.brain(context) or {}
        except Exception as e:
            log.exception("organism act failed id=%s", self.id)
            action = {"error": str(e), "cost": 5.0, "content": ""}
        self._action_count += 1
        self.memory.write({
            "event": "action",
            "action_count": self._action_count,
            "task": env_state.get("task", "")[:120],
            "model": action.get("model", genome_data.get("model", "")),
            "tools_used": action.get("tools_used", []),
            "elapsed": action.get("elapsed", 0),
            "total_tokens": action.get("total_tokens", 0),
            "content_preview": action.get("content", "")[:200],
            "error": action.get("error"),
            "avg_fitness": round(genome_data.get("lifetime_fitness", 0.0) / max(genome_data.get("evaluations", 1), 1), 4),
        })
        return action
