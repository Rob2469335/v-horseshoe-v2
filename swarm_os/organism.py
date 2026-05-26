# swarm_os/kernel/organism.py
"""
Organism — individual agent in the swarm.
Fixed: act() no longer passes genome.genes or genome.architecture
       (both removed from new Genome dataclass).
Context now passes genome.to_dict() for full transparency.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict

from .genetics import Genome
from swarm_os.config.settings import settings

log = logging.getLogger(__name__)
LOG_PATH = settings.log_path


class MemoryBank:
    """Per-organism persistent JSONL diary."""
    def __init__(self, org_id: str):
        self.org_id = org_id
        self.events: list = []

    def write(self, event: Dict[str, Any]) -> None:
        record = {"ts": time.time(), "org": self.org_id, **event}
        self.events.append(record)
        Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
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
    def __init__(
        self,
        id: str,
        brain: Callable[[Dict[str, Any]], Dict[str, Any]],
        genome: Genome,
    ):
        self.id      = id
        self.brain   = brain
        self.genome  = genome
        self.fitness: float = 0.0
        self.memory  = MemoryBank(id)
        self._action_count = 0

    def act(self, env_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the brain with current environment state.
        FIXED: passes genome.to_dict() instead of removed
               genome.genes and genome.architecture fields.
        """
        context = {
            "id":           self.id,
            "genome":       self.genome.to_dict(),   # full genome snapshot
            "active_tools": self.genome.active_tools(),
            "env":          env_state,
            "task":         env_state.get("task", ""),
            "action_count": self._action_count,
        }

        try:
            action = self.brain(context) or {}
        except Exception as e:
            log.exception("organism act failed id=%s", self.id)
            action = {"error": str(e), "cost": 5.0, "content": ""}

        self._action_count += 1

        self.memory.write({
            "event":          "action",
            "action_count":   self._action_count,
            "task":           env_state.get("task", "")[:120],
            "model":          action.get("model", self.genome.model),
            "tools_used":     action.get("tools_used", []),
            "elapsed":        action.get("elapsed", 0),
            "total_tokens":   action.get("total_tokens", 0),
            "content_preview":action.get("content", "")[:200],
            "error":          action.get("error"),
            "avg_fitness":    round(self.genome.average_fitness, 4),
        })

        return action

    def __repr__(self) -> str:
        return (
            f"Organism(id={self.id!r}, fitness={self.fitness:.3f}, "
            f"avg={self.genome.average_fitness:.3f}, "
            f"model={self.genome.model!r}, gen={self.genome.generation})"
        )
