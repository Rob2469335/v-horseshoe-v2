from __future__ import annotations

import asyncio
import logging
from typing import Any

from swarm_os.services.orchestrator import Orchestrator

log = logging.getLogger(__name__)

class SwarmWorker:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.orch = orchestrator
        self.is_running = False

    def _read_field(self, obj: Any, name: str, default: Any = None) -> Any:
        if hasattr(obj, name):
            return getattr(obj, name)
        if isinstance(obj, dict):
            return obj.get(name, default)
        return default

    async def run_loop(self) -> None:
        self.is_running = True
        log.info("SwarmWorker: The Swarm heart is beating and the brain is active...")

        while self.is_running:
            try:
                await self.orch.evolve()
                log.info("SwarmWorker: Agentic brain is processing...")

                step_result = await self.orch.run_agent_step()

                route = self._read_field(step_result, "route", {}) or {}
                route_action = self._read_field(route, "action")
                route_target = self._read_field(route, "target")

                log.info(
                    "SwarmWorker: step status=%s model=%s route_action=%s route_target=%s",
                    self._read_field(step_result, "status"),
                    self._read_field(step_result, "model"),
                    route_action,
                    route_target,
                )

                await asyncio.sleep(10)
            except Exception:
                log.exception("SwarmWorker: Error in execution loop")
                await asyncio.sleep(5)
