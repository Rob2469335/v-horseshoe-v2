import asyncio
import logging
from swarm_os.services.orchestrator import Orchestrator

log = logging.getLogger(__name__)

class SwarmWorker:
    def __init__(self, orchestrator: Orchestrator):
        self.orch = orchestrator
        self.is_running = False

    async def run_loop(self):
        self.is_running = True
        log.info("SwarmWorker: The Swarm heart is beating and the brain is active...")

        while self.is_running:
            try:
                await self.orch.evolve()
                log.info("SwarmWorker: Agentic brain is processing...")
                await self.orch.run_agent_step()
                await asyncio.sleep(10)
            except Exception as e:
                log.error(f"SwarmWorker: Error in execution loop: {e}")
                await asyncio.sleep(5)
