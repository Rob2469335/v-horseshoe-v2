from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict

from .genetics import Genome
from swarm_os.config.settings import settings

log = logging.getLogger(__name__)
LOG_PATH = settings.log_path


def _read_field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class MemoryBank:
    """Per-organism persistent JSONL diary (bounded in memory)."""

    def __init__(self, org_id: str):
        self.org_id = org_id
        self.events: deque = deque(maxlen=1000)

    def write(self, event: Dict[str, Any]) -> None:
        record = {"ts": time.time(), "org": self.org_id, **event}
        self.events.append(record)
        Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            log.debug("diary write failed org=%s", self.org_id)
        log.debug("memory write org=%s event=%s", self.org_id, event.get("event", "?"))

    def recent(self, n: int = 20) -> list:
        return list(self.events)[-n:]

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
        self.id = id
        self.brain = brain
        self.genome = genome
        self.fitness: float = 0.0
        self.memory = MemoryBank(id)
        self._action_count = 0

    def act(self, env_state: Dict[str, Any]) -> Dict[str, Any]:
        genome_data = self.genome.to_dict()
        context = {
            "id": self.id,
            "genome": genome_data,
            "active_tools": self.genome.active_tools(),
            "env": env_state,
            "task": env_state.get("task", ""),
            "action_count": self._action_count,
        }

        try:
            raw_action = self.brain(context)
        except Exception as exc:
            log.exception("organism act failed id=%s", self.id)
            raw_action = {"error": str(exc), "cost": 5.0, "content": ""}

        action = raw_action or {}
        self._action_count += 1

        tools_used = _read_field(action, "tools_used", [])
        error = _read_field(action, "error")

        # Normalize content — a brain may return a dict/object payload (e.g. an
        # error shape from a downed backend). Coerce defensively so a single bad
        # organism can never crash the whole generation's `gather`.
        content = _read_field(action, "content", "")
        if not isinstance(content, str):
            content = (
                json.dumps(content, ensure_ascii=False, default=str) if content else ""
            )

        try:
            self.memory.write(
                {
                    "event": "action",
                    "action_count": self._action_count,
                    "task": env_state.get("task", "")[:120],
                    "model": _read_field(action, "model", genome_data.get("model", "")),
                    "tools_used": tools_used,
                    "elapsed": _read_field(action, "elapsed", 0),
                    "total_tokens": _read_field(action, "total_tokens", 0),
                    "content_preview": content[:200],
                    "error": error,
                    "avg_fitness": round(self.genome.average_fitness, 4),
                }
            )
        except Exception as e:
            log.debug("memory write failed org=%s: %s", self.id, e)

        # Post-processed action — keep error+failure semantics intact so the
        # evaluator scores this organism accordingly.
        if isinstance(action, dict):
            out = dict(action)
        else:
            out = {
                "model": _read_field(action, "model", genome_data.get("model", "")),
                "tools_used": _read_field(action, "tools_used", []),
                "elapsed": _read_field(action, "elapsed", 0),
                "total_tokens": _read_field(action, "total_tokens", 0),
                "content": content,
                "error": error,
                "cost": _read_field(action, "cost", 0.0),
                "finish_reason": _read_field(action, "finish_reason", ""),
            }
        out["content"] = content

        # Digital Pheromones: Update tool weights based on execution success.
        # Best-effort fire-and-forget with a short timeout — `act()` runs on an
        # executor thread, so a live-service hang here would stall the whole
        # kernel's `gather`. Failures/slow calls are logged at debug and skipped.
        if tools_used:
            try:
                from swarm_os.services.tool_registry import get_tool_registry

                registry = get_tool_registry()
                success = not bool(error)

                def _flush() -> None:
                    for tool in tools_used:
                        asyncio.run(
                            registry.update_tool_pheromone(tool, success=success)
                        )

                async def _flush_with_timeout() -> None:
                    async with asyncio.timeout(2.0):
                        await asyncio.to_thread(_flush)

                asyncio.run(_flush_with_timeout())
            except TimeoutError:
                log.debug("pheromone update timed out org=%s", self.id)
            except Exception as e:
                log.debug("Pheromone update failed: %s", e)

        return out

    def __repr__(self) -> str:
        return (
            f"Organism(id={self.id!r}, fitness={self.fitness:.3f}, "
            f"avg={self.genome.average_fitness:.3f}, "
            f"model={self.genome.model!r}, gen={self.genome.generation})"
        )
